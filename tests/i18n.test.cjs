const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const modulePath = path.join(root, 'app/static/i18n.js');
const appSource = fs.readFileSync(path.join(root, 'app/static/app.js'), 'utf8').split('$("#home-icon").innerHTML')[0];

function harness({saved, languages=['en-GB'], blocked=false, missingKey, delaySpanish=false, thirdLocale=false, unavailableSpanish=false}={}) {
  const storage = new Map(saved ? [['finanza.language',saved]] : []);
  let release;
  const fetch = async url => {
    if (url.endsWith('/es.json') && unavailableSpanish) return {ok:false};
    const data=JSON.parse(fs.readFileSync(path.join(root,'app',url.endsWith('/fr.json') ? '/static/locales/en.json' : url),'utf8'));
    if (url.endsWith('/manifest.json') && thirdLocale) data.languages.push({code:'fr',name:'Français',locale:'fr-FR',dir:'ltr'});
    if (url.endsWith('/fr.json')) data['login.welcome']='Bienvenue.';
    if (url.endsWith('/es.json') && missingKey) delete data[missingKey];
    if (url.endsWith('/es.json') && delaySpanish) await new Promise(resolve=>{release=resolve;});
    return {ok:true,json:async()=>data};
  };
  const context={module:{exports:{}},fetch,navigator:{languages},localStorage:{
    getItem:key=>{if(blocked)throw new Error('disabled');return storage.get(key);},
    setItem:(key,value)=>{if(blocked)throw new Error('disabled');storage.set(key,value);}
  },Intl,Map,console};
  vm.runInNewContext(fs.readFileSync(modulePath,'utf8'),context);
  return {i18n:context.module.exports,storage,release:()=>release?.()};
}

test('browser detection, regional variants, saved preference and English fallback',async()=>{
  const a=harness({languages:['fr-FR','es-MX']});await a.i18n.init();assert.equal(a.i18n.language,'es');
  const b=harness({saved:'en',languages:['es-ES']});await b.i18n.init();assert.equal(b.i18n.language,'en');
  const c=harness({saved:'unknown',languages:['fr-FR']});await c.i18n.init();assert.equal(c.i18n.language,'en');
  assert.equal(c.i18n.match('../../secrets'),undefined);
});

test('switching persists the choice, handles plurals, and falls back per missing key',async()=>{
  const h=harness({missingKey:'common.emptyFoot'});await h.i18n.init();await h.i18n.setLanguage('es');
  assert.equal(h.storage.get('finanza.language'),'es');
  assert.equal(h.i18n.t('transactions.count',{count:1}),'1 movimiento');
  assert.equal(h.i18n.t('transactions.count',{count:2}),'2 movimientos');
  assert.equal(h.i18n.t('common.emptyFoot'),'Ready whenever you take the next step.');
  assert.equal(h.i18n.t('fields.custom',{},'Custom column'),'Custom column');
  assert.equal(h.i18n.t('errors.fxMissing',{source:'EUR',target:'SEK',day:'2026-10-21'}).includes('EUR/SEK'),true);
});

test('disabled browser storage does not prevent language changes',async()=>{
  const h=harness({blocked:true,languages:['es-ES']});await h.i18n.init();assert.equal(h.i18n.language,'es');
  await h.i18n.setLanguage('en');assert.equal(h.i18n.language,'en');
});

test('a slow earlier language request cannot overwrite the latest selection',async()=>{
  const h=harness({delaySpanish:true});await h.i18n.init();const old=h.i18n.setLanguage('es');
  await h.i18n.setLanguage('en');h.release();assert.equal(await old,false);assert.equal(h.i18n.language,'en');
});

test('dates, money, numbers and percentages use the active locale',async()=>{
  const h=harness();await h.i18n.init();
  function evaluate(expression){return vm.runInNewContext(appSource+'\n'+expression,{FinanzaI18n:h.i18n,Intl,Date,URLSearchParams,console});}
  const en=evaluate('JSON.stringify([fmt(1234.5,1),money(1234.5,"EUR",2),percentFmt(54.1),longMonth("2026-10")])');
  await h.i18n.setLanguage('es');
  const es=evaluate('JSON.stringify([fmt(1234.5,1),money(1234.5,"EUR",2),percentFmt(54.1),longMonth("2026-10")])');
  assert.notEqual(en,es);assert.equal(JSON.parse(en)[0],'1,234.5');assert.equal(JSON.parse(es)[0],'1.234,5');
  assert.match(JSON.parse(en)[3],/October/);assert.match(JSON.parse(es)[3],/octubre/);
  assert.equal(evaluate('noData("<script>alert(1)</script>","<img>").includes("&lt;script&gt;")'),true);
});


test('a manifest entry and catalog enable another language without app changes',async()=>{
  const h=harness({thirdLocale:true,languages:['fr-CA']});await h.i18n.init();
  assert.equal(h.i18n.language,'fr');assert.equal(h.i18n.locale,'fr-FR');
  assert.equal(h.i18n.t('login.welcome'),'Bienvenue.');
});


test('an unavailable language catalog falls back to English on live selection',async()=>{
  const h=harness({unavailableSpanish:true});await h.i18n.init();await h.i18n.setLanguage('es');
  assert.equal(h.i18n.language,'en');assert.equal(h.storage.get('finanza.language'),'en');
});

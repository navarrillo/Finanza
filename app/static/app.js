"use strict";
const t = (...args) => FinanzaI18n.t(...args);
const tx = (...args) => esc(t(...args));
const $ = (s) => document.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  home: "M3 10 12 3l9 7M5 9v11h5v-6h4v6h5V9",
  grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  chart: "M4 20V4M4 20h17M8 16v-5M13 16V7M18 16v-8",
  donut: "M12 3a9 9 0 1 0 9 9h-9V3ZM16 3.8V8h4.2A9 9 0 0 0 16 3.8Z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  wallet: "M20 8V5H5a2 2 0 0 0 0 4h16v11H5a2 2 0 0 1-2-2V7M17 12h4v4h-4z",
  people:
    "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M17 4a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  grow: "M3 17 9 11l4 4 8-10M15 5h6v6",
  lock: "M6 10h12v11H6zM8 10V6a4 4 0 0 1 8 0v4M12 14v3",
  calendar: "M4 5h16v16H4zM8 3v4M16 3v4M4 10h16",
  logout: "M9 4H4v16h5M13 8l4 4-4 4M8 12h13",
  in: "M12 3v14M6 11l6 6 6-6M4 21h16",
  out: "M12 17V3M6 9l6-6 6 6M4 21h16",
  leaf: "M19 3S6 1 4 10s5 13 11 7 4-14 4-14ZM5 20 16 7",
  info: "M12 16v-4M12 8h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  arrow: "M4 12h16M14 6l6 6-6 6",
  download: "M12 3v12M7 10l5 5 5-5M4 17v4h16v-4",
  budget: "M6 3h12v18H6zM9 7h6M9 11h6M9 15h2M14 15h1",
};
const icon = (n, cls = "") =>
  `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.6" aria-hidden="true"><path d="${paths[n] || paths.grid}"/></svg>`;
const pages = {
  resumen: "grid",
  evolucion: "chart",
  categorias: "donut",
  movimientos: "list",
  saldos: "wallet",
  comun: "people",
  inversiones: "grow",
};
let state = {
  page: "resumen",
  currency: "SEK",
  month: "",
  period: "month",
  data: null,
  txPage: 1,
  filters: {},
  categoryBank: "",
  includeOneOff: false,
};
let loadNumber = 0;
let txLoadNumber = 0;
// Bank codes stay stable in filters; display names come from the database.
const bankLabel = (code) =>
  state.data?.institutions?.find((institution) => institution.code === code)
    ?.name || code;
const bankInitials = (name) => {
  const words = String(name ?? "").match(/[\p{L}\p{N}]+/gu) || [];
  return (
    words
      .slice(0, 2)
      .map((word) => Array.from(word)[0])
      .join("")
      .toLocaleUpperCase(FinanzaI18n.locale) || "?"
  );
};
const colors = [
  "#2e644b",
  "#8aa975",
  "#b8c99c",
  "#d0d9bd",
  "#dfc99f",
  "#c19468",
  "#a5b5af",
  "#778d88",
];
const shortMonth = (m) =>
  new Date(m + "-01T12:00:00")
    .toLocaleDateString(FinanzaI18n.locale, { month: "short" })
    .replace(".", "");
const monthYear = (m, year = "numeric") =>
  new Date(m + "-01T12:00:00").toLocaleDateString(FinanzaI18n.locale, {
    month: "short",
    year,
  });
const longMonth = (m) =>
  new Date(m + "-01T12:00:00").toLocaleDateString(FinanzaI18n.locale, {
    month: "long",
    year: "numeric",
  });
const fmt = (n, digits = 0) =>
  n == null
    ? "—"
    : new Intl.NumberFormat(FinanzaI18n.locale, {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
        useGrouping: "always",
      }).format(n);
const moneyFormatter = (currency, digits) =>
  new Intl.NumberFormat(FinanzaI18n.locale, {
    style: "currency",
    currency,
    currencyDisplay: "narrowSymbol",
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
const money = (n, c = state.currency, digits = 0) =>
  n == null ? t("common.noData") : moneyFormatter(c, digits).format(n);
const percentFmt = (n, digits = 1) =>
  n == null
    ? "—"
    : new Intl.NumberFormat(FinanzaI18n.locale, {
        style: "percent",
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      }).format(n / 100);
function metricValue(n, percent = false) {
  if (n == null) return "—";
  const formatter = percent
    ? new Intl.NumberFormat(FinanzaI18n.locale, {
        style: "percent",
        maximumFractionDigits: 1,
        minimumFractionDigits: 1,
      })
    : moneyFormatter(state.currency, 0);
  return formatter
    .formatToParts(percent ? n / 100 : n)
    .map((part) =>
      ["currency", "percentSign"].includes(part.type)
        ? `<span class="unit">${esc(part.value)}</span>`
        : esc(part.value),
    )
    .join("");
}
const dateFmt = (d) =>
  d
    ? new Date(d.slice(0, 10) + "T12:00:00").toLocaleDateString(
        FinanzaI18n.locale,
        { day: "2-digit", month: "short", year: "numeric" },
      )
    : t("common.noData");
const noData = (
  title = t("common.emptyTitle"),
  text = t("common.emptyText"),
  i = "grow",
) =>
  `<div class="empty-state"><div class="empty-icon">${icon(i)}</div><h3>${esc(title)}</h3><p>${esc(text)}</p><small>${tx("common.emptyFoot")}</small></div>`;
let currentError = null;
let loginError = null;
let sessionTimer = null;
let dashboardFailed = false;
const errorMessage = (error) =>
  error?.error_key
    ? t(error.error_key, error.params || {})
    : t("errors.network");

async function api(url, options = {}) {
  const r = await fetch(url, {
    ...options,
    headers: { ...options.headers, "X-Finanza-Language": FinanzaI18n.language },
  });
  const data = await r.json();
  if (!r.ok) {
    if (r.status === 401 && !url.endsWith("/login")) showLogin();
    const error = new Error(data.detail);
    error.status = r.status;
    error.error_key = data.error_key || "errors.request";
    error.params = data.params || {};
    throw error;
  }
  if (url.startsWith("/api/") && !url.endsWith("/logout") && !url.startsWith("/api/auth/")) {
    clearTimeout(sessionTimer);
    // Clear visible financial data when server inactivity would expire the session.
    sessionTimer = setTimeout(showLogin, 15 * 60 * 1000);
  }
  return data;
}
function showLogin() {
  clearTimeout(sessionTimer);
  sessionTimer = null;
  ++loadNumber;
  state.data = null;
  $("#content").innerHTML = "";
  $("#password").value = "";
  $("#auth-code").value = "";
  $("#refresh").disabled = false;
  $("#login").hidden = false;
  $("#shell").hidden = true;
}
function showShell() {
  $("#login").hidden = true;
  $("#shell").hidden = false;
}
function setError(error) {
  currentError = error;
  $("#error").textContent = error ? errorMessage(error) : "";
  $("#error").hidden = !error;
}
async function load() {
  const requestId = ++loadNumber;
  $("#refresh").disabled = true;
  setError(null);
  try {
    const q = new URLSearchParams({ currency: state.currency });
    if (state.month) q.set("month", state.month);
    const data = await api("/api/dashboard?" + q);
    if (requestId !== loadNumber) return;
    state.data = data;
    state.month = data.month;
    dashboardFailed = false;
    updateChrome();
    document
      .querySelectorAll("[data-currency]")
      .forEach((b) =>
        b.classList.toggle("active", b.dataset.currency === state.currency),
      );
    render();
  } catch (e) {
    if (requestId === loadNumber) {
      dashboardFailed = true;
      setError(e);
      $("#content").innerHTML = noData(
        t("errors.display"),
        t("errors.displayText"),
        "info",
      );
    }
  } finally {
    if (requestId === loadNumber) $("#refresh").disabled = false;
  }
}
function periodRow() {
  return `<div class="period-row"><div class="segmented"><button data-period="month" class="${state.period === "month" ? "active" : ""}">${tx("period.month")}</button><button data-period="year" class="${state.period === "year" ? "active" : ""}">${tx("period.year")}</button></div><span class="period-note">${icon("lock")} ${tx("period.consolidated", { currency: state.currency })}</span></div>`;
}
function selectedMetrics() {
  return state.period === "month" ? state.data.current : state.data.totals;
}
function metricCard(label, key, i, highlight = false, percent = false) {
  const data = selectedMetrics(),
    v = data[key];
  const reference =
    state.period === "month"
      ? state.data.previous[key]
      : state.data.average[key];
  const comparable =
    state.period === "month"
      ? state.data.previous.has_data
      : state.data.observed_months > 0;
  let delta = "";
  if (
    state.period === "month" &&
    comparable &&
    reference != null &&
    v != null
  ) {
    const diff = percent
      ? v - reference
      : reference !== 0
        ? ((v - reference) / Math.abs(reference)) * 100
        : null;
    if (diff != null) {
      const good = key === "net_expenses" ? diff <= 0 : diff >= 0;
      delta = `<span class="delta ${good ? "" : "bad"}">${diff >= 0 ? "↗" : "↘"} ${percent ? tx("metrics.points", { value: fmt(Math.abs(diff), 1) }) : percentFmt(Math.abs(diff))}</span>${tx("metrics.previous")}`;
    }
  }
  if (!delta)
    delta =
      state.period === "month"
        ? tx("metrics.noComparison")
        : tx("metrics.average", { value: money(reference) });
  if (state.period === "year" && percent)
    delta = tx("metrics.monthsWithData", { count: state.data.observed_months });
  const avg = state.data.average[key];
  return `<article class="kpi ${highlight ? "highlight" : ""}"><div class="kpi-top"><span>${esc(label)}</span><span class="kpi-icon">${icon(i)}</span></div><div class="kpi-value">${metricValue(v, percent)}</div><div class="kpi-bottom">${delta}<br>${state.period === "month" ? tx("metrics.average", { value: percent ? percentFmt(avg) : money(avg) }) : `${esc(longMonth(state.data.series[0].month))} — ${esc(longMonth(state.month))}`}</div></article>`;
}
function kpis() {
  return `<div class="kpi-grid">${metricCard(t("metrics.income"), "income", "in")}${metricCard(t("metrics.netExpenses"), "net_expenses", "out")}${metricCard(t("metrics.savings"), "savings", "leaf", true)}${metricCard(t("metrics.savingsRate"), "savings_rate", "grow", false, true)}${metricCard(t("metrics.invested"), "invested", "wallet")}</div>`;
}
function chart(series, mode = "cash") {
  const w = 850,
    h = 260,
    l = 66,
    r = 20,
    top = 24,
    bottom = 39,
    cw = w - l - r,
    ch = h - top - bottom;
  const all = series.flatMap((d) =>
    mode === "oneoff"
      ? [d.one_off_expenses]
      : [d.income, d.net_expenses, d.savings],
  );
  let min = Math.min(0, ...all),
    max = Math.max(1, ...all);
  const pad = (max - min) * 0.12;
  max += pad;
  if (min < 0) min -= pad;
  const y = (v) => top + ((max - v) / (max - min)) * ch;
  const step = cw / series.length;
  const bw = Math.min(19, step * 0.26);
  const baseline = y(0);
  let s = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${tx(mode === "oneoff" ? "chart.oneOff" : "chart.cash")}"><title>${tx("chart.title", { title: t(mode === "oneoff" ? "chart.oneOffTitle" : "chart.evolution"), currency: state.currency })}</title>`;
  for (let k = 0; k <= 4; k++) {
    let v = min + ((max - min) * k) / 4;
    let yy = y(v);
    s += `<line x1="${l}" y1="${yy}" x2="${w - r}" y2="${yy}" stroke="#edf1ec" stroke-dasharray="3 4"/><text class="chart-label" x="${l - 10}" y="${yy + 3}" text-anchor="end">${new Intl.NumberFormat(FinanzaI18n.locale, { notation: "compact", maximumFractionDigits: 1 }).format(v)}</text>`;
  }
  s += `<text class="chart-label" x="${l}" y="11">${state.currency}</text>`;
  const points = [];
  series.forEach((d, i) => {
    const x = l + i * step + step / 2;
    if (mode === "oneoff") {
      const yy = y(d.one_off_expenses);
      s += `<rect x="${x - bw}" y="${Math.min(yy, baseline)}" width="${bw * 2}" height="${Math.max(0, Math.abs(yy - baseline))}" rx="3" fill="#cda56e"><title>${longMonth(d.month)} · ${tx("metrics.oneOff")}: ${money(d.one_off_expenses)}</title></rect>`;
    } else {
      [
        ["income", x - bw - 2, "#416f51"],
        ["net_expenses", x + 2, "#c1d4b1"],
      ].forEach(([key, xx, color]) => {
        const yy = y(d[key]);
        s += `<rect x="${xx}" y="${Math.min(yy, baseline)}" width="${bw}" height="${Math.max(0, Math.abs(yy - baseline))}" rx="3" fill="${color}"><title>${longMonth(d.month)} · ${key === "income" ? t("metrics.income") : t("metrics.netExpenses")}: ${money(d[key])}${d.has_data ? "" : " · " + t("common.noData")}</title></rect>`;
      });
      points.push(`${x},${y(d.savings)}`);
    }
    if (d.month === state.month)
      s += `<rect x="${x - 17}" y="${h - 25}" width="34" height="20" rx="5" fill="#edf3e9"/>`;
    s += `<text class="chart-label" x="${x}" y="${h - 11}" text-anchor="middle" fill="${d.month === state.month ? "#587947" : "#9ba59e"}">${shortMonth(d.month)}</text>`;
  });
  if (mode === "cash") {
    s += `<polyline points="${points.join(" ")}" fill="none" stroke="#9eb584" stroke-width="2" stroke-linejoin="round"/>`;
    series.forEach((d, i) => {
      s += `<circle cx="${l + i * step + step / 2}" cy="${y(d.savings)}" r="3" fill="#fff" stroke="#9eb584" stroke-width="1.5"><title>${longMonth(d.month)} · ${tx("metrics.savingsShort")}: ${money(d.savings)}</title></circle>`;
    });
  }
  return s + "</svg>";
}
function cashPanel() {
  return `<section class="panel"><div class="panel-head"><div><h2>${tx("chart.finances")}</h2><p class="panel-subtitle">${tx("period.year")} · ${esc(monthYear(state.data.series[0].month))} — ${esc(monthYear(state.month))}</p></div><button class="panel-action" data-go="evolucion">${tx("common.details")} ↗</button></div><div class="panel-body" style="padding-bottom:0"><div class="legend"><span><i style="background:#416f51"></i>${tx("metrics.income")}</span><span><i style="background:#c1d4b1"></i>${tx("metrics.netExpenses")}</span><span><i class="line" style="background:#9eb584"></i>${tx("metrics.savingsShort")}</span></div></div><div class="chart-wrap">${chart(state.data.series)}</div><div class="chart-callout">${icon("info")}<span>${tx("chart.netRule")} <strong>${tx("chart.oneOffSeparate")}</strong></span></div></section>`;
}
function categoryItems({
  bank = state.categoryBank,
  includeOneOff = state.includeOneOff,
} = {}) {
  const map = new Map(),
    months =
      state.period === "year"
        ? state.data.series.map((s) => s.month)
        : [state.month];
  state.data.category_rows
    .filter((r) => !bank || r.bank === bank)
    .forEach((r) => {
      let sum = months.reduce(
        (n, m) =>
          n + (r.months[m] || 0) - (includeOneOff ? 0 : r.one_off[m] || 0),
        0,
      );
      map.set(r.name, (map.get(r.name) || 0) + sum);
    });
  return [...map]
    .map(([name, value]) => ({ name, value }))
    .filter((r) => r.value !== 0)
    .sort((a, b) => b.value - a.value);
}
function donut(items) {
  const positive = items.filter((d) => d.value > 0),
    total = positive.reduce((a, b) => a + b.value, 0),
    net = items.reduce((a, b) => a + b.value, 0),
    radius = 62,
    circ = 2 * Math.PI * radius;
  let offset = 0;
  const arcs = positive
    .map((d, i) => {
      const length = (d.value / total) * circ;
      const result = `<circle cx="80" cy="80" r="${radius}" fill="none" stroke="${colors[i % colors.length]}" stroke-width="17" stroke-dasharray="${Math.max(0, length - 2)} ${circ}" stroke-dashoffset="${-offset}"><title>${esc(d.name)}: ${money(d.value)}</title></circle>`;
      offset += length;
      return result;
    })
    .join("");
  return `<div class="donut-layout"><div class="donut"><svg viewBox="0 0 160 160" role="img" aria-label="${tx("categories.distribution")}"><circle cx="80" cy="80" r="62" stroke="#f0f3eb" fill="none" stroke-width="17"/>${arcs}</svg><div class="donut-center"><small>${tx("categories.total")}</small><strong>${fmt(net)}</strong><span>${state.currency === "SEK" ? t("categories.sek") : t("categories.eur")}</span></div></div><div class="donut-key">${positive
    .slice(0, 6)
    .map(
      (d, i) =>
        `<div><i style="background:${colors[i % colors.length]}"></i><span>${esc(d.name)}</span><b>${percentFmt((d.value / total) * 100, 0)}</b></div>`,
    )
    .join(
      "",
    )}</div></div>${items.some((d) => d.value < 0) ? `<p class="panel-subtitle">${tx("categories.negative")}</p>` : ""}`;
}
function categoryPanel() {
  const items = categoryItems({ bank: "", includeOneOff: false });
  return `<section class="panel"><div class="panel-head"><div><h2>${tx("categories.where")}</h2><p class="panel-subtitle">${state.period === "month" ? longMonth(state.month) : t("period.year")} · ${tx("metrics.netExpenses")}</p></div><button class="panel-action" data-go="categorias" aria-label="${tx("common.details")}">↗</button></div><div class="panel-body">${items.length ? donut(items) : noData(t("categories.noSpending"), "", "donut")}<div class="category-list">${items
    .slice(0, 3)
    .map(
      (d, i) =>
        `<div class="category-progress"><span class="cat-icon">${["⌂", "◈", "↗"][i]}</span><span class="cat-name">${esc(d.name)}</span><span class="progress"><span style="width:${Math.min(100, (d.value / Math.max(1, items[0].value)) * 100)}%;background:${colors[i]}"></span></span><strong>${money(d.value)}</strong></div>`,
    )
    .join("")}</div></div></section>`;
}
function accountCards(joint = false) {
  return `<div class="account-grid">${state.data.accounts
    .filter((a) => Boolean(a.is_joint) === joint)
    .map(
      (a) =>
        `<article class="account-card"><span class="bank-logo" aria-hidden="true">${esc(bankInitials(a.bank))}</span><div class="account-info"><strong>${esc(a.bank)}</strong><small>${esc(a.name)} · ${esc(a.currency)}</small></div><div class="account-amount"><strong>${money(a.balance, a.currency)}</strong><small>${a.as_of ? dateFmt(a.as_of) : t("common.pending")}</small></div></article>`,
    )
    .join("")}</div>`;
}
function transactionTable(data, compact = false, joint = false) {
  const rows = data.rows || [];
  return `<div class="table-wrap"><table class="data-table"><thead><tr><th>${tx("common.date")}</th><th>${tx("common.description")}</th><th>${tx("common.category")}</th><th class="hide-mobile">${tx("common.bank")}</th><th class="right">${tx("common.amount")}</th>${compact ? "" : `<th class="right">${tx("common.consolidated")}</th>`}</tr></thead><tbody>${rows.map((row) => `<tr class="${row.category === "Otros" ? "unclassified" : ""}"><td class="month-cell">${esc(dateFmt(row.booking_date))}</td><td><strong>${esc(row.description)}</strong>${row.split_part ? `<span class="split-badge">${tx("transactions.part", { part: row.split_part })}</span>` : ""}<small>${esc(row.counterparty || row.account_name || "")}${row.is_internal_transfer ? " · " + tx("transactions.internal") : ""}${row.is_one_off ? " · " + tx("transactions.oneOff") : ""}${row.is_pass_through ? " · " + tx("transactions.passThrough") : ""}</small></td><td><span class="category-tag ${row.category === "Otros" ? "other" : ""}">${row.category === "Otros" ? "◉ " : ""}${esc(row.category || "Otros")}</span></td><td class="hide-mobile">${esc(bankLabel(row.bank) || "")}</td><td class="right amount ${row.amount > 0 ? "positive" : ""}">${row.amount > 0 ? "+" : ""}${esc(money(row.amount, row.currency, 2))}</td>${compact ? "" : `<td class="right">${esc(money(row.converted_amount, state.currency, 2))}</td>`}</tr>`).join("") || `<tr><td colspan="6">${tx("transactions.empty")}</td></tr>`}</tbody></table></div>${compact ? "" : `<div class="table-footer"><span>${tx("transactions.count", { count: data.total_transactions ?? new Set(rows.map((row) => row.id)).size })} · ${tx("transactions.lines", { count: data.total_lines ?? rows.length })} · ${tx(joint ? "transactions.jointView" : "transactions.splitRule")}</span>${joint ? "" : `<div class="pager"><button id="prev-page" ${state.txPage === 1 ? "disabled" : ""} aria-label="${tx("transactions.previous")}">←</button><span>${fmt(state.txPage)} / ${fmt(Math.max(1, Math.ceil(data.total_lines / 25)))}</span><button id="next-page" ${state.txPage * 25 >= data.total_lines ? "disabled" : ""} aria-label="${tx("transactions.next")}">→</button></div>`}</div>`}`;
}
function overview() {
  const metrics = selectedMetrics();
  return `${periodRow()}${kpis()}<div class="grid-main">${cashPanel()}${categoryPanel()}</div><div class="section-title"><h2>${tx("overview.accounts")}</h2><button class="panel-action" data-go="saldos">${tx("overview.balances")} ${icon("arrow")}</button></div>${accountCards()}<section class="panel"><div class="panel-head" style="padding-bottom:18px"><div><h2>${tx("overview.recent")}</h2><p class="panel-subtitle">${tx("overview.recentText")}</p></div><button class="panel-action" data-go="movimientos">${tx("common.allRows")} ↗</button></div><div id="recent-table" class="loading">${tx("common.loadingTransactions")}</div></section><p class="panel-subtitle" style="line-height:1.8;margin-top:14px">${tx("overview.summary", { count: state.data.observed_months, regular: money(metrics.regular_expenses), oneOff: money(metrics.one_off_expenses), assets: money(metrics.to_assets), savings: money(metrics.savings_after_one_off) })}</p>`;
}
function evolution() {
  return `${periodRow()}${kpis()}${cashPanel()}<section class="panel subsection"><div class="panel-head"><div><h2>${tx("chart.oneOffTitle")}</h2><p class="panel-subtitle">${tx("evolution.oneOffText")}</p></div><span class="category-tag">${tx("evolution.yearAmount", { amount: money(state.data.totals.one_off_expenses) })}</span></div><div class="chart-wrap">${chart(state.data.series, "oneoff")}</div></section><section class="panel subsection"><div class="panel-head" style="padding-bottom:18px"><h2>${tx("evolution.byMonth")}</h2><span class="panel-subtitle">${tx("evolution.averageNote")}</span></div><div class="table-wrap"><table class="data-table"><thead><tr><th>${tx("common.month")}</th><th class="right">${tx("metrics.income")}</th><th class="right">${tx("metrics.netExpenses")}</th><th class="right">${tx("metrics.oneOff")}</th><th class="right">${tx("metrics.withOneOff")}</th><th class="right">${tx("metrics.savingsShort")}</th><th class="right">${tx("metrics.invested")}</th><th class="right">${tx("metrics.assets")}</th></tr></thead><tbody>${[
    ...state.data.series,
  ]
    .reverse()
    .map(
      (s) =>
        `<tr><td>${longMonth(s.month)}${s.has_data ? "" : " · " + t("common.noData")}</td>${["income", "net_expenses", "one_off_expenses", "total_net_expenses", "savings", "invested", "to_assets"].map((k) => `<td class="right">${s.has_data ? money(s[k]) : "—"}</td>`).join("")}</tr>`,
    )
    .join("")}</tbody></table></div></section>`;
}
function categories() {
  const months =
    state.period === "month"
      ? [state.month]
      : state.data.series.map((s) => s.month);
  const items = categoryItems();
  const rows = state.data.category_rows
    .filter((r) => !state.categoryBank || r.bank === state.categoryBank)
    .map((r) => ({
      ...r,
      total: months.reduce(
        (s, m) =>
          s +
          (r.months[m] || 0) -
          (state.includeOneOff ? 0 : r.one_off[m] || 0),
        0,
      ),
    }))
    .filter((r) => r.total !== 0)
    .sort((a, b) => b.total - a.total);
  return `${periodRow()}<section class="panel"><div class="filter-bar"><label>${tx("common.bank")}<select id="category-bank"><option value="">${tx("categories.allBanks")}</option>${state.data.banks.map((b) => `<option value="${esc(b)}" ${b === state.categoryBank ? "selected" : ""}>${esc(bankLabel(b))}</option>`).join("")}</select></label><label class="check-label" style="flex-direction:row;align-items:center;min-height:35px"><input id="include-oneoff" type="checkbox" style="width:14px;height:14px" ${state.includeOneOff ? "checked" : ""}> ${tx("categories.includeOneOff")}</label></div></section><div class="grid-main subsection"><section class="panel"><div class="panel-head"><h2>${tx("categories.byCategory")}</h2></div><div class="panel-body">${items.length ? donut(items) : noData(t("categories.noExpenses"), "", "donut")}</div></section><section class="panel"><div class="panel-head"><h2>${tx("categories.perspective")}</h2></div><div class="panel-body"><div class="stat-inline"><div>${tx("metrics.netExpenses")}<strong>${money(items.reduce((a, b) => a + b.value, 0))}</strong></div><div>${tx("categories.count")}<strong>${items.length}</strong></div></div><p class="notice" style="margin:24px 0 0">${tx("categories.rule")}</p></div></section></div><section class="panel"><div class="panel-head" style="padding-bottom:20px"><h2>${tx("categories.subcategories")}</h2><span class="panel-subtitle">${tx("chart.evolution")} · ${state.currency}</span></div><div class="table-wrap"><table class="data-table"><thead><tr><th>${tx("categories.column")}</th><th>${tx("common.bank")}</th><th class="right">${tx("categories.totalPeriod")}</th>${state.data.series.map((s) => `<th class="right">${esc(monthYear(s.month, "2-digit").toLocaleUpperCase(FinanzaI18n.locale))}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr><td><strong>${esc(r.name)}</strong><small>${esc(r.category === r.name ? r.subcategory || t("categories.noSubcategory") : r.category + " · " + (r.subcategory || t("categories.noSubcategory")))}</small></td><td>${esc(bankLabel(r.bank))}</td><td class="right amount">${money(r.total)}</td>${state.data.series.map((s) => `<td class="right">${fmt((r.months[s.month] || 0) - (state.includeOneOff ? 0 : r.one_off[s.month] || 0))}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="15">${tx("common.noResults")}</td></tr>`}</tbody></table></div></section>`;
}
function transactionPage() {
  const f = state.filters;
  return `<section class="panel"><form class="filter-bar" id="tx-filters"><label class="wide">${tx("transactions.search")}<input name="search" type="search" placeholder="${tx("transactions.placeholder")}" value="${esc(f.search || "")}"></label><label>${tx("transactions.from")}<input name="start" type="date" value="${esc(f.start || "")}"></label><label>${tx("transactions.to")}<input name="end" type="date" value="${esc(f.end || "")}"></label><label>${tx("common.bank")}<select name="bank"><option value="">${tx("common.all")}</option>${state.data.banks.map((b) => `<option value="${esc(b)}" ${f.bank === b ? "selected" : ""}>${esc(bankLabel(b))}</option>`).join("")}</select></label><label>${tx("common.category")}<select name="category"><option value="">${tx("common.allCategories")}</option>${state.data.categories.map((c) => `<option value="${esc(c)}" ${f.category === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label><label>${tx("transactions.min")}<input name="min_amount" type="number" step="0.01" placeholder="−1000" value="${esc(f.min_amount ?? "")}"></label><label>${tx("transactions.max")}<input name="max_amount" type="number" step="0.01" placeholder="1000" value="${esc(f.max_amount ?? "")}"></label><button class="button primary" type="submit">${tx("transactions.apply")}</button><button class="button" type="button" id="clear-filters">${tx("transactions.clear")}</button></form><div class="notice" style="margin:0 22px 18px">${tx("transactions.rule")}</div><div class="panel-head" style="padding:0 22px 18px"><h2>${tx("transactions.personal")}</h2><a class="button" id="export" href="#">${icon("download")} ${tx("transactions.export")}</a></div><div id="tx-table" class="loading">${tx("common.loadingTransactions")}</div></section>`;
}
function balanceChart(a) {
  if (!a.points.length)
    return noData(t("balances.empty"), t("balances.emptyText"), "wallet");
  const points = a.points,
    w = 850,
    h = 200,
    l = 70,
    r = 20,
    top = 20,
    bottom = 35;
  let min = Math.min(0, ...points.map((p) => p.balance)),
    max = Math.max(1, ...points.map((p) => p.balance));
  max += (max - min) * 0.1;
  const first = Date.parse(points[0].as_of),
    last = Date.parse(points.at(-1).as_of),
    x = (d) =>
      l + ((Date.parse(d) - first) / Math.max(1, last - first)) * (w - l - r),
    y = (v) => top + ((max - v) / (max - min)) * (h - top - bottom);
  const line = points.map((p) => `${x(p.as_of)},${y(p.balance)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${tx("balances.chart", { account: a.name, currency: a.currency })}"><title>${tx("balances.history", { account: a.name, currency: a.currency })}</title>${[
    0, 1, 2, 3,
  ]
    .map((i) => {
      const v = min + ((max - min) * i) / 3;
      return `<line x1="${l}" x2="${w - r}" y1="${y(v)}" y2="${y(v)}" stroke="#edf1ec" stroke-dasharray="3 4"/><text class="chart-label" x="${l - 10}" y="${y(v) + 3}" text-anchor="end">${fmt(v)}</text>`;
    })
    .join(
      "",
    )}<polygon points="${l},${y(min)} ${line} ${x(points.at(-1).as_of)},${y(min)}" fill="#f0f5eb"/><polyline points="${line}" fill="none" stroke="#799863" stroke-width="2"/>${points.length < 80 ? points.map((p) => `<circle cx="${x(p.as_of)}" cy="${y(p.balance)}" r="2" fill="#799863"><title>${dateFmt(p.as_of)} · ${money(p.balance, a.currency, 2)}</title></circle>`).join("") : ""}<text class="chart-label" x="${l}" y="${h - 10}">${dateFmt(points[0].as_of)}</text><text class="chart-label" x="${w - r}" y="${h - 10}" text-anchor="end">${dateFmt(points.at(-1).as_of)}</text></svg>`;
}
function balances() {
  const accounts = state.data.accounts.filter((a) => !a.is_joint),
    total = accounts.reduce((s, a) => s + (a.converted_balance || 0), 0);
  return `<div class="notice">${tx("balances.total", { amount: money(total) })}</div>${accountCards()}${accounts.map((a) => `<section class="panel balance-panel"><div class="panel-head"><div><h2>${esc(a.name)}</h2><p class="panel-subtitle">${esc(a.bank)} · ${esc(a.currency)} · ${tx("balances.priority")}</p></div><strong>${esc(money(a.balance, a.currency))}</strong></div><div class="chart-wrap">${balanceChart(a)}</div></section>`).join("")}`;
}
function jointPage() {
  const j = state.data.joint;
  return `<div class="notice"><strong>${tx("joint.title")}</strong> ${tx("joint.rule")}</div><div class="kpi-grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">${[
    [t("joint.income"), j.income],
    [t("joint.expenses"), j.expenses],
    [t("joint.mine"), j.contributions],
  ]
    .map(
      ([name, n]) =>
        `<article class="kpi"><div class="kpi-top">${esc(name)}<span class="kpi-icon">${icon("people")}</span></div><div class="kpi-value">${esc(money(n))}</div><div class="kpi-bottom">${esc(longMonth(state.month))}</div></article>`,
    )
    .join(
      "",
    )}</div>${accountCards(true)}<section class="panel"><div class="panel-head" style="padding-bottom:20px"><h2>${tx("joint.transactions")}</h2><span class="panel-subtitle">${tx("joint.until", { month: longMonth(state.month) })}</span></div>${transactionTable({ rows: j.transactions, total_lines: j.total_lines, total_transactions: j.total_transactions }, false, true)}${j.total_lines > 200 ? `<div class="chart-callout">${tx("joint.limit")}</div>` : ""}</section>`;
}
function optionalValue(row, column) {
  const value = row[column];
  if (value == null) return "—";
  if (column === "is_active") return t(value ? "common.yes" : "common.no");
  if (
    [
      "issue_date",
      "due_date",
      "document_date",
      "as_of",
      "first_seen",
      "last_seen",
    ].includes(column) &&
    /^\d{4}-\d{2}-\d{2}$/.test(value)
  )
    return dateFmt(value);
  if (typeof value === "number" && column !== "id" && !column.endsWith("_id")) {
    if (
      [
        "amount",
        "tax_amount",
        "typical_amount",
        "market_value",
        "cost_basis",
        "price",
      ].includes(column)
    )
      return row.currency ? money(value, row.currency, 2) : fmt(value, 2);
    return fmt(value, Number.isInteger(value) ? 0 : 6);
  }
  return value;
}
function optionalPanel(key, title, text, i) {
  const block = state.data.optional[key],
    columns = block.rows.length ? Object.keys(block.rows[0]) : [];
  return `<section class="panel subsection"><div class="panel-head"><div><h2>${esc(title)}</h2><p class="panel-subtitle">${esc(text)}</p></div><span class="category-tag">${block.count ? tx("optional.count", { count: block.count }) : tx("optional.soon")}</span></div>${block.count ? `<div class="table-wrap generic-table subsection"><table class="data-table"><thead><tr>${columns.map((c) => `<th>${tx("fields." + c, {}, c)}</th>`).join("")}</tr></thead><tbody>${block.rows.map((row) => `<tr>${columns.map((c) => `<td>${esc(optionalValue(row, c))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>${block.count > 200 ? `<div class="chart-callout">${tx("optional.limit")}</div>` : ""}` : noData(t("common.emptyTitle"), key === "budgets" ? t("optional.budgetsEmpty") : text, i)}</section>`;
}
function investments() {
  return `<div class="notice">${tx("investments.summary", { invested: money(state.data.current.invested), assets: money(state.data.current.to_assets) })}</div>${optionalPanel("holding_snapshots", t("investments.holdings"), t("investments.holdingsText"), "grow")}${optionalPanel("instruments", t("investments.instruments"), t("investments.instrumentsText"), "grow")}${optionalPanel("budgets", t("investments.budgets"), t("investments.budgetsText"), "budget")}${optionalPanel("recurring_series", t("investments.recurring"), t("investments.recurringText"), "calendar")}${optionalPanel("documents", t("investments.documents"), t("investments.documentsText"), "list")}`;
}
function render() {
  const pageKey = pages[state.page] ? state.page : "resumen";
  $("#crumb").textContent = t(`pages.${pageKey}.nav`);
  $("#page-title").innerHTML =
    tx(`pages.${pageKey}.title`) + '<span class="title-dot">.</span>';
  $("#page-description").textContent = t(`pages.${pageKey}.description`);
  $("#nav").innerHTML = Object.entries(pages)
    .map(
      ([key, pageIcon], i) =>
        `${i === 5 ? '<div class="nav-divider"></div>' : ""}<a class="nav-link ${state.page === key ? "active" : ""}" href="#${key}" ${state.page === key ? 'aria-current="page"' : ""}>${icon(pageIcon)}<span>${tx(`pages.${key}.nav`)}</span></a>`,
    )
    .join("");
  $("#content").innerHTML = (
    {
      resumen: overview,
      evolucion: evolution,
      categorias: categories,
      movimientos: transactionPage,
      saldos: balances,
      comun: jointPage,
      inversiones: investments,
    }[state.page] || overview
  )();
  document.querySelectorAll("[data-period]").forEach(
    (b) =>
      (b.onclick = () => {
        state.period = b.dataset.period;
        render();
      }),
  );
  document.querySelectorAll("[data-go]").forEach(
    (b) =>
      (b.onclick = () => {
        location.hash = b.dataset.go;
      }),
  );
  if (state.page === "resumen") loadRecent();
  if (state.page === "categorias") {
    $("#category-bank").onchange = (e) => {
      state.categoryBank = e.target.value;
      render();
    };
    $("#include-oneoff").onchange = (e) => {
      state.includeOneOff = e.target.checked;
      render();
    };
  }
  if (state.page === "movimientos") {
    $("#tx-filters").onsubmit = (e) => {
      e.preventDefault();
      state.filters = Object.fromEntries(new FormData(e.target));
      state.txPage = 1;
      loadTransactions();
    };
    $("#clear-filters").onclick = () => {
      state.filters = {};
      state.txPage = 1;
      render();
    };
    loadTransactions();
  }
}
function txQuery() {
  const q = new URLSearchParams({
    currency: state.currency,
    page: state.txPage,
    lang: FinanzaI18n.language,
  });
  for (const [k, v] of Object.entries(state.filters)) {
    if (v !== "" && v != null) q.set(k, v);
  }
  return q;
}
async function loadTransactions() {
  const n = ++txLoadNumber;
  const target = $("#tx-table");
  setError(null);
  try {
    const q = txQuery(),
      data = await api("/api/transactions?" + q);
    if (n !== txLoadNumber || !target?.isConnected) return;
    target.classList.remove("loading");
    target.innerHTML = transactionTable(data);
    q.delete("page");
    $("#export").href = "/api/export.csv?" + q;
    $("#prev-page").onclick = () => {
      state.txPage--;
      loadTransactions();
    };
    $("#next-page").onclick = () => {
      state.txPage++;
      loadTransactions();
    };
  } catch (e) {
    if (n === txLoadNumber) {
      setError(e);
      if (target?.isConnected)
        target.innerHTML = noData(
          t("errors.query"),
          t("errors.queryText"),
          "info",
        );
    }
  }
}
async function loadRecent() {
  const target = $("#recent-table");
  try {
    const end = new Date(
      Number(state.month.slice(0, 4)),
      Number(state.month.slice(5)),
      0,
    ).getDate();
    const q = new URLSearchParams({
      currency: state.currency,
      page_size: 5,
      start: state.month + "-01",
      end: state.month + "-" + end,
    });
    const data = await api("/api/transactions?" + q);
    if (target?.isConnected) {
      target.classList.remove("loading");
      target.innerHTML = transactionTable(data, true);
    }
  } catch (e) {
    if (target?.isConnected) {
      target.textContent = errorMessage(e);
    }
  }
}
$("#home-icon").innerHTML = icon("home");
$("#lock-icon").innerHTML = icon("lock");
$("#calendar-icon").innerHTML = icon("calendar");
$("#logout").innerHTML = icon("logout");
$("#login-form").onsubmit = async (e) => {
  e.preventDefault();
  loginError = null;
  $("#login-error").textContent = "";
  const button = e.target.querySelector("button");
  button.disabled = true;
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("#username").value,
        password: $("#password").value,
        code: $("#auth-code").value.trim(),
      }),
    });
    $("#password").value = "";
    showShell();
    await load();
  } catch (error) {
    loginError = error;
    $("#login-error").textContent = errorMessage(error);
  } finally {
    $("#password").value = "";
    $("#auth-code").value = "";
    button.disabled = false;
  }
};
$("#logout").onclick = async () => {
  try {
    await api("/api/logout", { method: "POST" });
    state.data = null;
    $("#content").innerHTML = "";
    showLogin();
  } catch (e) {
    setError(e);
  }
};
$("#month").onchange = (e) => {
  state.month = e.target.value;
  load();
};
document.querySelectorAll("[data-currency]").forEach(
  (b) =>
    (b.onclick = () => {
      if (state.currency !== b.dataset.currency) {
        state.currency = b.dataset.currency;
        load();
      }
    }),
);
$("#refresh").onclick = () => load();
$("#menu").onclick = () => $("#sidebar").classList.toggle("open");
window.addEventListener("hashchange", () => {
  state.page = pages[location.hash.slice(1)]
    ? location.hash.slice(1)
    : "resumen";
  $("#sidebar").classList.remove("open");
  if (state.data) render();
});
window.addEventListener("click", (e) => {
  if (
    $("#sidebar").classList.contains("open") &&
    !e.target.closest(".sidebar") &&
    !e.target.closest("#menu")
  )
    $("#sidebar").classList.remove("open");
});
function updateChrome() {
  $("#main .eyebrow").textContent = t(
    state.data?.demo ? "demo.eyebrow" : "app.eyebrow",
  );
  $("[data-private-label]").textContent = t(
    state.data?.demo ? "demo.label" : "app.private",
  );
  if (!state.data) return;
  const months = [...new Set([state.month, ...state.data.months])]
    .sort()
    .reverse();
  $("#month").innerHTML = months
    .map(
      (m) =>
        `<option value="${esc(m)}" ${m === state.month ? "selected" : ""}>${esc(longMonth(m))}</option>`,
    )
    .join("");
}
async function changeLanguage(code) {
  // Preserve filters typed but not yet applied, as well as active selections.
  const form = $("#tx-filters");
  if (form) state.filters = Object.fromEntries(new FormData(form));
  try {
    const changed = await FinanzaI18n.setLanguage(code);
    if (!changed) return;
    FinanzaI18n.apply();
    updateChrome();
    if (dashboardFailed)
      $("#content").innerHTML = noData(
        t("errors.display"),
        t("errors.displayText"),
        "info",
      );
    else if (state.data) render();
    setError(currentError);
    if (loginError) $("#login-error").textContent = errorMessage(loginError);
  } catch (error) {
    setError(error);
  }
}
document.querySelectorAll("[data-language]").forEach((select) => {
  select.onchange = () => changeLanguage(select.value);
});
(async () => {
  try {
    await FinanzaI18n.init();
    FinanzaI18n.apply();
    updateChrome();
    state.page = pages[location.hash.slice(1)]
      ? location.hash.slice(1)
      : "resumen";
    try {
      const policy = await api("/api/auth/config");
      $("#mfa-field").hidden = !policy.mfa_required;
      $("#mfa-help").hidden = !policy.mfa_required;
      $("#auth-code").required = policy.mfa_required;
      await api("/api/session");
      showShell();
      await load();
    } catch (error) {
      showLogin();
      if (error.status !== 401) {
        loginError = error;
        $("#login-error").textContent = errorMessage(error);
      }
    }
  } catch {
    // The entire app is local; missing locale assets indicate a broken deployment.
    document.body.textContent =
      "Unable to load language files. Check the local deployment. / No se pueden cargar los idiomas. Revisa el despliegue local.";
  }
})();

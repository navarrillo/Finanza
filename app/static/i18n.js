"use strict";
// One shared set of local JSON catalogs serves the browser and API errors.
const FinanzaI18n = (() => {
  const cache = new Map();
  let manifest = { default: "en", languages: [] };
  let language = "en";
  let requestNumber = 0;
  const preferenceKey = "finanza.language";
  const settings = () =>
    manifest.languages.find((item) => item.code === language) || {
      locale: "en-GB",
      dir: "ltr",
    };
  const match = (candidate) => {
    const value = String(candidate || "")
      .toLowerCase()
      .replaceAll("_", "-");
    return (
      manifest.languages.find((item) => item.code.toLowerCase() === value)
        ?.code ||
      manifest.languages.find(
        (item) => item.code.toLowerCase() === value.split("-")[0],
      )?.code
    );
  };
  async function catalog(code) {
    if (!cache.has(code)) {
      // Code comes only from the repository's manifest, never from an arbitrary URL.
      const response = await fetch(
        `/static/locales/${encodeURIComponent(code)}.json`,
      );
      if (!response.ok) throw new Error(`Locale unavailable: ${code}`);
      cache.set(code, await response.json());
    }
    return cache.get(code);
  }
  function t(key, params = {}, fallback) {
    let value =
      cache.get(language)?.[key] ??
      cache.get(manifest.default)?.[key] ??
      fallback ??
      key;
    if (value && typeof value === "object") {
      const plural = new Intl.PluralRules(settings().locale).select(
        Number(params.count || 0),
      );
      value = value[plural] ?? value.other;
    }
    return String(value).replace(/\{(\w+)\}/g, (whole, name) =>
      Object.hasOwn(params, name)
        ? typeof params[name] === "number"
          ? new Intl.NumberFormat(settings().locale).format(params[name])
          : String(params[name])
        : whole,
    );
  }
  async function setLanguage(code, persist = true) {
    const id = ++requestNumber;
    let chosen = match(code) || manifest.default;
    try {
      await catalog(chosen);
    } catch {
      chosen = manifest.default;
      await catalog(chosen);
    }
    if (id !== requestNumber) return false;
    language = chosen;
    if (persist) {
      try {
        localStorage.setItem(preferenceKey, chosen);
      } catch {
        /* Storage can be disabled. */
      }
    }
    return true;
  }
  async function init() {
    const response = await fetch("/static/locales/manifest.json");
    if (!response.ok) throw new Error("Locale manifest unavailable");
    manifest = await response.json();
    await catalog(manifest.default);
    let saved;
    try {
      saved = localStorage.getItem(preferenceKey);
    } catch {
      /* Use browser preference. */
    }
    const preferred =
      match(saved) ||
      (navigator.languages || [navigator.language]).map(match).find(Boolean) ||
      manifest.default;
    try {
      await setLanguage(preferred, false);
    } catch {
      await setLanguage(manifest.default, false);
    }
  }
  function apply(root = document) {
    document.documentElement.lang = language;
    document.documentElement.dir = settings().dir || "ltr";
    document.title = t("app.title");
    root.querySelectorAll("[data-i18n]").forEach((element) => {
      element.textContent = t(element.dataset.i18n);
    });
    for (const attribute of ["aria-label", "title", "placeholder", "content"]) {
      root.querySelectorAll(`[data-i18n-${attribute}]`).forEach((element) => {
        element.setAttribute(
          attribute,
          t(element.getAttribute(`data-i18n-${attribute}`)),
        );
      });
    }
    root.querySelectorAll("[data-language]").forEach((select) => {
      select.replaceChildren(
        ...manifest.languages.map((item) => {
          const option = document.createElement("option");
          option.value = item.code;
          option.textContent = item.name;
          return option;
        }),
      );
      select.value = language;
    });
  }
  return {
    init,
    setLanguage,
    apply,
    t,
    match,
    get language() {
      return language;
    },
    get locale() {
      return settings().locale;
    },
    get languages() {
      return manifest.languages;
    },
  };
})();
// Allows dependency-free unit tests; browsers use the lexical binding above.
if (typeof module !== "undefined") module.exports = FinanzaI18n;

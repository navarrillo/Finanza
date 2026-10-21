# Adding and maintaining languages

Finanza ships with English (`en`, `en-GB`) and Spanish (`es`, `es-ES`). It uses local JSON catalogs and the browser's standard `Intl` APIs, with no translation service or build-time dependency. `app/i18n.py` reads the same catalogs for API errors.

## Add a language

1. Copy `app/static/locales/en.json` to a new file, for example `fr.json`.
2. Translate **values only**. Keep every key, interpolation placeholder and plural object. Do not include HTML: the UI inserts translated text safely.
3. Add the language to `app/static/locales/manifest.json`:

   ```json
   {"code": "fr", "name": "Français", "locale": "fr-FR", "dir": "ltr"}
   ```

   `code` must match the filename. `name` is the native language name shown in both selectors. `locale` is a valid BCP 47 tag supported by `Intl`. The current layout has been verified for left-to-right English/Spanish. `dir` updates the document direction, but introducing a right-to-left language also requires layout review; it is not a claim of complete RTL support.

4. Run the tests, then restart the server or rebuild the Docker image. The selectors and API errors discover the language from the manifest. No bank mapping, SQL view or finance calculation changes are needed.
5. Visually review login and all seven screens on desktop/mobile: longer labels, chart legends, tables, filters, empty/error states, document metadata and language switching. Use synthetic data.

Keep `default` set to `en` for English fallback, or intentionally change it to another fully translated catalog if maintaining a custom installation.

## Keys, interpolation and plurals

Use semantic keys such as `transactions.search` rather than full sentences as identifiers. Add new keys to **every** registered catalog; automated tests require matching keys and placeholders.

```json
{
  "period.consolidated": "Consolidated in {currency} · Personal accounts only",
  "transactions.count": {
    "one": "{count} transaction",
    "other": "{count} transactions"
  }
}
```

`Intl.PluralRules` selects `zero`, `one`, `two`, `few`, `many` or `other` for the language, falling back to `other`. Include `other` in every plural object and keep the same named parameters in every form. Numeric interpolation is formatted with the active locale; use full sentences with placeholders so translators can reorder them.

In dynamic UI templates use `tx("key", params)`, which escapes translated text and parameters for HTML. Use `t("key", params)` for plain text that is assigned through `textContent` or subsequently escaped. Static elements use `data-i18n`, `data-i18n-aria-label`, `data-i18n-title`, `data-i18n-placeholder` or `data-i18n-content`; these are applied without HTML injection. Unknown optional database columns deliberately fall back to their raw column name.

## Selection and persistence

On startup the browser loads the local manifest and default catalog, then chooses:

1. A previously selected, supported language from `localStorage["finanza.language"]`.
2. The first supported browser preference (`es-MX` can match `es`).
3. The default language (`en`).

An unavailable selected catalog or individual missing key falls back to English. A slow earlier language load cannot overwrite a newer selection. If browser storage is disabled, the selector still works for the current session. Only the language code is stored, never credentials or financial information.

Changing language preserves the active screen, month, display currency, period and filters. Native date input controls may follow the browser/OS locale; their values remain ISO dates. Visible date labels, numbers, currency placement, percentage formatting, chart axes and tooltips use `Intl` with the selected locale.

## API errors and exports

The browser sends `X-Finanza-Language`. Other clients can use `Accept-Language` (quality weights and regional variants are supported). Same-origin CSV download links include a validated `lang` query selection. Error responses include translated `detail`, a stable `error_key`, and interpolation `params`; the UI can translate an already-visible error again when the user switches language. Requests use a context-local language to avoid leaking one user's preference to another request.

The export deliberately keeps stable machine-readable columns, original numbers/currencies and original database strings, irrespective of UI language. Its filename is `transactions.csv`. The last column is `consolidated_currency`. Category labels such as `Otros` and `Gastos comunes` remain data/contract identifiers, rather than being rewritten to match the interface language. Custom names, bank descriptions, account aliases and classifications are never machine-translated or sent outside the app.

## Previews

[English](preview-en.png) · [Español](preview-es.png). Both previews use synthetic data.

## Verification

```bash
python -m pytest -q
# Optional, with Node.js installed; no npm packages required:
node --test tests/i18n.test.cjs
```

Python tests check catalog completeness/placeholder parity, static translation keys, negotiation and localized authentication/filter/FX errors. JavaScript tests cover preference selection, fallback, disabled storage, plural forms, concurrent switches and formatting. Existing read-only tests remain in place.

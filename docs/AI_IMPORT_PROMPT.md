# Import your bank exports with your own AI

Copy the prompt below into your coding assistant **inside your own local checkout**. Attach a small redacted sample to explain the bank's format if needed; let the resulting script read the complete exports locally. Giving a cloud AI the full statements sends that provider private financial data. This project does not require that upload or offer a central import service.

The result should be a reusable local importer for that bank, not a one-time SQL dump invented by the AI. Review its reconciliation report before using the database. Monthly updates use that same importer and never require changing the dashboard.

After the importer is ready, follow the [monthly checklist](../README.md#each-month) or its [Spanish version](README.es.md#actualización-mensual-y-wal). This repository supplies the schema, prompt and validator; it does not yet supply bank-specific importers.

```text
Help me import my bank exports into this local Finanza checkout.

First read finanzas_esquema.sql, docs/DATA_CONTRACT.md, and README.md.
Inspect the exported files that I explicitly identify as input. They may be
CSV, XLS/XLSX, OFX or PDF; prefer machine-readable bank exports over PDF.
Do all processing locally. Do not send financial data to external services,
commit it to Git, or put real examples, account details or logs in public files.

Input files: [LOCAL PATHS, preferably inside imports/]
Output database: data/finanzas.db
Account aliases, currencies, institutions, personal/joint status:
[DESCRIBE THESE HERE; aliases are enough, full IBANs are optional]
Export date format and decimal/thousands conventions, if known: [DETAILS]
FX input file if needed (daily EUR/SEK, direction and source): [LOCAL PATH]

Build an offline, rerunnable importer separate from app/. The dashboard must
remain read-only. If the target database does not exist, initialize it with
python3 scripts/init_db.py --output data/finanzas.db. If it exists, back it up
locally with SQLite's backup API before any updates. Never overwrite it with
a new schema. Never modify finanzas_esquema.sql, the views, or the dashboard
to make invalid input appear to work.

Use the exact schema, constraints, views and seeded category catalog.
Enable PRAGMA foreign_keys=ON on the importer connection. Use transactions
and parameterized SQL. Keep bank/account identities stable across months.
Make bank-specific parsing configuration reusable. Ask me only for facts
that cannot be inferred reliably, especially account ownership, ambiguous
dates or signs, and which transfers connect my own accounts.

Map dates to ISO YYYY-MM-DD, correctly handle the bank's locale and debit /
credit conventions, keep original descriptions, and keep amounts in the
account currency (negative = outgoing). Do not convert stored transactions.
Use an account alias and leave holder/IBAN null if not needed. Preserve source
provenance locally; do not fabricate balance_after, counterparties, rates,
source transactions or investment valuations.

Use a stable per-account bank transaction ID for deduplication when possible.
Otherwise design a canonical hash with occurrence handling: two legitimate
identical transactions must not collapse, and overlapping monthly exports
must not duplicate old rows. Explain limitations and stop for review when
matching is ambiguous. A source-file hash alone is not sufficient for
transaction deduplication. Avoid INSERT OR REPLACE; skip exact duplicates
and flag conflicting versions. A second import of the same input must add
zero rows. Preserve every classification whose method='manual'.

Use txn_classification and txn_splits, never parse descriptions at dashboard
runtime. Internal transfers are excluded; reimbursements are not income;
investment and assets have separate kinds; one-off expenses have their flag;
joint accounts are separate. My contributions to a joint account use the
personal category 'Gastos comunes'. Unknown classifications go to 'Otros'
with a review list. Do not guess internal transfers solely because dates or
amounts coincide. Split amounts must sum to the parent amount, retain its
sign and currency, and carry flags on each split line.

The supplied monthly view already excludes one-off expenses from expenses
and net_expenses. Preserve that behavior. Investment and asset indicators
are signed net flows, including redemptions. Do not count reimbursements as
salary or infer all positive amounts are wages.

If EUR/SEK conversion is needed, import historical rates ONLY from a real
local rate dataset I provide, preserving dates, base, quote and source.
Request the dataset if absent. Never invent rates or use today's rate for
past activity. The dashboard carries forward only an earlier available rate.

Validate in a temporary database before updating my target. Reconcile source
row counts, accepted/rejected/duplicate counts, signed totals by account /
period / currency, and balances when the source provides them. Do not hide
unparsed or rejected rows. Verify foreign keys and v_split_errors. Ensure
reimporting the same file is idempotent and overlapping files do not double
count. Run python3 scripts/validate_db.py --db data/finanzas.db and the
project tests. Keep diagnostic records with private data in ignored folders.
A passing schema check does not prove classifications or balances correct:
show me a local reconciliation summary and the items needing human review.

Deliver the reusable importer, the exact command to run it next month, a synthetic
sample generated by its test code, and a summary of any unresolved assumptions. Only
write synthetic fixtures or parsing code to publishable paths. Point my
local .env at the result (DB_PATH=./data/finanzas.db,
DB_FILENAME=finanzas.db, DEMO_MODE=false), preserving my existing credentials.
Use the README for local/Docker deployment; keep the SQLite data mount
read-only for the dashboard container.
```

If you prefer a Spanish prompt, ask your assistant to translate this prompt while preserving the filenames, schema and rules. The dashboard UI supports English and Spanish (and local translation catalogs for additional languages); consolidated currencies are currently EUR/SEK. User-supplied data and category names remain in their original language. A new bank does not need dashboard changes if its data uses this contract; new currencies require application work.

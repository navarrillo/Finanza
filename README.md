# Finanza

A private, self-hosted personal finance dashboard for your bank exports. Run it on your own computer or server; your accounts, statements and database stay with you.

**The dashboard only reads SQLite.** Bank-specific importers run separately. The public repository contains the app, an empty database schema, a generic category catalog and a generator for synthetic examples. No real account records, shared login password, cloud account or external runtime service is required.

FastAPI + Python's SQLite driver + plain HTML/CSS/JavaScript: one process, no frontend build step, no CDN, analytics or telemetry. The responsive interface supports **English and Spanish**, with **EUR/SEK** consolidation and SEK selected by default. Account cards, bank names, initials and filter labels are read dynamically from each user’s accounts/institutions; the demo uses fictional banks. Any bank can work through the schema; additional consolidated currencies need application changes. New UI languages use local translation files without changing the finance logic.

[Language guide](docs/INTERNATIONALIZATION.md) · [Detailed Spanish guide](docs/README.es.md) · [AI import prompt](docs/AI_IMPORT_PROMPT.md) · [Database contract](docs/DATA_CONTRACT.md) · [MIT license](LICENSE)

## Try the demo

From the repository root, with Python 3.11+ and Docker Compose v2 installed:

```bash
python3 scripts/setup.py --demo
docker compose up -d --build
```

Open [localhost:8000](http://127.0.0.1:8000). Sign in using `APP_USER` and `APP_PASSWORD` from your local `.env`. Setup generates a unique random password and session secret, and does not overwrite existing files. If `.env` already exists, its settings are preserved; check that it points to the intended database.

The database is synthetic, including split payments, reimbursements, transfers, one-off purchases, investment flows and a separate joint account. There is no default public password.

Without Docker:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/setup.py --demo
set -a
source .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

## Languages

Use the language selector on the login screen or in the dashboard header. The first visit follows a supported browser language, with English as the fallback. An explicit choice is remembered locally; no account or financial data is stored with that preference. Dates, numbers, currencies, percentages, chart labels, accessible controls and API errors follow the selected language.

Switching language keeps the active page, month, currency and filters. Institution names, account aliases, descriptions and category/subcategory names remain the original database values; translation never rewrites SQLite. CSV column names and raw data remain a stable machine-readable format (`consolidated_currency` for the final column).

To add another language, copy `app/static/locales/en.json`, translate its values, and register its code, native name and `Intl` locale in `manifest.json`. The language selector and API error handling discover it automatically after restart/rebuild. See [the internationalization guide](docs/INTERNATIONALIZATION.md).

## Use your bank exports

1. Keep exports in the ignored `imports/` directory. Prefer CSV/XLSX/OFX over PDF when your bank offers them.
2. Run `python3 scripts/setup.py` for a new empty personal database and private configuration. To create only a database, use `python3 scripts/init_db.py --output data/finanzas.db`. Existing files are never overwritten by these helpers.
3. Give your own coding assistant the [copy-and-paste import prompt](docs/AI_IMPORT_PROMPT.md). It should read the [contract](docs/DATA_CONTRACT.md) and [canonical SQL schema](finanzas_esquema.sql), then build a repeatable, local importer for your bank. You can share redacted examples for parser design and let the generated script read complete statements locally. The app does not include a universal importer, and an AI-generated parser still needs validation.
4. Run the importer and `python3 scripts/validate_db.py --db data/finanzas.db`. Reconcile counts, totals, duplicates, balances and classification review items with the bank's export. EUR/SEK consolidation requires real historical FX data supplied locally; neither the AI nor the app should invent rates.
5. Point your local `.env` at your database, preserving your credentials:

   ```dotenv
   DB_PATH=./data/finanzas.db
   DB_DIRECTORY=./data
   DB_FILENAME=finanzas.db
   DEMO_MODE=false
   ```

6. Restart the app (`docker compose up -d --force-recreate` for Docker) after changing its environment. For later monthly data imports, just finish the import and press ↻ in the dashboard; no app restart or code change is needed.

An importer can write **only outside the dashboard runtime**. Do not change the schema or views to fit a bank; map the export into the existing contract. If you use a cloud AI service, submitting statements sends private data to that provider: local parsing does not require such an upload.

## What it shows

- Monthly and trailing 12-month income, net spending, savings, savings rate and net investment flows, with comparisons.
- Monthly evolution and a separate one-off expense chart. The canonical view already excludes one-offs from habitual net expenses; savings after one-offs is also shown.
- Category/subcategory breakdown and bank filters, with reimbursements reducing spending.
- Searchable, paginated movements, highlighted unclassified `Otros` rows and filtered CSV export. Split lines count as one parent movement.
- Account balances in their original currencies, derived from movement balances and official snapshots.
- A separate joint account view. Joint purchases are not added to personal totals; personal `Gastos comunes` contributions are counted once.
- Empty states for investments, budgets, recurring series and documents; available records appear automatically.

Daily EUR/SEK conversion uses the latest supplied rate on or before the movement date. Missing rates produce an explicit error rather than partial totals. Monthly native-currency totals come from `v_monthly_cashflow`; conversion weights reconcile with that view.

## Deploy on your own server

Copy the code to a Linux host with Docker Engine, Compose v2 and Python 3.11+ for the standalone setup helpers. Keep the private database on that host, e.g. `/srv/finanza/data/finanzas.db`. Generate `.env` locally with `scripts/setup.py` or fill `.env.example` with new credentials. Set:

```dotenv
APP_USER=admin
APP_PASSWORD=your-own-unique-long-password
SESSION_SECRET=your-own-random-secret-at-least-32-characters
DB_DIRECTORY=/srv/finanza/data
DB_FILENAME=finanzas.db
COOKIE_SECURE=true
PUBLIC_ORIGIN=https://finanza.example.com
DEMO_MODE=false
```

Generate secret values with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`; do not use the example text as a credential. Protect `.env` with `chmod 600 .env`.

```bash
docker compose config --quiet
docker compose up -d --build
```

The container sets `DB_PATH=/data/${DB_FILENAME}`, mounts the **data directory read-only**, runs as UID/GID `10001:10001`, and exposes port 8000 on host loopback only. Grant that user read/traverse access to the directory and database. The separate importer keeps write access outside the dashboard container.

For Caddy installed on the host, adapt [Caddyfile.example](Caddyfile.example):

```caddyfile
finanza.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

Point your domain to the server; Caddy handles HTTPS. Keep the app login enabled. Set `PUBLIC_ORIGIN` to the exact browser origin so login/logout checks work behind the proxy. `COOKIE_SECURE=false` is for local HTTP only. If Caddy runs in another container, use a shared private Docker network and `reverse_proxy finanzas:8000` instead of its own loopback; remove the host port mapping if unnecessary.

The app opens a new `mode=ro` connection per request, enables `query_only`, and reads a consistent transaction. It never performs a migration, checkpoint or import. For a changing WAL database, the importer must publish a consistent copy or ensure existing readable WAL/SHM files are available. Do not copy a live `.db` alone with pending WAL changes or mark a changing database `immutable`. See the [detailed deployment guide](docs/README.es.md#actualización-mensual-y-wal).

Runtime requests stay on your own origin; package installation and image building need registry access. Login uses an eight-hour signed HttpOnly/SameSite cookie and an in-memory attempt limit. Put public deployments behind HTTPS and your reverse proxy's access controls.

## Publishing and contributing

The **code can be public; user financial data must stay private**. `.gitignore` and `.dockerignore` exclude credentials, databases/sidecars, common statement formats, private directories, SQL dumps and generated archives. Only `finanzas_esquema.sql` is allowed as a public SQL file; it contains schema definitions and generic category seeds. `.env.example` contains no credentials. Test credentials are disposable test-only values.

Before committing, inspect the files Git will include:

```bash
git status --short --untracked-files=all
git ls-files --cached --others --exclude-standard
git check-ignore .env data/finanzas.db imports/statement.csv
```

These rules do not remove files already tracked or erase past commits. Never force-add a statement, private database, `.env`, private log or SQL export. If a secret was published, revoke it; deleting the latest file alone is not a cleanup of history. GitHub explains [ignore rules](https://docs.github.com/en/get-started/git-basics/ignoring-files) and [removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

For bank importer contributions, include parser code, mapping notes and **synthetic** fixtures only. Real exports belong in private local folders. Keep `app/` read-only, preserve the canonical views, and test idempotent and overlapping imports. Filing a public issue should never require a real statement.

## Structure and tests

```text
app/                    # Read-only API, authentication and static dashboard
app/static/locales/     # Shared UI/API translation catalogs + language manifest
scripts/setup.py        # Unique local credentials + demo or empty DB
scripts/init_db.py      # Standalone empty DB initializer; no overwrite
scripts/create_demo.py  # Reproducible synthetic data generator; no overwrite
scripts/validate_db.py  # Read-only import contract validator
finanzas_esquema.sql     # Public canonical schema + generic category catalog
docs/                   # AI prompt, data contract and Spanish deployment guide
tests/                  # Temporary synthetic databases only
Dockerfile
docker-compose.yml
Caddyfile.example
.env.example
LICENSE
```

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover read-only database access, unchanged file hashes, login, daily/inverse/weekend FX, view reconciliation, split counts, reimbursements, one-offs, investment returns, joint separation, filters, CSV and empty/future tables. Setup/validation tests use temporary files. Translation checks cover key/placeholder parity and API errors; optional dependency-free JavaScript tests also check browser detection, saved preferences, plurals, fallback, races and locale formatting when Node.js is available (Node.js is not needed to run the app). Docker configuration has been validated; the image has not been built/run in the development environment because no Docker daemon was available there. Validate it on your target host before deployment.

Licensed under [MIT](LICENSE).

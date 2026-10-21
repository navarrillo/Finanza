"""The runtime has no database creation, migration, or mutation path."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .i18n import translate


class DataError(ValueError):
    def __init__(self, key, **params):
        self.key = key
        self.params = params
        super().__init__(translate(key, params))


@contextmanager
def connect():
    path = Path(os.environ.get("DB_PATH", "data/finanzas.db")).expanduser().resolve()
    if not path.is_file():
        raise DataError("errors.dbMissing")
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")  # One consistent snapshot for all queries in this request.
        yield con
    finally:
        con.close()


def rows(con, sql, params=()):
    return [dict(row) for row in con.execute(sql, params)]


def optional_rows(con, name):
    allowed = {
        "budgets",
        "documents",
        "instruments",
        "holding_snapshots",
        "recurring_series",
    }
    if name not in allowed:
        raise DataError("errors.table")
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type=? AND name=?", ("table", name)
    ).fetchone()
    if not exists:
        return {"rows": [], "count": 0}
    return {
        "rows": rows(con, f'SELECT * FROM "{name}" LIMIT 200'),
        "count": con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0],
    }


class FX:
    def __init__(self, con, target):
        self.target = target
        self.rates = rows(
            con, "SELECT rate_date, base, quote, rate FROM fx_rates ORDER BY rate_date"
        )
        self.cache = {}

    def rate(self, source, day):
        if source == self.target:
            return 1.0
        key = source, day
        if key not in self.cache:
            matches = [
                r
                for r in self.rates
                if r["rate_date"] <= day
                and {r["base"], r["quote"]} == {source, self.target}
                and float(r["rate"]) > 0
            ]
            if not matches:
                raise DataError(
                    "errors.fxMissing", source=source, target=self.target, day=day
                )
            r = matches[-1]
            self.cache[key] = (
                float(r["rate"]) if r["base"] == source else 1 / float(r["rate"])
            )
        return self.cache[key]

    def amount(self, amount, source, day):
        return float(amount or 0) * self.rate(source, day)

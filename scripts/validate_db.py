#!/usr/bin/env python3
"""Validate the import contract read-only; report counts, never financial records."""

import argparse
import json
import math
import re
import sqlite3
from datetime import date
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "finanzas_esquema.sql"
KINDS = (
    "expense",
    "income",
    "transfer",
    "reimbursement",
    "investment",
    "asset",
    "neutral",
)


def valid_date(value):
    try:
        return (
            isinstance(value, str)
            and len(value) == 10
            and date.fromisoformat(value).isoformat() == value
        )
    except ValueError:
        return False


def finite_number(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (ValueError, TypeError, OverflowError):
        return False


def canonical_objects(con):
    return {
        (kind, name): re.sub(r"\s+", " ", sql).strip()
        for kind, name, sql in con.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        )
    }


def validate(path):
    report = {"valid": False, "errors": [], "warnings": [], "counts": {}}
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        report["errors"].append("Database does not exist; no file has been created.")
        return report
    con = None
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        actual, canonical = canonical_objects(con), canonical_objects(expected)
        for kind, name in canonical:
            if (kind, name) not in actual:
                report["errors"].append(f"Missing canonical {kind}: {name}.")
            elif actual[(kind, name)] != canonical[(kind, name)]:
                report["errors"].append(
                    f"Canonical definition differs: {name}. Do not modify the supplied schema."
                )
        if report["errors"]:
            return report
        integrity = [r[0] for r in con.execute("PRAGMA quick_check")]
        if integrity != ["ok"]:
            report["errors"].append("SQLite integrity check failed.")
        foreign_keys = len(con.execute("PRAGMA foreign_key_check").fetchall())
        if foreign_keys:
            report["errors"].append(f"{foreign_keys} foreign key violations.")
        for table in ("accounts", "transactions", "txn_splits", "fx_rates"):
            report["counts"][table] = con.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]

        checks = {
            "Split sums do not match parent movements": "SELECT COUNT(*) FROM v_split_errors",
            "Split signs or numbering are invalid": """SELECT COUNT(*) FROM txn_splits s JOIN transactions t ON t.id=s.transaction_id
                                                    WHERE s.part_no<1 OR s.amount*t.amount<0""",
            "Transaction currency differs from its account": """SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE t.currency<>a.currency""",
            "Snapshot currency differs from its account": """SELECT COUNT(*) FROM balance_snapshots s JOIN accounts a ON a.id=s.account_id WHERE s.currency<>a.currency""",
            "Unsupported account currencies (currently EUR/SEK)": "SELECT COUNT(*) FROM accounts WHERE currency NOT IN ('EUR','SEK')",
            "Account joint flags must be 0 or 1": "SELECT COUNT(*) FROM accounts WHERE is_joint NOT IN (0,1)",
            "Classification flags must be 0 or 1": """SELECT COUNT(*) FROM txn_classification WHERE is_internal_transfer NOT IN (0,1) OR is_one_off NOT IN (0,1) OR is_pass_through NOT IN (0,1)""",
            "Split flags must be 0 or 1": """SELECT COUNT(*) FROM txn_splits WHERE is_internal_transfer NOT IN (0,1) OR is_one_off NOT IN (0,1) OR is_pass_through NOT IN (0,1)""",
            "Missing transaction deduplication hash": "SELECT COUNT(*) FROM transactions WHERE trim(dedup_hash)=''",
        }
        for message, sql in checks.items():
            count = con.execute(sql).fetchone()[0]
            if count:
                report["errors"].append(f"{message}: {count}.")
        bad_kinds = con.execute(
            "SELECT COUNT(*) FROM categories WHERE kind NOT IN (?,?,?,?,?,?,?)", KINDS
        ).fetchone()[0]
        if bad_kinds:
            report["errors"].append(f"Invalid category kinds: {bad_kinds}.")
        for table, column, nullable in (
            ("transactions", "booking_date", False),
            ("transactions", "value_date", True),
            ("balance_snapshots", "as_of", False),
            ("fx_rates", "rate_date", False),
            ("holding_snapshots", "as_of", False),
        ):
            count = sum(
                not valid_date(v)
                for (v,) in con.execute(f'SELECT "{column}" FROM "{table}"')
                if not (nullable and v is None)
            )
            if count:
                report["errors"].append(
                    f"Invalid ISO dates in {table}.{column}: {count}."
                )
        for table, column, nullable in (
            ("transactions", "amount", False),
            ("transactions", "balance_after", True),
            ("txn_splits", "amount", False),
            ("balance_snapshots", "balance", False),
            ("fx_rates", "rate", False),
        ):
            count = sum(
                not finite_number(v)
                for (v,) in con.execute(f'SELECT "{column}" FROM "{table}"')
                if not (nullable and v is None)
            )
            if count:
                report["errors"].append(
                    f"Invalid finite numbers in {table}.{column}: {count}."
                )
        nonpositive = con.execute(
            "SELECT COUNT(*) FROM fx_rates WHERE rate<=0"
        ).fetchone()[0]
        if nonpositive:
            report["errors"].append(f"Nonpositive FX rates: {nonpositive}.")
        missing = 0
        for currency, day in con.execute(
            "SELECT currency,booking_date FROM transactions UNION SELECT currency,as_of FROM balance_snapshots"
        ):
            if currency not in ("EUR", "SEK") or not valid_date(day):
                continue
            target = "SEK" if currency == "EUR" else "EUR"
            exists = con.execute(
                """SELECT 1 FROM fx_rates WHERE rate_date<=? AND rate>0
                                     AND ((base=? AND quote=?) OR (base=? AND quote=?)) LIMIT 1""",
                (day, currency, target, target, currency),
            ).fetchone()
            if not exists:
                missing += 1
        if missing:
            report["errors"].append(
                f"Missing historical EUR/SEK coverage for {missing} distinct currency/date pairs. Supply a real local rate dataset."
            )
        unclassified = con.execute(
            "SELECT COUNT(DISTINCT id) FROM v_transactions WHERE category IS NULL OR category='Otros'"
        ).fetchone()[0]
        if unclassified:
            report["warnings"].append(
                f"{unclassified} personal movements require classification review."
            )
        if not report["counts"]["transactions"]:
            report["warnings"].append(
                "No transactions: the dashboard will display empty states."
            )
        report["warnings"].append(
            "Source reconciliation, ownership, duplicate identity and classification accuracy require a separate human/importer review."
        )
        report["valid"] = not report["errors"]
    except (sqlite3.Error, OSError):
        # Do not echo raw DB errors that could contain private paths or row values.
        report["errors"].append(
            "Database could not be validated. Check file integrity, canonical schema, permissions and WAL sidecars."
        )
    finally:
        expected.close()
        if con is not None:
            con.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/finanzas.db")
    args = parser.parse_args()
    result = validate(args.db)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["valid"] else 1)

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.create_demo import create_demo
from scripts.init_db import create_empty_database
from scripts.setup import setup
from scripts.validate_db import validate

ROOT = Path(__file__).resolve().parents[1]


def read_env(path):
    return dict(
        line.split("=", 1) for line in path.read_text().splitlines() if "=" in line
    )


def test_new_empty_database_and_validation_are_readonly(tmp_path):
    path = create_empty_database(tmp_path / "empty.db")
    before = path.read_bytes()
    report = validate(path)
    assert report["valid"]
    assert report["counts"]["transactions"] == 0
    assert report["warnings"]
    assert path.read_bytes() == before
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    assert con.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 21
    con.close()
    with pytest.raises(FileExistsError):
        create_empty_database(path)
    assert path.read_bytes() == before


def test_missing_validation_target_is_not_created(tmp_path):
    path = tmp_path / "absent.db"
    result = validate(path)
    assert not result["valid"]
    assert not path.exists()


def test_setup_unique_credentials_and_preserves_existing_files(tmp_path, capsys):
    first, second = tmp_path / "first", tmp_path / "second"
    setup(first, demo=False)
    setup(second, demo=False)
    a, b = read_env(first / ".env"), read_env(second / ".env")
    assert a["APP_PASSWORD"] != b["APP_PASSWORD"]
    assert a["SESSION_SECRET"] != b["SESSION_SECRET"]
    assert len(a["APP_PASSWORD"]) >= 24
    assert len(a["SESSION_SECRET"]) >= 32
    assert a["DEMO_MODE"] == "false"
    assert a["MFA_REQUIRED"] == "true"
    assert a["AUTH_DB_PATH"] == "./private/auth/auth.db"
    assert (first / "private/auth").is_dir()
    if os.name == "posix":
        assert stat.S_IMODE((first / ".env").stat().st_mode) == 0o600
    output = capsys.readouterr().out
    assert a["APP_PASSWORD"] not in output
    assert a["SESSION_SECRET"] not in output
    before = {p: p.read_bytes() for p in (first / ".env", first / "data/finanzas.db")}
    setup(first, demo=False)
    assert all(p.read_bytes() == content for p, content in before.items())


def test_setup_demo_and_no_sensitive_data_in_validation_output(tmp_path):
    setup(tmp_path, demo=True)
    path = tmp_path / "data/demo.db"
    assert read_env(tmp_path / ".env")["DEMO_MODE"] == "true"
    assert read_env(tmp_path / ".env")["MFA_REQUIRED"] == "false"
    con = sqlite3.connect(path)
    con.execute(
        "UPDATE transactions SET description='PRIVATE-DESCRIPTION-MARKER',counterparty='PRIVATE-NAME-MARKER' WHERE id=1"
    )
    con.commit()
    con.close()
    before = hashlib.sha256(path.read_bytes()).digest()
    report = validate(path)
    assert report["valid"], report
    assert report["counts"]["transactions"] > 0
    assert "PRIVATE-" not in json.dumps(report)
    assert hashlib.sha256(path.read_bytes()).digest() == before


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            "UPDATE transactions SET booking_date='2026-02-30' WHERE id=1",
            "Invalid ISO dates",
        ),
        (
            "UPDATE transactions SET amount='not-a-number' WHERE id=1",
            "Invalid finite numbers",
        ),
        (
            "UPDATE txn_classification SET is_one_off=2 WHERE transaction_id=1",
            "Classification flags",
        ),
        (
            "UPDATE txn_splits SET amount=amount-1 WHERE id=(SELECT MIN(id) FROM txn_splits)",
            "Split sums",
        ),
        ("DELETE FROM fx_rates", "Missing historical EUR/SEK"),
        (
            "UPDATE transactions SET account_id=999999 WHERE id=1",
            "foreign key violations",
        ),
        (
            "DROP VIEW v_monthly_cashflow; CREATE VIEW v_monthly_cashflow AS SELECT 1 AS income",
            "Canonical definition differs",
        ),
    ],
)
def test_validator_rejects_invalid_imports(tmp_path, mutation, message):
    path = tmp_path / "invalid.db"
    create_demo(path)
    con = sqlite3.connect(path)
    con.executescript(mutation)
    con.close()
    report = validate(path)
    assert not report["valid"]
    assert any(message in error for error in report["errors"]), report


def test_public_repo_excludes_private_inputs_and_allows_schema():
    private = [
        ".env",
        ".env.local",
        "data/finanzas.db",
        "finanzas_esquema.db",
        "statement.csv",
        "statement.CSV",
        "statement.XLSX",
        "private.DB",
        "dump.SQL",
        "statement.pdf",
        "raw/statement.json",
        "imports/statement.html",
        "exports/export.json",
        "backup.sql",
        "private/key.txt",
    ]
    for path in private:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", path], cwd=ROOT, capture_output=True
        )
        assert result.returncode == 0, path
    for path in (
        ".env.example",
        "finanzas_esquema.sql",
        "README.md",
        "scripts/init_db.py",
    ):
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", path], cwd=ROOT, capture_output=True
        )
        assert result.returncode == 1, path

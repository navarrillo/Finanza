import csv
import hashlib
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.analytics import dashboard, months_ending
from app.db import DataError, FX, connect
from app.main import app, attempts
from scripts.create_demo import create_demo


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "demo.db"
    create_demo(p)
    monkeypatch.setenv("DB_PATH", str(p))
    monkeypatch.setenv("APP_PASSWORD", "prueba-local-segura-2026")
    monkeypatch.setenv("SESSION_SECRET", "a" * 64)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("APP_USER", "admin")
    attempts.clear()
    return p


@pytest.fixture
def client(db):
    with TestClient(app) as c:
        yield c


def log_in(c):
    assert (
        c.post(
            "/api/login",
            json={"username": "admin", "password": "prueba-local-segura-2026"},
        ).status_code
        == 200
    )


def test_readonly_and_no_file_changes(db, client):
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with connect() as c:
        with pytest.raises(sqlite3.OperationalError):
            c.execute("UPDATE accounts SET name='changed'")
        with pytest.raises(sqlite3.OperationalError):
            c.execute("CREATE TABLE forbidden(id INTEGER)")
    log_in(client)
    for currency in ("EUR", "SEK"):
        assert (
            client.get("/api/dashboard", params={"currency": currency}).status_code
            == 200
        )
        assert (
            client.get("/api/export.csv", params={"currency": currency}).status_code
            == 200
        )
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_no_db_creation(tmp_path, monkeypatch):
    missing = tmp_path / "real-does-not-exist.db"
    monkeypatch.setenv("DB_PATH", str(missing))
    with pytest.raises(DataError):
        with connect():
            pass
    assert not missing.exists()


def test_demo_refuses_overwrite(db):
    before = db.read_bytes()
    with pytest.raises(FileExistsError):
        create_demo(db)
    assert db.read_bytes() == before


def test_auth_cookie_logout_and_tampering(client):
    for url in (
        "/api/dashboard",
        "/api/transactions",
        "/api/export.csv",
        "/api/session",
    ):
        assert client.get(url).status_code == 401
    assert (
        client.post(
            "/api/login", json={"username": "admin", "password": "wrong"}
        ).status_code
        == 401
    )
    log_in(client)
    assert client.get("/api/session").status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/dashboard").status_code == 401
    client.cookies.set("finanzas_session", "tampered.signature")
    assert client.get("/api/dashboard").status_code == 401


def test_login_origin_and_rate_limit(client):
    assert (
        client.post(
            "/api/login",
            json={"username": "admin", "password": "whatever"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    for _ in range(8):
        assert (
            client.post(
                "/api/login", json={"username": "admin", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/login", json={"username": "admin", "password": "wrong"}
        ).status_code
        == 429
    )


def test_fx_weekend_inverse_and_missing(db):
    with connect() as c:
        day = "2026-10-18"  # Sunday. Friday's rate must be used.
        friday = c.execute(
            "SELECT rate FROM fx_rates WHERE rate_date='2026-10-16'"
        ).fetchone()[0]
        assert FX(c, "SEK").amount(100, "EUR", day) == pytest.approx(100 * friday)
        assert FX(c, "EUR").amount(100, "SEK", day) == pytest.approx(100 / friday)
        with pytest.raises(DataError):
            FX(c, "SEK").amount(100, "EUR", "2000-01-01")


def test_daily_fx_not_month_average(db):
    with connect() as c:
        result = dashboard(c, "SEK", "2026-10")["current"]
        native = c.execute(
            "SELECT SUM(income) FROM v_monthly_cashflow WHERE month='2026-10' AND currency='SEK'"
        ).fetchone()[0]
        # EUR expenses are insurance on day 5; incoming internal EUR transfer is excluded.
        rate = c.execute(
            "SELECT rate FROM fx_rates WHERE rate_date<='2026-10-05' ORDER BY rate_date DESC LIMIT 1"
        ).fetchone()[0]
        native_exp = c.execute(
            "SELECT net_expenses FROM v_monthly_cashflow WHERE month='2026-10' AND currency='SEK'"
        ).fetchone()[0]
        assert result["income"] == pytest.approx(native)
        assert result["net_expenses"] == pytest.approx(native_exp + 95 * rate)
        assert result["savings"] == pytest.approx(
            result["income"] - result["net_expenses"]
        )


def test_monthly_view_is_authoritative(db):
    # Same-currency totals come straight from the supplied view; no hidden reconstruction.
    c = sqlite3.connect(db)
    c.executescript(
        "DROP VIEW v_monthly_cashflow; CREATE VIEW v_monthly_cashflow AS SELECT '2026-10' AS month,'SEK' AS currency,100 AS income,2 AS reimbursements,30 AS expenses,28 AS net_expenses,0 AS one_off_expenses,7 AS invested,0 AS to_assets;"
    )
    c.close()
    with connect() as c:
        result = dashboard(c, "SEK")["current"]
        assert result["income"] == 100
        assert result["net_expenses"] == 28
        assert result["savings"] == 72
        assert result["invested"] == 7
    with connect() as c:
        with pytest.raises(DataError, match="does not match"):
            dashboard(c, "EUR")


def test_splits_reimbursements_internal_joint_and_averages(db):
    with connect() as c:
        data = dashboard(c, "SEK")
        assert len(data["series"]) == 12
        assert (
            data["transaction_count"]
            == c.execute(
                "SELECT COUNT(DISTINCT id) FROM v_transactions WHERE month='2026-10'"
            ).fetchone()[0]
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM v_transactions WHERE month='2026-10'"
            ).fetchone()[0]
            > data["transaction_count"]
        )
        current = data["current"]
        assert current["reimbursements"] == 448
        assert current["invested"] == 2800
        assert data["joint"]["contributions"] == 8400
        assert data["joint"]["income"] == 16800
        own_common = sum(
            -r["amount"]
            for r in c.execute(
                "SELECT amount FROM v_transactions WHERE month='2026-10' AND category='Gastos comunes'"
            )
        )
        assert own_common == 8400
        assert sum(
            r["months"].get("2026-10", 0) - r["one_off"].get("2026-10", 0)
            for r in data["category_rows"]
        ) == pytest.approx(current["net_expenses"])
        personal_names = {
            t["account_name"]
            for t in c.execute("SELECT account_name FROM v_transactions")
        }
        assert "Cuenta común" not in personal_names
        may = next(s for s in data["series"] if s["month"] == "2026-05")
        assert may["one_off_expenses"] == 95000
        assert may["regular_expenses"] == pytest.approx(may["net_expenses"])
        assert data["average"]["net_expenses"] == pytest.approx(
            sum(s["regular_expenses"] for s in data["series"]) / 12
        )
        assert data["average"]["savings"] == pytest.approx(
            sum(s["income"] - s["net_expenses"] for s in data["series"]) / 12
        )
        assert data["totals"]["to_assets"] == 12000
        # Snapshots override the final movement, balances never double count a split.
        a = next(a for a in data["accounts"] if a["id"] == 2)
        assert (
            a["balance"]
            == c.execute(
                "SELECT balance FROM balance_snapshots WHERE account_id=2"
            ).fetchone()[0]
        )


def test_filters_pagination_and_csv_all_rows(client):
    log_in(client)
    f = {
        "start": "2026-10-01",
        "end": "2026-10-31",
        "bank": "demo_sek",
        "currency": "EUR",
    }
    r = client.get("/api/transactions", params={**f, "page_size": 2}).json()
    assert len(r["rows"]) == 2
    assert r["total_lines"] > 2
    assert all(t["bank"] == "demo_sek" and t["month"] == "2026-10" for t in r["rows"])
    exported = client.get("/api/export.csv", params=f)
    lines = list(
        csv.reader(io.StringIO(exported.content.decode("utf-8-sig")), delimiter=";")
    )
    assert len(lines) == r["total_lines"] + 1
    other = client.get(
        "/api/transactions", params={"category": "Otros", "max_amount": 0}
    ).json()
    assert all(t["category"] == "Otros" and t["amount"] < 0 for t in other["rows"])
    assert (
        client.get("/api/transactions", params={"search": "%' OR 1=1 --"}).json()[
            "total_lines"
        ]
        == 0
    )
    assert (
        client.get("/api/transactions", params={"start": "2026-02-30"}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/transactions", params={"min_amount": 10, "max_amount": 0}
        ).status_code
        == 422
    )
    assert client.get("/api/dashboard", params={"currency": "USD"}).status_code == 422
    assert client.get("/api/dashboard", params={"month": "2026-13"}).status_code == 422


def test_csv_formula_injection(client, db):
    c = sqlite3.connect(db)
    c.execute("UPDATE transactions SET description='=HYPERLINK(\"evil\")' WHERE id=1")
    c.commit()
    c.close()
    log_in(client)
    r = client.get("/api/export.csv", params={"search": "HYPERLINK"})
    lines = list(
        csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";")
    )
    assert lines[0]["description"].startswith("'=")


def test_empty_optional_tables_appear_when_populated(client, db):
    log_in(client)
    assert all(
        v["count"] == 0
        for v in client.get("/api/dashboard").json()["optional"].values()
    )
    c = sqlite3.connect(db)
    c.execute(
        "INSERT INTO budgets(id,period,category_id,amount,currency) VALUES(1,'2026-10',3,3000,'SEK')"
    )
    c.execute(
        "INSERT INTO instruments(id,name,isin,currency) VALUES(1,'Fondo ficticio','XX0000000000','EUR')"
    )
    c.commit()
    c.close()
    r = client.get("/api/dashboard").json()
    assert r["optional"]["budgets"]["rows"][0]["amount"] == 3000
    assert r["optional"]["instruments"]["count"] == 1


def test_empty_database(db):
    c = sqlite3.connect(db)
    c.executescript(
        "DELETE FROM transactions; DELETE FROM txn_splits; DELETE FROM txn_classification; DELETE FROM balance_snapshots; DELETE FROM accounts; DELETE FROM fx_rates;"
    )
    c.close()
    with connect() as c:
        data = dashboard(c, "SEK", "2026-10")
        assert data["current"]["income"] == 0
        assert data["current"]["savings_rate"] is None
        assert data["observed_months"] == 0
        assert data["accounts"] == []
        assert months_ending("2026-01", 2) == ["2025-12", "2026-01"]


def test_real_schema_one_off_neutral_income_and_investment_redemption(db):
    c = sqlite3.connect(db)
    # Actual supplied view counts positive neutral movements as income and nets investment returns.
    c.execute(
        "INSERT INTO transactions(id,account_id,booking_date,description,amount,currency,dedup_hash) VALUES(90001,2,'2026-10-20','Ingreso neutral de prueba',50,'SEK','test-neutral')"
    )
    c.execute(
        "INSERT INTO txn_classification(transaction_id,category_id) VALUES(90001,17)"
    )
    c.execute(
        "INSERT INTO transactions(id,account_id,booking_date,description,amount,currency,dedup_hash) VALUES(90002,2,'2026-10-20','Reembolso de inversión',1000,'SEK','test-redemption')"
    )
    c.execute(
        "INSERT INTO txn_classification(transaction_id,category_id) VALUES(90002,18)"
    )
    c.commit()
    c.close()
    with connect() as c:
        d = dashboard(c, "EUR", "2026-10")
        native = c.execute(
            "SELECT income,invested FROM v_monthly_cashflow WHERE month='2026-10' AND currency='SEK'"
        ).fetchone()
        assert native["invested"] == 1800
        assert native["income"] == pytest.approx(37990.4 + 50)
        # Conversion reconciles successfully against exactly the supplied SQL view.
        assert d["current"]["invested"] > 0
        may = next(s for s in d["series"] if s["month"] == "2026-05")
        assert may["one_off_expenses"] > 8000
        assert may["net_expenses"] < 2500
        assert may["savings_after_one_off"] == pytest.approx(
            may["savings"] - may["one_off_expenses"]
        )


def test_unclassified_null_is_filterable_as_otros(client, db):
    c = sqlite3.connect(db)
    c.execute(
        "INSERT INTO transactions(id,account_id,booking_date,description,amount,currency,dedup_hash) VALUES(90003,2,'2026-10-21','Sin clasificación de prueba',-20,'SEK','test-unclassified')"
    )
    c.commit()
    c.close()
    log_in(client)
    result = client.get(
        "/api/transactions", params={"category": "Otros", "search": "Sin clasificación"}
    ).json()
    assert result["total_transactions"] == 1
    assert result["rows"][0]["category"] == "Otros"


def test_bank_names_codes_and_new_accounts_come_from_database(client, db):
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE institutions SET code='everyday_bank',name='Everyday Credit Union' WHERE id=1"
    )
    con.execute(
        "INSERT INTO institutions(id,code,name,country) VALUES(99,'new_bank','New Savings Bank','SE')"
    )
    con.execute(
        "INSERT INTO accounts(id,institution_id,name,currency,is_joint) VALUES(99,99,'My savings','SEK',0)"
    )
    con.commit()
    con.close()
    log_in(client)
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.json()
    names = {
        institution["code"]: institution["name"] for institution in data["institutions"]
    }
    assert names["everyday_bank"] == "Everyday Credit Union"
    assert names["new_bank"] == "New Savings Bank"
    assert "everyday_bank" in data["banks"]
    new_account = next(a for a in data["accounts"] if a["id"] == 99)
    assert new_account["bank"] == "New Savings Bank"
    assert new_account["balance"] is None
    assert all(a["bank"] != "Aurora Demo" for a in data["accounts"])
    filtered = client.get("/api/transactions", params={"bank": "everyday_bank"}).json()
    assert filtered["total_transactions"] > 0
    assert all(t["bank"] == "everyday_bank" for t in filtered["rows"])

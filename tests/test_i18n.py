import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app, attempts
from scripts.create_demo import create_demo

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "app/static/locales"


def leaves(value):
    return list(value.values()) if isinstance(value, dict) else [value]


def test_catalogs_have_matching_keys_and_placeholders():
    manifest = json.loads((LOCALES / "manifest.json").read_text())
    catalogs = {
        item["code"]: json.loads((LOCALES / f"{item['code']}.json").read_text())
        for item in manifest["languages"]
    }
    assert {"en", "es"} <= set(catalogs)
    baseline = catalogs[manifest["default"]]
    for code, catalog in catalogs.items():
        assert re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)*", code)
        assert catalog.keys() == baseline.keys()
        for key, value in catalog.items():
            expected = set(re.findall(r"\{(\w+)\}", leaves(baseline[key])[-1]))
            for text in leaves(value):
                assert text and isinstance(text, str)
                assert set(re.findall(r"\{(\w+)\}", text)) == expected, (code, key)


class TranslationAttributes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.keys = []

    def handle_starttag(self, tag, attrs):
        self.keys.extend(
            value for name, value in attrs if name.startswith("data-i18n") and value
        )


def test_static_and_literal_ui_keys_exist():
    en = json.loads((LOCALES / "en.json").read_text())
    parser = TranslationAttributes()
    parser.feed((ROOT / "app/static/index.html").read_text())
    js = (ROOT / "app/static/app.js").read_text()
    keys = set(parser.keys) | set(re.findall(r'\b(?:t|tx)\(\s*["\']([^"\']+)["\']', js))
    for key in keys:
        if key.endswith("."):
            continue  # Optional database columns have documented raw-name fallback.
        assert key in en, key
    for page in (
        "resumen",
        "evolucion",
        "categorias",
        "movimientos",
        "saldos",
        "comun",
        "inversiones",
    ):
        assert all(
            f"pages.{page}.{field}" in en for field in ("nav", "title", "description")
        )


@pytest.fixture
def localized_client(tmp_path, monkeypatch):
    database = tmp_path / "demo.db"
    create_demo(database)
    monkeypatch.setenv("DB_PATH", str(database))
    monkeypatch.setenv("APP_USER", "admin")
    monkeypatch.setenv("APP_PASSWORD", "private-local-test-2026")
    monkeypatch.setenv("SESSION_SECRET", "a" * 64)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    attempts.clear()
    with TestClient(app) as client:
        yield client, database


def test_api_language_selection_and_no_context_leak(localized_client):
    client, _ = localized_client
    es = client.get(
        "/api/session",
        headers={"Accept-Language": "fr-FR;q=0.8, es-MX;q=0.9, en;q=0.5"},
    )
    assert es.headers["Content-Language"] == "es"
    assert es.json()["detail"] == "Inicia sesión para ver tus finanzas."
    en = client.get("/api/session")
    assert en.headers["Content-Language"] == "en"
    assert en.json()["detail"] == "Sign in to view your finances."
    override = client.get(
        "/api/session", headers={"X-Finanza-Language": "en", "Accept-Language": "es"}
    )
    assert override.json()["error_key"] == "errors.auth"
    assert override.headers["Content-Language"] == "en"
    fallback = client.get("/api/session", headers={"X-Finanza-Language": "../../.env"})
    assert fallback.headers["Content-Language"] == "en"


def test_localized_errors_keep_database_and_data_values_unchanged(localized_client):
    client, database = localized_client
    before = hashlib.sha256(database.read_bytes()).digest()
    assert (
        client.post(
            "/api/login",
            json={"username": "admin", "password": "private-local-test-2026"},
        ).status_code
        == 200
    )
    for code, message in [
        ("en", "The start date must not be after the end date."),
        ("es", "La fecha inicial debe ser anterior a la final."),
    ]:
        result = client.get(
            "/api/transactions",
            params={"start": "2026-10-31", "end": "2026-10-01"},
            headers={"X-Finanza-Language": code},
        )
        assert result.status_code == 422
        assert result.json()["detail"] == message
        assert result.json()["error_key"] == "errors.dateRange"
        rows = client.get(
            "/api/transactions",
            params={"category": "Otros"},
            headers={"X-Finanza-Language": code},
        ).json()["rows"]
        assert all(row["category"] == "Otros" for row in rows)
    assert hashlib.sha256(database.read_bytes()).digest() == before


def test_missing_fx_error_is_localized(localized_client):
    client, database = localized_client
    con = sqlite3.connect(database)
    con.execute("DELETE FROM fx_rates")
    con.commit()
    con.close()
    client.post(
        "/api/login", json={"username": "admin", "password": "private-local-test-2026"}
    )
    for code, phrase in [("en", "exchange rate"), ("es", "tipo de cambio")]:
        response = client.get("/api/dashboard", headers={"X-Finanza-Language": code})
        assert response.status_code == 422
        assert response.json()["error_key"] == "errors.fxMissing"
        assert phrase in response.json()["detail"]


def test_browser_locale_module_without_external_dependencies():
    node = shutil.which("node")
    if node is None:
        pytest.skip(
            "Node is optional; install it to run the dependency-free browser locale unit tests."
        )
    result = subprocess.run(
        [node, "--test", "tests/i18n.test.cjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_another_registered_catalog_is_automatically_available_to_api(
    localized_client, monkeypatch
):
    from app.i18n import CATALOGS

    client, _ = localized_client
    french = {
        **CATALOGS["en"],
        "errors.auth": "Connectez-vous pour consulter vos finances.",
    }
    monkeypatch.setitem(CATALOGS, "fr", french)
    response = client.get("/api/session", headers={"X-Finanza-Language": "fr-CA"})
    assert response.headers["Content-Language"] == "fr"
    assert response.json()["detail"] == french["errors.auth"]

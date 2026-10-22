import re
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import app.main as main
from app.main import (
    app,
    attempts,
    global_attempts,
    revoked,
    unsafe_forwarded_allow_ips,
    weak_secret,
)
from scripts.setup import setup

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "prueba-local-segura-2026"
SESSION = "7c1e9a4b2f083d56e1a7c4b90f2d8e35a6c1b4e7f092d3a8c5e1b6f4a7d0c293"
# Documentation addresses (RFC 5737). Not a deployment.
PROXY = "192.0.2.10"
ASSIGNMENT = re.compile(r"^(APP_PASSWORD|SESSION_SECRET)=(.*)$")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", SESSION)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("APP_USER", "admin")
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    attempts.clear()
    global_attempts.clear()
    revoked.clear()


@pytest.fixture
def client(configured):
    with TestClient(app) as test_client:
        yield test_client


def login(client, password, forwarded=None):
    headers = {}
    if forwarded is not None:
        headers["X-Forwarded-For"] = forwarded
    return client.post(
        "/api/login",
        json={"username": "admin", "password": password},
        headers=headers,
    )


@pytest.mark.parametrize(
    "value,minimum",
    [
        ("", 12),
        ("short", 12),
        ("a" * 64, 32),
        ("abc" * 20, 32),
        ("0123456789abcdef" * 4, 32),
        ("your-own-unique-long-password", 12),
        ("your-own-random-secret-at-least-32-characters", 32),
        ("elige-una-clave-larga-y-privada", 12),
        ("pega-aqui-el-secreto-aleatorio-generado", 32),
        ("una-clave-privada-de-al-menos-12-caracteres", 12),
        ("un-secreto-aleatorio-de-al-menos-32-caracteres", 32),
        ("prefix-your-own-unique-long-password-suffix", 12),
        ("YOUR-OWN-RANDOM-SECRET-AT-LEAST-32-CHARACTERS", 32),
    ],
)
def test_weak_or_documented_secrets_rejected(value, minimum):
    assert weak_secret(value, minimum)


def test_generated_and_passphrase_secrets_accepted():
    assert not weak_secret("correct-horse-battery-staple", 12)
    assert not weak_secret(PASSWORD, 12)
    assert not weak_secret(SESSION, 32)
    for _ in range(40):
        assert not weak_secret(secrets.token_urlsafe(24), 12)
        assert not weak_secret(secrets.token_hex(32), 32)
        assert not weak_secret(secrets.token_hex(16), 32)
        assert not weak_secret(secrets.token_urlsafe(16), 12)


def test_documented_assignments_are_rejected():
    found = False
    paths = [ROOT / "README.md", ROOT / ".env.example", * (ROOT / "docs").rglob("*.md")]
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ASSIGNMENT.match(line.strip())
            if not match:
                continue
            found = True
            minimum = 32 if match.group(1) == "SESSION_SECRET" else 12
            assert weak_secret(match.group(2).strip(), minimum), (path.name, line)
    assert found


def test_setup_secrets_start_the_app(tmp_path, monkeypatch):
    setup(tmp_path, demo=False)
    env = dict(
        line.split("=", 1)
        for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert not weak_secret(env["APP_PASSWORD"], 12)
    assert not weak_secret(env["SESSION_SECRET"], 32)
    monkeypatch.setenv("APP_PASSWORD", env["APP_PASSWORD"])
    monkeypatch.setenv("SESSION_SECRET", env["SESSION_SECRET"])
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    with TestClient(app) as test_client:
        health = test_client.get("/healthz")
        assert health.status_code == 200
        assert "Strict-Transport-Security" not in health.headers


def test_startup_refuses_placeholder_secret(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    monkeypatch.setenv(
        "SESSION_SECRET", "your-own-random-secret-at-least-32-characters"
    )
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    with pytest.raises(RuntimeError, match="scripts/setup.py"):
        with TestClient(app):
            pass


def test_startup_refuses_low_entropy_password(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "a" * 24)
    monkeypatch.setenv("SESSION_SECRET", SESSION)
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    with pytest.raises(RuntimeError, match="scripts/setup.py"):
        with TestClient(app):
            pass


@pytest.mark.parametrize("value", ["*", " * ", "127.0.0.1, *", "0.0.0.0/0", "::/0"])
def test_startup_refuses_trusting_every_peer(monkeypatch, value):
    assert unsafe_forwarded_allow_ips(value)
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", SESSION)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)
    with pytest.raises(RuntimeError, match="FORWARDED_ALLOW_IPS"):
        with TestClient(app):
            pass


def test_specific_proxy_address_is_allowed():
    assert not unsafe_forwarded_allow_ips("127.0.0.1")
    assert not unsafe_forwarded_allow_ips(PROXY)
    assert not unsafe_forwarded_allow_ips("192.0.2.0/24")


def test_logout_revokes_a_copied_cookie(client):
    assert login(client, PASSWORD).status_code == 200
    token = client.cookies["finanzas_session"]
    logged_out = client.post("/api/logout")
    assert logged_out.status_code == 200
    assert "finanzas_session=" in logged_out.headers["set-cookie"]
    client.cookies.set("finanzas_session", token)
    assert client.get("/api/session").status_code == 401


def test_password_change_invalidates_sessions(client, monkeypatch):
    assert login(client, PASSWORD).status_code == 200
    assert client.get("/api/session").status_code == 200
    monkeypatch.setenv("APP_PASSWORD", "otra-clave-distinta-2026-xy")
    assert client.get("/api/session").status_code == 401
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    assert client.get("/api/session").status_code == 200
    monkeypatch.setenv("SESSION_SECRET", secrets.token_hex(32))
    assert client.get("/api/session").status_code == 401


def test_successful_login_does_not_consume_the_global_budget(client):
    assert login(client, PASSWORD).status_code == 200
    assert global_attempts == []


def test_global_cap_across_addresses(client, monkeypatch):
    monkeypatch.setattr(main, "GLOBAL_MAX_ATTEMPTS", 3)
    for _ in range(3):
        attempts.pop("testclient", None)
        assert login(client, "wrong").status_code == 401
    attempts.pop("testclient", None)
    assert login(client, "wrong").status_code == 429
    attempts.pop("testclient", None)
    assert login(client, PASSWORD).status_code == 429


def test_spoofed_forwarded_header_does_not_bypass_the_app(client):
    for index in range(8):
        status = login(client, "wrong", f"203.0.113.{index + 1}").status_code
        assert status == 401
    assert login(client, "wrong", "198.51.100.50").status_code == 429


def test_trusted_proxy_limits_the_observed_client(configured):
    # Same middleware Uvicorn installs for --proxy-headers.
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=PROXY)
    with TestClient(wrapped, client=(PROXY, 443)) as test_client:
        for _ in range(8):
            assert login(test_client, "wrong", "203.0.113.5").status_code == 401
        assert login(test_client, "wrong", "203.0.113.5").status_code == 429
        # A client-supplied prefix must not move the real (rightmost) client
        # into a fresh bucket. A different observed client still has its own.
        assert (
            login(test_client, "wrong", "198.51.100.9, 203.0.113.5").status_code
            == 429
        )
        assert (
            login(test_client, "wrong", "203.0.113.5, 203.0.113.8").status_code
            == 401
        )
        assert login(test_client, PASSWORD, "203.0.113.8").status_code == 200


def test_untrusted_peer_cannot_spoof_a_new_bucket(configured):
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=PROXY)
    with TestClient(wrapped, client=("198.51.100.20", 1234)) as test_client:
        for index in range(8):
            status = login(test_client, "wrong", f"203.0.113.{index + 1}").status_code
            assert status == 401
        assert login(test_client, "wrong", "203.0.113.200").status_code == 429


def test_security_headers_and_hsts_only_with_secure_cookies(client, monkeypatch):
    response = client.get("/healthz")
    assert (
        response.headers["Permissions-Policy"]
        == "camera=(), microphone=(), geolocation=(), payment=(), publickey-credentials-get=(self), publickey-credentials-create=(self)"
    )
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in response.headers
    monkeypatch.setenv("COOKIE_SECURE", "true")
    secured = client.get("/healthz")
    assert secured.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert "includeSubDomains" not in secured.headers["Strict-Transport-Security"]
    assert "preload" not in secured.headers["Strict-Transport-Security"].casefold()


def test_image_enables_proxy_headers_and_hides_the_server_banner():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--proxy-headers" in dockerfile
    assert "--no-server-header" in dockerfile
    assert "--forwarded-allow-ips" not in dockerfile
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assignment = next(
        line for line in compose.splitlines() if "FORWARDED_ALLOW_IPS:" in line
    )
    assert "*" not in assignment
    assert "127.0.0.1" in assignment

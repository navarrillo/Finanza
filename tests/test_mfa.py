import hashlib
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyotp
import pytest
from fastapi.testclient import TestClient

from app import auth_store as store
from app import main
from scripts.create_demo import create_demo

# RFC 6238 test secret, used only in temporary test stores.
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
NOW = 1_800_000_000
PASSWORD = "temporary-test-password-2026"


@pytest.fixture
def enrolled(tmp_path, monkeypatch):
    path = tmp_path / "auth" / "auth.db"
    finance = tmp_path / "finance.db"
    create_demo(finance)
    monkeypatch.setenv("DB_PATH", str(finance))
    monkeypatch.setenv("AUTH_DB_PATH", str(path))
    monkeypatch.setenv("MFA_REQUIRED", "true")
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("APP_USER", "admin")
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "7c1e9a4b2f083d56e1a7c4b90f2d8e35a6c1b4e7f092d3a8c5e1b6f4a7d0c293")
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    main.attempts.clear()
    main.global_attempts.clear()
    main.revoked.clear()
    # Confirm enrollment with the previous step so this step is available to login.
    codes = store.configure(path, SECRET, pyotp.TOTP(SECRET).at(NOW - 30), now=NOW - 30)
    monkeypatch.setattr(store.time, "time", lambda: NOW)
    return path, codes, finance


@pytest.fixture
def client(enrolled):
    with TestClient(main.app) as client:
        yield client


def login(client, code="", password=PASSWORD):
    return client.post("/api/login", json={"username": "admin", "password": password, "code": code})


def test_totp_rfc_vector_and_bounded_drift():
    # RFC 6238's 8-digit 94287082 value at t=59, truncated to six digits.
    assert store.matching_step(SECRET, "287082", 59) == 1
    assert store.matching_step(SECRET, pyotp.TOTP(SECRET).at(NOW + 30), NOW) == NOW // 30 + 1
    assert store.matching_step(SECRET, pyotp.TOTP(SECRET).at(NOW + 60), NOW) is None
    assert store.matching_step(SECRET, "１２３４５６", NOW) is None


def test_both_factors_required_and_failures_generic(client):
    otp = pyotp.TOTP(SECRET).at(NOW)
    replies = [login(client), login(client, "000000"), login(client, otp, "wrong-password")]
    assert all(r.status_code == 401 and "set-cookie" not in r.headers for r in replies)
    assert replies[0].json() == replies[1].json() == replies[2].json()
    for url in ("/api/session", "/api/dashboard", "/api/transactions", "/api/export.csv"):
        assert client.get(url).status_code == 401
    assert login(client, otp).status_code == 200
    assert client.get("/api/session").status_code == 200
    cookie = client.cookies[main.COOKIE]
    assert SECRET not in cookie and otp not in cookie and PASSWORD not in cookie


def test_totp_replay_rejected_after_restart(client, enrolled):
    otp = pyotp.TOTP(SECRET).at(NOW)
    assert login(client, otp).status_code == 200
    # A different client/process has no memory of the first attempt.
    main.attempts.clear()
    main.global_attempts.clear()
    with TestClient(main.app) as other:
        assert login(other, otp).status_code == 401
        assert other.get("/api/session").status_code == 401


def test_recovery_codes_hashed_single_use_and_password_required(client, enrolled):
    path, codes, _ = enrolled
    assert all(code.encode() not in path.read_bytes() for code in codes)
    assert login(client, codes[0], "wrong-password").status_code == 401
    assert login(client, codes[0].lower()).status_code == 200
    with TestClient(main.app) as other:
        assert login(other, codes[0]).status_code == 401
        assert login(other, codes[1].replace("-", "")).status_code == 200


@pytest.mark.parametrize("use_recovery", [False, True])
def test_factor_consumption_is_atomic(enrolled, use_recovery):
    _, codes, _ = enrolled
    code = codes[0] if use_recovery else pyotp.TOTP(SECRET).at(NOW)
    def attempt(index):
        return store.authenticate(True, code, "same-ip", f"nonce-{index}", NOW + 3600, now=NOW)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(attempt, range(4)))
    assert results.count("ok") == 1
    assert results.count("invalid") == 3


def test_persistent_limits_cover_password_and_factor_guesses(client, enrolled):
    for _ in range(8):
        assert login(client, "000000").status_code == 401
    main.attempts.clear()
    main.global_attempts.clear()
    with TestClient(main.app) as restarted:
        assert login(restarted, pyotp.TOTP(SECRET).at(NOW)).status_code == 429


def test_global_limit_across_ips(enrolled):
    for index in range(30):
        assert store.authenticate(False, "", f"ip-{index}", f"nonce-{index}", NOW + 3600, now=NOW) == "invalid"
    assert store.authenticate(True, pyotp.TOTP(SECRET).at(NOW), "fresh", "new", NOW + 3600, now=NOW) == "limited"
    assert store.authenticate(True, pyotp.TOTP(SECRET).at(NOW + 330), "fresh", "new", NOW + 4000, now=NOW + 330) == "ok"


def test_revoked_cookie_stays_revoked_after_restart(client, enrolled):
    assert login(client, enrolled[1][0]).status_code == 200
    cookie = client.cookies[main.COOKIE]
    assert client.post("/api/logout").status_code == 200
    main.revoked.clear()
    with TestClient(main.app) as restarted:
        restarted.cookies.set(main.COOKIE, cookie)
        assert restarted.get("/api/session").status_code == 401


def test_idle_timeout_is_permanent(client, enrolled, monkeypatch):
    assert login(client, enrolled[1][0]).status_code == 200
    monkeypatch.setattr(store.time, "time", lambda: NOW + store.IDLE_TTL + 1)
    assert client.get("/api/session").status_code == 401
    monkeypatch.setattr(store.time, "time", lambda: NOW)
    assert client.get("/api/session").status_code == 401


def test_absolute_timeout_despite_activity(client, enrolled, monkeypatch):
    response = login(client, enrolled[1][0])
    assert response.status_code == 200
    assert "Max-Age=3600" in response.headers["set-cookie"]
    for elapsed in (800, 1600, 2400, 3200):
        monkeypatch.setattr(store.time, "time", lambda elapsed=elapsed: NOW + elapsed)
        assert client.get("/api/session").status_code == 200
    monkeypatch.setattr(store.time, "time", lambda: NOW + 3601)
    assert client.get("/api/session").status_code == 401


def test_legacy_or_unregistered_tokens_rejected(client):
    client.cookies.set(main.COOKIE, main.make_token())
    assert client.get("/api/session").status_code == 401


def test_password_and_username_changes_invalidate_session(client, enrolled, monkeypatch):
    assert login(client, enrolled[1][0]).status_code == 200
    monkeypatch.setenv("APP_USER", "different-user")
    assert client.get("/api/session").status_code == 401
    monkeypatch.setenv("APP_USER", "admin")
    monkeypatch.setenv("APP_PASSWORD", "different-password-2026")
    assert client.get("/api/session").status_code == 401


def test_rotate_and_admin_revoke_invalidate_sessions(client, enrolled):
    path, codes, _ = enrolled
    assert login(client, codes[0]).status_code == 200
    new_secret = pyotp.random_base32()
    new_codes = store.configure(path, new_secret, pyotp.TOTP(new_secret).at(NOW), rotate=True, now=NOW)
    assert client.get("/api/session").status_code == 401
    assert login(client, codes[1]).status_code == 401
    assert login(client, new_codes[0]).status_code == 200
    store.revoke_all(path)
    assert client.get("/api/session").status_code == 401


def test_confirmation_required_existing_store_not_overwritten(enrolled):
    path = enrolled[0]
    before = path.read_bytes()
    with pytest.raises(store.AuthStateError):
        store.configure(path, SECRET, "bad-code", rotate=True, now=NOW)
    assert path.read_bytes() == before
    with pytest.raises(FileExistsError):
        store.configure(path, SECRET, pyotp.TOTP(SECRET).at(NOW), now=NOW)
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o600


def test_auth_lifecycle_does_not_modify_finance_database(client, enrolled):
    finance = enrolled[2]
    before = hashlib.sha256(finance.read_bytes()).digest()
    assert login(client, enrolled[1][0]).status_code == 200
    assert client.get("/api/dashboard").status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert hashlib.sha256(finance.read_bytes()).digest() == before


def test_missing_state_fails_closed(client, enrolled):
    enrolled[0].unlink()
    response = login(client, pyotp.TOTP(SECRET).at(NOW))
    assert response.status_code == 503 and "set-cookie" not in response.headers
    assert "auth.db" not in response.text and SECRET not in response.text
    assert not enrolled[0].exists()
    with pytest.raises(store.AuthStateError):
        with TestClient(main.app):
            pass


def test_missing_store_cannot_reactivate_existing_session(client, enrolled):
    assert login(client, enrolled[1][0]).status_code == 200
    enrolled[0].unlink()
    assert client.get("/api/session").status_code == 503
    assert not enrolled[0].exists()


def test_unwritable_store_fails_closed(client, enrolled):
    path = enrolled[0]
    path.chmod(0o400)
    try:
        assert login(client, enrolled[1][0]).status_code == 503
        with pytest.raises(store.AuthStateError):
            store.validate_runtime()
    finally:
        path.chmod(0o600)


def test_secure_cookie_flags(client, enrolled, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    response = login(client, enrolled[1][0])
    assert response.status_code == 200
    value = response.headers["set-cookie"].lower()
    assert "httponly" in value and "samesite=strict" in value and "secure" in value


def test_local_cli_revoke_and_noninteractive_enrollment(enrolled, tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "not-created.db"
    result = subprocess.run([sys.executable, "scripts/manage_auth.py", "setup", "--state", str(path)], cwd=root, capture_output=True, text=True)
    assert result.returncode == 1
    assert "interactive terminal" in result.stderr
    assert "otpauth://" not in result.stdout and not path.exists()
    assert store.authenticate(True, enrolled[1][0], "ip", "nonce", NOW + 3600, now=NOW) == "ok"
    result = subprocess.run([sys.executable, "scripts/manage_auth.py", "revoke-sessions", "--state", str(enrolled[0])], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0
    assert not store.active_session("nonce", now=NOW)


def test_finance_store_and_same_file_rejected(enrolled, monkeypatch, tmp_path):
    finance = enrolled[2]
    before = finance.read_bytes()
    monkeypatch.setenv("AUTH_DB_PATH", str(finance))
    with pytest.raises(store.AuthStateError, match="separate"):
        store.validate_runtime()
    alias = tmp_path / "hardlink.db"
    alias.hardlink_to(finance)
    with pytest.raises(store.AuthStateError, match="separate"):
        store.state_path(alias)
    assert finance.read_bytes() == before


def test_rotation_rejects_non_auth_database(enrolled, tmp_path):
    path = tmp_path / "unrelated.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE unrelated(value)")
    before = path.read_bytes()
    with pytest.raises(store.AuthStateError):
        store.configure(path, SECRET, pyotp.TOTP(SECRET).at(NOW), rotate=True, now=NOW)
    assert path.read_bytes() == before


def test_mfa_required_by_default_and_no_production_downgrade(enrolled, monkeypatch):
    monkeypatch.delenv("MFA_REQUIRED")
    assert store.mfa_required()
    monkeypatch.setenv("MFA_REQUIRED", "false")
    with pytest.raises(store.AuthStateError):
        store.validate_runtime()
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    with pytest.raises(store.AuthStateError):
        store.validate_runtime()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    store.validate_runtime()
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://example.test")
    with pytest.raises(store.AuthStateError):
        store.validate_runtime()


def test_public_policy_no_secrets_and_origin_checks(client, enrolled):
    assert client.get("/api/auth/config").json() == {"mfa_required": True}
    response = client.post("/api/login", json={"username": "admin", "password": PASSWORD, "code": enrolled[1][0]}, headers={"Origin": "https://evil.test"})
    assert response.status_code == 403
    assert login(client, enrolled[1][0]).status_code == 200

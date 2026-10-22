"""Passkey registration and login. The software authenticator never leaves this file."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import cbor2
import pyotp
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from app import auth_store as store
from app import main
from scripts.create_demo import create_demo

SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
NOW = 1_800_000_000
PASSWORD = "temporary-test-password-2026"
ORIGIN = "https://finanzas.example.test"
RP_ID = "finanzas.example.test"
ROOT = Path(__file__).resolve().parents[1]


class SoftPasskey:
    """Minimal resident credential. Signs the same bytes py_webauthn verifies."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0
        self.user_handle = b""

    def cose(self):
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({
            1: 2,
            3: -7,
            -1: 1,
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        })

    def authenticator_data(self, rp_id, sign_count, *, attested, uv):
        flags = 0x01 | (0x04 if uv else 0) | (0x40 if attested else 0)
        data = hashlib.sha256(rp_id.encode("utf-8")).digest() + bytes([flags]) + sign_count.to_bytes(4, "big")
        if attested:
            credential_id = self.credential_id
            data += bytes(16) + len(credential_id).to_bytes(2, "big") + credential_id + self.cose()
        return data

    def client_data(self, kind, challenge, origin):
        return json.dumps({
            "type": kind,
            "challenge": bytes_to_base64url(challenge),
            "origin": origin,
            "crossOrigin": False,
        }, separators=(",", ":")).encode()

    def _body(self, client_data, extra):
        raw_id = bytes_to_base64url(self.credential_id)
        return {
            "id": raw_id,
            "rawId": raw_id,
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {"clientDataJSON": bytes_to_base64url(client_data), **extra},
        }

    def register(self, options, origin, rp_id=None):
        rp_id = rp_id or options["rp"]["id"]
        challenge = base64url_to_bytes(options["challenge"])
        self.user_handle = base64url_to_bytes(options["user"]["id"])
        auth_data = self.authenticator_data(rp_id, 0, attested=True, uv=True)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return self._body(
            self.client_data("webauthn.create", challenge, origin),
            {"attestationObject": bytes_to_base64url(attestation)},
        )

    def authenticate(self, options, origin, rp_id=None, uv=True):
        rp_id = rp_id or options["rpId"]
        challenge = base64url_to_bytes(options["challenge"])
        self.sign_count += 1
        auth_data = self.authenticator_data(rp_id, self.sign_count, attested=False, uv=uv)
        client_data = self.client_data("webauthn.get", challenge, origin)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return self._body(client_data, {
            "authenticatorData": bytes_to_base64url(auth_data),
            "signature": bytes_to_base64url(signature),
            "userHandle": bytes_to_base64url(self.user_handle),
        })


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
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.delenv("WEBAUTHN_RP_ID", raising=False)
    monkeypatch.delenv("WEBAUTHN_REAUTH_SECONDS", raising=False)
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    main.attempts.clear()
    main.global_attempts.clear()
    main.revoked.clear()
    codes = store.configure(path, SECRET, pyotp.TOTP(SECRET).at(NOW - 30), now=NOW - 30)
    monkeypatch.setattr(store.time, "time", lambda: NOW)
    return path, codes, finance


@pytest.fixture
def client(enrolled):
    with TestClient(main.app) as client:
        yield client


def totp_login(client, code=None):
    code = pyotp.TOTP(SECRET).at(NOW) if code is None else code
    assert client.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 202
    response = client.post("/api/login/verify", json={"code": code})
    assert response.status_code == 200
    return response


def sign_count(path, credential_id):
    with sqlite3.connect(path) as con:
        row = con.execute("SELECT sign_count FROM passkeys WHERE credential_id=?", (credential_id,)).fetchone()
    return None if row is None else row[0]


def register(client, name="Laptop", origin=ORIGIN, rp_id=None):
    options = client.post("/api/passkeys/options")
    assert options.status_code == 200, options.text
    soft = SoftPasskey()
    saved = client.post("/api/passkeys", json={"name": name, "credential": soft.register(options.json(), origin, rp_id)})
    assert saved.status_code == 200, saved.text
    return soft


def test_register_rename_delete_and_passkey_login_without_username(client, enrolled):
    path, codes, finance = enrolled
    before = hashlib.sha256(finance.read_bytes()).digest()
    totp_login(client)
    options = client.post("/api/passkeys/options")
    body = options.json()
    assert body["rp"] == {"name": "Finanza", "id": RP_ID}
    assert body["authenticatorSelection"]["residentKey"] == "required"
    assert body["authenticatorSelection"]["userVerification"] == "required"
    assert body["authenticatorSelection"]["requireResidentKey"] is True
    assert body["timeout"] == store.WEBAUTHN_CHALLENGE_TTL * 1000
    assert "allowCredentials" not in body
    assert base64url_to_bytes(body["user"]["id"]) != b"admin"
    soft = SoftPasskey()
    saved = client.post("/api/passkeys", json={"name": "  Laptop  ", "credential": soft.register(body, ORIGIN)})
    assert saved.status_code == 200
    assert SECRET not in saved.text and "public_key" not in saved.text
    listed = client.get("/api/passkeys")
    assert listed.status_code == 200
    item = listed.json()["passkeys"][0]
    assert set(item) == {"id", "name", "created", "last_used"}
    assert item["name"] == "Laptop"
    assert item["last_used"] is None
    assert listed.json()["can_register"] is True
    renamed = client.patch(f"/api/passkeys/{item['id']}", json={"name": "Desk"})
    assert renamed.status_code == 200
    assert client.get("/api/passkeys").json()["passkeys"][0]["name"] == "Desk"
    assert client.post("/api/logout").status_code == 200
    assert main.COOKIE not in client.cookies

    ceremony = client.post("/api/login/passkey/options")
    assert ceremony.status_code == 200
    public = ceremony.json()
    assert public["rpId"] == RP_ID
    assert public["userVerification"] == "required"
    assert "allowCredentials" not in public
    assert "username" not in public
    assertion = soft.authenticate(public, ORIGIN)
    response = client.post("/api/login/passkey", json=assertion)
    assert response.status_code == 200
    cookie = next(
        value.lower() for value in response.headers.get_list("set-cookie")
        if value.startswith(main.COOKIE + "=")
    )
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=3600" in cookie
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/passkeys").json()["passkeys"][0]["last_used"] == NOW
    assert sign_count(path, soft.credential_id) == 1
    # Password and TOTP remain a fallback. The first authenticator step is already consumed.
    assert client.post("/api/logout").status_code == 200
    totp_login(client, codes[0])
    assert client.get("/api/session").status_code == 200
    item = client.get("/api/passkeys").json()["passkeys"][0]
    assert client.delete(f"/api/passkeys/{item['id']}").status_code == 200
    assert client.get("/api/passkeys").json()["passkeys"] == []
    assert client.post("/api/logout").status_code == 200
    refused = client.post("/api/login/passkey", json=soft.authenticate(
        client.post("/api/login/passkey/options").json(), ORIGIN
    ))
    assert refused.status_code == 401
    assert hashlib.sha256(finance.read_bytes()).digest() == before


def test_reused_challenge_cannot_open_a_second_session(client, enrolled):
    path = enrolled[0]
    totp_login(client)
    soft = register(client)
    assert client.post("/api/logout").status_code == 200
    options = client.post("/api/login/passkey/options").json()
    assertion = soft.authenticate(options, ORIGIN)
    assert client.post("/api/login/passkey", json=assertion).status_code == 200
    assert client.post("/api/logout").status_code == 200
    again = client.post("/api/login/passkey", json=assertion)
    assert again.status_code == 401
    assert again.json()["error_key"] == "errors.credentials"
    assert main.COOKIE not in client.cookies
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM webauthn_challenges").fetchone()[0] == 0
    assert sign_count(path, soft.credential_id) == 1


def test_expired_challenge_is_rejected(client, enrolled, monkeypatch):
    totp_login(client)
    soft = register(client)
    assert client.post("/api/logout").status_code == 200
    options = client.post("/api/login/passkey/options").json()
    assertion = soft.authenticate(options, ORIGIN)
    monkeypatch.setattr(store.time, "time", lambda: NOW + store.WEBAUTHN_CHALLENGE_TTL + 1)
    response = client.post("/api/login/passkey", json=assertion)
    assert response.status_code == 401
    assert response.json()["error_key"] == "errors.credentials"
    assert main.COOKIE not in client.cookies
    assert sign_count(enrolled[0], soft.credential_id) == 0


def test_wrong_origin_and_wrong_rp_id_are_generic_failures(client, enrolled, monkeypatch):
    path = enrolled[0]
    totp_login(client)
    soft = register(client)
    assert client.post("/api/logout").status_code == 200
    wrong_password = client.post("/api/login", json={"username": "admin", "password": "not-the-password"})
    options = client.post("/api/login/passkey/options").json()
    wrong_origin = client.post("/api/login/passkey", json=soft.authenticate(options, "https://evil.example"))
    assert wrong_origin.status_code == 401
    assert wrong_origin.json() == wrong_password.json()
    assert sign_count(path, soft.credential_id) == 0
    assert main.COOKIE not in client.cookies

    monkeypatch.setenv("WEBAUTHN_RP_ID", "example.test")
    options = client.post("/api/login/passkey/options")
    assert options.status_code == 200
    assert options.json()["rpId"] == "example.test"
    # The authenticator answers for the rpId the credential was created with.
    wrong_rp = client.post("/api/login/passkey", json=soft.authenticate(options.json(), ORIGIN, rp_id=RP_ID))
    assert wrong_rp.status_code == 401
    assert wrong_rp.json() == wrong_password.json()
    assert sign_count(path, soft.credential_id) == 0

    monkeypatch.delenv("WEBAUTHN_RP_ID")
    options = client.post("/api/login/passkey/options").json()
    assert client.post("/api/login/passkey", json=soft.authenticate(options, ORIGIN)).status_code == 200


def test_registration_requires_recent_reauthentication(client, enrolled, monkeypatch):
    path = enrolled[0]
    assert client.post("/api/passkeys/options").status_code == 401
    totp_login(client)
    with sqlite3.connect(path) as con:
        con.execute("UPDATE sessions SET authenticated_at=?", (NOW - store.reauth_window() - 1,))
    stale = client.post("/api/passkeys/options")
    assert stale.status_code == 403
    assert stale.json()["error_key"] == "errors.reauth"
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM webauthn_challenges").fetchone()[0] == 0
        con.execute("UPDATE sessions SET authenticated_at=?", (NOW,))
    monkeypatch.setenv("WEBAUTHN_REAUTH_SECONDS", "120")
    with sqlite3.connect(path) as con:
        con.execute("UPDATE sessions SET authenticated_at=?", (NOW - 130,))
    assert client.post("/api/passkeys/options").status_code == 403
    with sqlite3.connect(path) as con:
        con.execute("UPDATE sessions SET authenticated_at=?", (NOW - 100,))
    assert client.post("/api/passkeys/options").status_code == 200
    # A passkey sign-in is itself recent authentication.
    soft = register(client)
    assert client.post("/api/logout").status_code == 200
    options = client.post("/api/login/passkey/options").json()
    assert client.post("/api/login/passkey", json=soft.authenticate(options, ORIGIN)).status_code == 200
    monkeypatch.delenv("WEBAUTHN_REAUTH_SECONDS")
    assert client.get("/api/passkeys").json()["can_register"] is True
    assert client.post("/api/passkeys/options").status_code == 200


def test_passkey_failures_share_the_address_limit_and_stay_generic(client, enrolled):
    bogus = {
        "id": "aaaa",
        "rawId": "aaaa",
        "type": "public-key",
        "response": {"clientDataJSON": "e30", "authenticatorData": "aaaa", "signature": "aaaa"},
    }
    for _ in range(8):
        response = client.post("/api/login/passkey", json=bogus)
        assert response.status_code == 401
        assert response.json()["error_key"] == "errors.credentials"
    main.attempts.clear()
    main.global_attempts.clear()
    with TestClient(main.app) as restarted:
        assert restarted.post("/api/login/passkey", json=bogus).status_code == 429
        assert restarted.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 429


def test_user_verification_and_sign_counter_regression_are_rejected(client, enrolled):
    path = enrolled[0]
    totp_login(client)
    soft = register(client)
    assert client.post("/api/logout").status_code == 200
    options = client.post("/api/login/passkey/options").json()
    unverified = client.post("/api/login/passkey", json=soft.authenticate(options, ORIGIN, uv=False))
    assert unverified.status_code == 401
    assert sign_count(path, soft.credential_id) == 0
    options = client.post("/api/login/passkey/options").json()
    assert client.post("/api/login/passkey", json=soft.authenticate(options, ORIGIN)).status_code == 200
    assert sign_count(path, soft.credential_id) == 2
    with sqlite3.connect(path) as con:
        con.execute("UPDATE passkeys SET sign_count=5 WHERE credential_id=?", (soft.credential_id,))
    assert client.post("/api/logout").status_code == 200
    options = client.post("/api/login/passkey/options").json()
    regressed = client.post("/api/login/passkey", json=soft.authenticate(options, ORIGIN))
    assert regressed.status_code == 401
    assert sign_count(path, soft.credential_id) == 5
    assert main.COOKIE not in client.cookies


def test_cross_origin_browser_requests_are_rejected(client, enrolled):
    totp_login(client)
    headers = {"Origin": "https://evil.example"}
    assert client.post("/api/login/passkey/options", headers=headers).status_code == 403
    assert client.post("/api/passkeys/options", headers=headers).status_code == 403
    with sqlite3.connect(enrolled[0]) as con:
        assert con.execute("SELECT COUNT(*) FROM webauthn_challenges").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM failures").fetchone()[0] == 0


def test_revoke_passkeys_cli_removes_credentials_only(client, enrolled):
    path = enrolled[0]
    totp_login(client)
    soft = register(client, name="Phone")
    assert client.post("/api/logout").status_code == 200
    result = subprocess.run(
        [sys.executable, "scripts/manage_auth.py", "revoke-passkeys", "--state", str(path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Revoked 1 passkey" in result.stdout
    assert SECRET not in result.stdout
    options = client.post("/api/login/passkey/options").json()
    assert client.post("/api/login/passkey", json=soft.authenticate(options, ORIGIN)).status_code == 401
    totp_login(client, enrolled[1][0])
    assert client.get("/api/passkeys").json()["passkeys"] == []
    # Rotating the authenticator does not remove a passkey registered afterwards.
    soft = register(client, name="Replacement")
    new_secret = pyotp.random_base32()
    store.configure(path, new_secret, pyotp.TOTP(new_secret).at(NOW), rotate=True, now=NOW)
    assert client.get("/api/session").status_code == 401
    options = client.post("/api/login/passkey/options").json()
    assert client.post("/api/login/passkey", json=soft.authenticate(options, ORIGIN)).status_code == 200


def test_version_two_store_migrates_without_reenrollment(enrolled):
    path, codes, finance = enrolled
    before = finance.read_bytes()
    with sqlite3.connect(path) as con:
        secret, step = con.execute("SELECT secret, last_step FROM configuration").fetchone()
        recovery = list(con.execute("SELECT hash FROM recovery"))
    path.unlink()
    with sqlite3.connect(path) as con:
        con.executescript(f"""
            PRAGMA application_id={store.APPLICATION_ID};
            PRAGMA user_version=2;
            CREATE TABLE configuration(id INTEGER PRIMARY KEY CHECK(id=1), secret TEXT NOT NULL, last_step INTEGER NOT NULL);
            CREATE TABLE recovery(hash TEXT PRIMARY KEY);
            CREATE TABLE sessions(nonce_hash TEXT PRIMARY KEY, expires REAL NOT NULL, last_seen REAL NOT NULL);
            CREATE TABLE challenges(nonce_hash TEXT PRIMARY KEY, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE failures(ip_hash TEXT NOT NULL, created REAL NOT NULL, cleared INTEGER NOT NULL DEFAULT 0);
        """)
        con.execute("INSERT INTO configuration VALUES(1,?,?)", (secret, step))
        con.executemany("INSERT INTO recovery VALUES(?)", recovery)
    os.chmod(path, 0o600)
    with TestClient(main.app) as migrated:
        assert migrated.post("/api/login", json={"username": "admin", "password": PASSWORD}).status_code == 202
        assert migrated.post("/api/login/verify", json={"code": codes[0]}).status_code == 200
    with sqlite3.connect(path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3
        assert "authenticated_at" in {row[1] for row in con.execute("PRAGMA table_info(sessions)")}
        assert con.execute("SELECT COUNT(*) FROM passkeys").fetchone()[0] == 0
        assert con.execute("SELECT secret FROM configuration").fetchone()[0] == SECRET
    assert finance.read_bytes() == before


def test_relying_party_settings_are_rejected_when_they_do_not_match(enrolled, monkeypatch):
    monkeypatch.setenv("WEBAUTHN_REAUTH_SECONDS", "30")
    with pytest.raises(store.AuthStateError):
        store.validate_runtime()
    monkeypatch.setenv("WEBAUTHN_REAUTH_SECONDS", "300")
    monkeypatch.setenv("WEBAUTHN_RP_ID", "https://finanzas.example.test")
    with pytest.raises(store.AuthStateError):
        store.validate_runtime()
    monkeypatch.setenv("WEBAUTHN_RP_ID", "attacker.example")
    with pytest.raises(store.AuthStateError):
        with TestClient(main.app):
            pass
    monkeypatch.setenv("WEBAUTHN_RP_ID", "example.test")
    store.validate_runtime()

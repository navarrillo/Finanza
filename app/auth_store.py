"""Private authentication state. Never opens or modifies the finance database."""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pyotp

APPLICATION_ID = 0x464E4155
SCHEMA_VERSION = 3
SESSION_TTL = 3600
IDLE_TTL = 900
WINDOW = 300
IP_LIMIT = 8
GLOBAL_LIMIT = 30
CHALLENGE_TTL = 300
CHALLENGE_LIMIT = 5
# WebAuthn ceremonies are short-lived and single-use. Two minutes covers a
# user gesture without leaving a reusable challenge behind.
WEBAUTHN_CHALLENGE_TTL = 120
# Pending passkey login ceremonies for one client address. A newer request
# replaces that address's oldest challenge, so the address can always start
# a fresh ceremony and cannot fill GLOBAL_LIMIT. A constant, like IP_LIMIT,
# not an environment variable. It must stay below GLOBAL_LIMIT.
PASSKEY_LOGIN_IP_LIMIT = 5
MAX_PASSKEYS = 10
_RP_ID = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)


class AuthStateError(Exception):
    pass


def mfa_required():
    value = os.environ.get("MFA_REQUIRED", "true").lower()
    if value not in {"true", "false"}:
        raise AuthStateError("MFA_REQUIRED must be true or false.")
    return value == "true"


def reauth_window():
    """How long a password+TOTP or passkey sign-in may register another passkey."""
    raw = os.environ.get("WEBAUTHN_REAUTH_SECONDS", "300").strip()
    if not re.fullmatch(r"[0-9]+", raw):
        raise AuthStateError("WEBAUTHN_REAUTH_SECONDS must be an integer from 60 to 3600.")
    value = int(raw)
    if value < 60 or value > 3600:
        raise AuthStateError("WEBAUTHN_REAUTH_SECONDS must be an integer from 60 to 3600.")
    return value


def valid_rp_id(value):
    return bool(value) and len(value) <= 253 and bool(_RP_ID.fullmatch(value))


def host_matches_rp(host, rp_id):
    host = (host or "").lower().rstrip(".")
    rp_id = rp_id.lower().rstrip(".")
    return bool(host) and (host == rp_id or host.endswith("." + rp_id))


def configured_origin():
    """PUBLIC_ORIGIN without a trailing slash, or an empty string when unset."""
    return os.environ.get("PUBLIC_ORIGIN", "").strip().rstrip("/")


def validate_webauthn_config():
    """Fail closed on a relying-party setting that cannot match the public origin."""
    reauth_window()
    origin = configured_origin()
    rp_id = os.environ.get("WEBAUTHN_RP_ID", "").strip()
    if rp_id and not valid_rp_id(rp_id):
        raise AuthStateError(
            "WEBAUTHN_RP_ID must be a hostname without a scheme, port, or path."
        )
    if not origin:
        return
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise AuthStateError(
            "PUBLIC_ORIGIN must be an origin without a path, query, or userinfo."
        )
    if rp_id and not host_matches_rp(parsed.hostname, rp_id):
        raise AuthStateError(
            "WEBAUTHN_RP_ID must match the PUBLIC_ORIGIN host or be a parent domain."
        )


def state_path(path=None):
    target = Path(path or os.environ.get("AUTH_DB_PATH", "private/auth/auth.db")).expanduser().resolve()
    finance = Path(os.environ.get("DB_PATH", "data/finanzas.db")).expanduser().resolve()
    if target == finance or (target.exists() and finance.exists() and target.samefile(finance)):
        raise AuthStateError("Authentication state must be separate from DB_PATH.")
    return target


def _check(con):
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if (con.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or version not in {1, 2, SCHEMA_VERSION}):
        raise AuthStateError("Not a Finanza authentication store.")
    row = con.execute("SELECT secret, last_step FROM configuration WHERE id=1").fetchone()
    if not row or not re.fullmatch(r"[A-Z2-7]{32}", row[0]):
        raise AuthStateError("Authentication setup is incomplete.")
    return row


def _columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _migrate(con):
    """Upgrade only this auth store. Version 3 adds passkeys and WebAuthn challenges."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version < 2:
        con.execute(
            "CREATE TABLE IF NOT EXISTS challenges("
            "nonce_hash TEXT PRIMARY KEY, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)"
        )
    if version < SCHEMA_VERSION:
        if "authenticated_at" not in _columns(con, "sessions"):
            con.execute(
                "ALTER TABLE sessions ADD COLUMN authenticated_at REAL NOT NULL DEFAULT 0"
            )
        if "passkey_user" not in _columns(con, "configuration"):
            con.execute("ALTER TABLE configuration ADD COLUMN passkey_user BLOB")
        con.execute(
            """CREATE TABLE IF NOT EXISTS passkeys(
                credential_id BLOB PRIMARY KEY,
                public_key BLOB NOT NULL,
                sign_count INTEGER NOT NULL CHECK(sign_count>=0),
                name TEXT NOT NULL,
                created REAL NOT NULL,
                last_used REAL,
                user_handle BLOB NOT NULL,
                aaguid TEXT NOT NULL DEFAULT ''
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS webauthn_challenges(
                challenge_hash TEXT PRIMARY KEY,
                challenge BLOB NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('authentication','registration')),
                expires REAL NOT NULL,
                session_hash TEXT,
                ip_hash TEXT
            )"""
        )
    if version != SCHEMA_VERSION:
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


@contextmanager
def store(path=None):
    con = None
    try:
        # mode=rw refuses to silently create an empty store at runtime.
        con = sqlite3.connect(state_path(path).as_uri() + "?mode=rw", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        _check(con)
        con.execute("BEGIN IMMEDIATE")
        _migrate(con)
        yield con
        con.commit()
    except (sqlite3.Error, OSError) as exc:
        raise AuthStateError("Authentication state is unavailable.") from exc
    finally:
        if con is not None:
            con.close()


def validate_runtime():
    validate_webauthn_config()
    if not mfa_required():
        if (os.environ.get("DEMO_MODE", "false").lower() != "true"
                or os.environ.get("COOKIE_SECURE", "true").lower() != "false"
                or os.environ.get("PUBLIC_ORIGIN")):
            raise AuthStateError("Disabling MFA is allowed only for a local HTTP demo without PUBLIC_ORIGIN.")
        return
    with store() as con:
        # Prove this volume is writable without changing any authentication state.
        con.execute("UPDATE configuration SET last_step=last_step WHERE id=1")


def matching_step(secret, code, now):
    if not re.fullmatch(r"[0-9]{6}", code):
        return None
    otp = pyotp.TOTP(secret)
    current = int(now // otp.interval)
    matches = [step for step in range(max(0, current - 1), current + 2)
               if hmac.compare_digest(otp.at(step * otp.interval), code)]
    return max(matches) if matches else None


def recovery_hash(code):
    normalized = code.strip().replace("-", "").upper()
    if not re.fullmatch(r"[0-9A-F]{32}", normalized):
        return None
    # Recovery codes contain 128 random bits; SHA-256 cannot be dictionary attacked.
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def new_recovery_codes():
    return ["-".join(re.findall(".{8}", secrets.token_hex(16).upper())) for _ in range(10)]


def _schema_sql():
    return f"""
        PRAGMA application_id={APPLICATION_ID};
        PRAGMA user_version={SCHEMA_VERSION};
        CREATE TABLE configuration(
            id INTEGER PRIMARY KEY CHECK(id=1),
            secret TEXT NOT NULL,
            last_step INTEGER NOT NULL,
            passkey_user BLOB
        );
        CREATE TABLE recovery(hash TEXT PRIMARY KEY);
        CREATE TABLE sessions(
            nonce_hash TEXT PRIMARY KEY,
            expires REAL NOT NULL,
            last_seen REAL NOT NULL,
            authenticated_at REAL NOT NULL
        );
        CREATE TABLE challenges(nonce_hash TEXT PRIMARY KEY, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE failures(ip_hash TEXT NOT NULL, created REAL NOT NULL, cleared INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX failure_dates ON failures(created);
        CREATE TABLE passkeys(
            credential_id BLOB PRIMARY KEY,
            public_key BLOB NOT NULL,
            sign_count INTEGER NOT NULL CHECK(sign_count>=0),
            name TEXT NOT NULL,
            created REAL NOT NULL,
            last_used REAL,
            user_handle BLOB NOT NULL,
            aaguid TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE webauthn_challenges(
            challenge_hash TEXT PRIMARY KEY,
            challenge BLOB NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('authentication','registration')),
            expires REAL NOT NULL,
            session_hash TEXT,
            ip_hash TEXT
        );
    """


def configure(path, secret, confirmed_code, *, rotate=False, now=None):
    """Offline administrator operation. Refuses arbitrary/existing finance files."""
    now = time.time() if now is None else now
    step = matching_step(secret, confirmed_code, now)
    if step is None:
        raise AuthStateError("Authenticator confirmation failed; no configuration changed.")
    target = state_path(path)
    codes = new_recovery_codes()
    if rotate:
        with store(target) as con:
            con.execute("UPDATE configuration SET secret=?, last_step=? WHERE id=1", (secret, step))
            con.execute("DELETE FROM recovery")
            con.execute("DELETE FROM sessions")
            con.execute("DELETE FROM challenges")
            con.execute("DELETE FROM webauthn_challenges")
            con.executemany("INSERT INTO recovery(hash) VALUES (?)", [(recovery_hash(c),) for c in codes])
        return codes
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        con = sqlite3.connect(target)
        try:
            con.executescript(_schema_sql())
            with con:
                con.execute(
                    "INSERT INTO configuration(id, secret, last_step) VALUES(1,?,?)",
                    (secret, step),
                )
                con.executemany("INSERT INTO recovery(hash) VALUES (?)", [(recovery_hash(c),) for c in codes])
        finally:
            con.close()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return codes


def nonce_hash(nonce):
    return hashlib.sha256(nonce.encode()).hexdigest()


def _ip_hash(ip):
    return hashlib.sha256(ip.encode()).hexdigest()


def _prune(con, now):
    con.execute("DELETE FROM failures WHERE created<=?", (now - WINDOW,))
    con.execute("DELETE FROM sessions WHERE expires<=? OR last_seen<=?", (now, now - IDLE_TTL))
    con.execute("DELETE FROM challenges WHERE expires<=?", (now,))
    con.execute("DELETE FROM webauthn_challenges WHERE expires<=?", (now,))


def _limited(con, ip_hash):
    total, address = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(ip_hash=? AND cleared=0),0) FROM failures", (ip_hash,)
    ).fetchone()
    return total >= GLOBAL_LIMIT or address >= IP_LIMIT


def _failure(con, ip_hash, now):
    con.execute("INSERT INTO failures(ip_hash,created) VALUES(?,?)", (ip_hash, now))


def _remember_session(con, nonce, expires, now):
    con.execute(
        "INSERT INTO sessions(nonce_hash, expires, last_seen, authenticated_at) VALUES(?,?,?,?)",
        (nonce_hash(nonce), expires, now, now),
    )


def begin_login(credentials_ok, ip, nonce, expires, previous=None, now=None):
    """A correct password creates a bounded pending challenge, never a session."""
    now = time.time() if now is None else now
    ip_hash = _ip_hash(ip)
    with store() as con:
        _prune(con, now)
        if _limited(con, ip_hash):
            return "limited"
        if not credentials_ok:
            _failure(con, ip_hash, now)
            return "invalid"
        if previous:
            con.execute("DELETE FROM challenges WHERE nonce_hash=?", (nonce_hash(previous),))
        if con.execute("SELECT COUNT(*) FROM challenges").fetchone()[0] >= GLOBAL_LIMIT:
            return "limited"
        con.execute("INSERT INTO challenges(nonce_hash,expires) VALUES(?,?)", (nonce_hash(nonce), expires))
        return "ok"


def verify_login(challenge, code, ip, nonce, expires, now=None):
    """Atomically consume the pending challenge and factor, then register a session."""
    now = time.time() if now is None else now
    ip_hash = _ip_hash(ip)
    with store() as con:
        _prune(con, now)
        digest = nonce_hash(challenge)
        pending = con.execute("SELECT attempts FROM challenges WHERE nonce_hash=?", (digest,)).fetchone()
        if not pending:
            return "expired"
        if _limited(con, ip_hash):
            return "limited"
        accepted = False
        secret, last_step = _check(con)
        step = matching_step(secret, code, now)
        if step is not None and step > last_step:
            con.execute("UPDATE configuration SET last_step=? WHERE id=1", (step,))
            accepted = True
        elif recovery := recovery_hash(code):
            accepted = con.execute("DELETE FROM recovery WHERE hash=?", (recovery,)).rowcount == 1
        if not accepted:
            _failure(con, ip_hash, now)
            if pending[0] + 1 >= CHALLENGE_LIMIT:
                con.execute("DELETE FROM challenges WHERE nonce_hash=?", (digest,))
                return "expired"
            con.execute("UPDATE challenges SET attempts=attempts+1 WHERE nonce_hash=?", (digest,))
            return "invalid"
        con.execute("UPDATE failures SET cleared=1 WHERE ip_hash=?", (ip_hash,))
        _remember_session(con, nonce, expires, now)
        con.execute("DELETE FROM challenges WHERE nonce_hash=?", (digest,))
        return "ok"


def cancel_login(nonce):
    with store() as con:
        con.execute("DELETE FROM challenges WHERE nonce_hash=?", (nonce_hash(nonce),))


def active_session(nonce, now=None):
    now = time.time() if now is None else now
    with store() as con:
        digest = nonce_hash(nonce)
        row = con.execute("SELECT expires,last_seen FROM sessions WHERE nonce_hash=?", (digest,)).fetchone()
        if not row:
            return False
        if row[0] <= now or row[1] <= now - IDLE_TTL:
            con.execute("DELETE FROM sessions WHERE nonce_hash=?", (digest,))
            return False
        con.execute("UPDATE sessions SET last_seen=? WHERE nonce_hash=?", (now, digest))
        return True


def revoke_session(nonce):
    with store() as con:
        con.execute("DELETE FROM sessions WHERE nonce_hash=?", (nonce_hash(nonce),))


def revoke_all(path=None):
    with store(path) as con:
        con.execute("DELETE FROM sessions")
        con.execute("DELETE FROM challenges")
        con.execute("DELETE FROM webauthn_challenges")


def _challenge_digest(challenge):
    return hashlib.sha256(challenge).hexdigest()


def _passkey_user(con):
    row = con.execute("SELECT passkey_user FROM configuration WHERE id=1").fetchone()
    if row and row[0]:
        return bytes(row[0])
    handle = secrets.token_bytes(32)
    con.execute("UPDATE configuration SET passkey_user=? WHERE id=1", (handle,))
    return handle


def _recent(con, session_nonce, now):
    row = con.execute(
        "SELECT authenticated_at, expires, last_seen FROM sessions WHERE nonce_hash=?",
        (nonce_hash(session_nonce),),
    ).fetchone()
    if not row or row["expires"] <= now or row["last_seen"] <= now - IDLE_TTL:
        return False
    return now - row["authenticated_at"] <= reauth_window()


def _webauthn_backlog(con, kind, holder):
    """True when a registration ceremony must be refused.

    Login ceremonies do not use this. They replace the caller's oldest
    pending challenge instead of rejecting, so one address cannot fill the
    table and then block everyone else.
    """
    total = con.execute("SELECT COUNT(*) FROM webauthn_challenges").fetchone()[0]
    if total >= GLOBAL_LIMIT:
        return True
    column = "ip_hash" if kind == "authentication" else "session_hash"
    own = con.execute(
        f"SELECT COUNT(*) FROM webauthn_challenges WHERE kind=? AND {column}=?",
        (kind, holder),
    ).fetchone()[0]
    return own >= IP_LIMIT


def _login_challenge_count(con, ip_hash=None):
    if ip_hash is None:
        row = con.execute(
            "SELECT COUNT(*) FROM webauthn_challenges WHERE kind='authentication'"
        ).fetchone()
    else:
        row = con.execute(
            "SELECT COUNT(*) FROM webauthn_challenges WHERE kind='authentication' AND ip_hash=?",
            (ip_hash,),
        ).fetchone()
    return row[0]


def _delete_oldest_login_challenges(con, count, ip_hash=None):
    """Drop the oldest pending login ceremonies.

    ``rowid`` is insertion order. Ceremonies started together share an
    expiry, so the expiry column cannot tell which one was replaced.
    """
    if count <= 0:
        return
    if ip_hash is None:
        rows = con.execute(
            "SELECT rowid FROM webauthn_challenges WHERE kind='authentication' ORDER BY rowid LIMIT ?",
            (count,),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT rowid FROM webauthn_challenges WHERE kind='authentication' AND ip_hash=? "
            "ORDER BY rowid LIMIT ?",
            (ip_hash, count),
        ).fetchall()
    for row in rows:
        con.execute("DELETE FROM webauthn_challenges WHERE rowid=?", (row[0],))


def _insert_webauthn_challenge(con, challenge, kind, expires, session_hash, ip_hash):
    con.execute(
        "INSERT INTO webauthn_challenges(challenge_hash, challenge, kind, expires, session_hash, ip_hash)"
        " VALUES(?,?,?,?,?,?)",
        (_challenge_digest(challenge), challenge, kind, expires, session_hash, ip_hash),
    )


def _consume_webauthn_challenge(con, challenge, kind, now, session_hash=None):
    """Delete the challenge immediately. A second presentation cannot succeed."""
    if not isinstance(challenge, (bytes, bytearray)) or not challenge:
        return None
    digest = _challenge_digest(bytes(challenge))
    row = con.execute(
        "SELECT challenge, kind, expires, session_hash FROM webauthn_challenges WHERE challenge_hash=?",
        (digest,),
    ).fetchone()
    if not row:
        return None
    con.execute("DELETE FROM webauthn_challenges WHERE challenge_hash=?", (digest,))
    stored = bytes(row["challenge"])
    if (
        row["kind"] != kind
        or row["expires"] <= now
        or len(stored) != len(challenge)
        or not hmac.compare_digest(stored, bytes(challenge))
    ):
        return None
    if kind == "registration" and row["session_hash"] != session_hash:
        return None
    return stored


def begin_passkey_authentication(ip, challenge, now=None):
    """Store one single-use login challenge for this client address.

    The address may hold ``PASSKEY_LOGIN_IP_LIMIT`` pending login challenges.
    At that cap, its oldest challenge is deleted and the new one is stored,
    so the caller is not rejected and cannot occupy the global pool. If other
    addresses already fill ``GLOBAL_LIMIT``, the oldest login challenge in
    the table is deleted to make a slot. Registration ceremonies are not
    deleted. Expired rows are purged before either decision. An address that
    is already over the failed-login limit still gets no challenge.
    """
    now = time.time() if now is None else now
    ip_hash = _ip_hash(ip)
    with store() as con:
        _prune(con, now)
        if _limited(con, ip_hash):
            return "limited"
        held = _login_challenge_count(con, ip_hash)
        own_drop = max(0, held - (PASSKEY_LOGIN_IP_LIMIT - 1))
        total = con.execute("SELECT COUNT(*) FROM webauthn_challenges").fetchone()[0]
        global_drop = max(0, total - own_drop - (GLOBAL_LIMIT - 1))
        # Refuse only when freeing a slot would require deleting a registration
        # ceremony. Do that check before deleting, so a refusal keeps every row.
        if global_drop > _login_challenge_count(con) - own_drop:
            return "limited"
        _delete_oldest_login_challenges(con, own_drop, ip_hash)
        _delete_oldest_login_challenges(con, global_drop)
        _insert_webauthn_challenge(con, challenge, "authentication", now + WEBAUTHN_CHALLENGE_TTL, None, ip_hash)
        return "ok"


def begin_passkey_registration(session_nonce, challenge, now=None):
    """Return the stable user handle and current credential ids, or a status string."""
    now = time.time() if now is None else now
    session_hash = nonce_hash(session_nonce)
    with store() as con:
        _prune(con, now)
        if not _recent(con, session_nonce, now):
            return "reauth"
        if con.execute("SELECT COUNT(*) FROM passkeys").fetchone()[0] >= MAX_PASSKEYS:
            return "full"
        if _webauthn_backlog(con, "registration", session_hash):
            return "limited"
        handle = _passkey_user(con)
        existing = [
            bytes(row[0]) for row in con.execute("SELECT credential_id FROM passkeys")
        ]
        _insert_webauthn_challenge(
            con, challenge, "registration", now + WEBAUTHN_CHALLENGE_TTL, session_hash, None
        )
        return ("ok", handle, existing)


def can_register_passkey(session_nonce, now=None):
    now = time.time() if now is None else now
    with store() as con:
        return _recent(con, session_nonce, now)


def list_passkeys():
    with store() as con:
        rows = con.execute(
            "SELECT credential_id, name, created, last_used FROM passkeys ORDER BY created, rowid"
        ).fetchall()
    return [
        {
            "id": bytes(row["credential_id"]),
            "name": row["name"],
            "created": row["created"],
            "last_used": row["last_used"],
        }
        for row in rows
    ]


def rename_passkey(credential_id, name):
    with store() as con:
        return con.execute(
            "UPDATE passkeys SET name=? WHERE credential_id=?", (name, credential_id)
        ).rowcount == 1


def delete_passkey(credential_id):
    with store() as con:
        return con.execute(
            "DELETE FROM passkeys WHERE credential_id=?", (credential_id,)
        ).rowcount == 1


def revoke_passkeys(path=None):
    """Remove every passkey. Sessions and the TOTP factor are left unchanged."""
    with store(path) as con:
        count = con.execute("SELECT COUNT(*) FROM passkeys").fetchone()[0]
        con.execute("DELETE FROM passkeys")
        con.execute("DELETE FROM webauthn_challenges")
        return count


def complete_passkey_registration(session_nonce, claimed_challenge, name, verify, now=None):
    """Consume the registration challenge, then store the verified credential.

    ``verify(stored_challenge)`` returns ``(credential_id, public_key, sign_count, aaguid)``
    or raises. It must not touch the auth store.
    """
    now = time.time() if now is None else now
    session_hash = nonce_hash(session_nonce)
    with store() as con:
        _prune(con, now)
        if not _recent(con, session_nonce, now):
            return "reauth"
        if con.execute("SELECT COUNT(*) FROM passkeys").fetchone()[0] >= MAX_PASSKEYS:
            return "full"
        stored = _consume_webauthn_challenge(
            con, claimed_challenge, "registration", now, session_hash
        )
        if stored is None:
            return "invalid"
        try:
            credential_id, public_key, sign_count, aaguid = verify(stored)
        except Exception:
            return "invalid"
        if (
            not isinstance(credential_id, (bytes, bytearray))
            or not 1 <= len(credential_id) <= 1023
            or not isinstance(public_key, (bytes, bytearray))
            or not 1 <= len(public_key) <= 2048
            or not isinstance(sign_count, int)
            or sign_count < 0
            or not isinstance(aaguid, str)
            or len(aaguid) > 80
        ):
            return "invalid"
        handle = _passkey_user(con)
        try:
            con.execute(
                "INSERT INTO passkeys(credential_id, public_key, sign_count, name, created, last_used, user_handle, aaguid)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (bytes(credential_id), bytes(public_key), sign_count, name, now, None, handle, aaguid),
            )
        except sqlite3.IntegrityError:
            return "invalid"
        return "ok"


def _handles_match(stored, presented):
    if not isinstance(presented, (bytes, bytearray)) or not stored:
        return False
    stored = bytes(stored)
    presented = bytes(presented)
    if len(stored) != len(presented):
        return False
    return hmac.compare_digest(stored, presented)


def complete_passkey_authentication(ip, claimed_challenge, credential_id, user_handle, verify, nonce, expires, now=None):
    """Consume the login challenge, reject sign-counter regression, then open a session.

    ``verify(public_key, sign_count, stored_challenge)`` returns the new sign
    count or raises. It must not touch the auth store. Every unsuccessful
    attempt shares the password-login failure counters.
    """
    now = time.time() if now is None else now
    ip_hash = _ip_hash(ip)
    with store() as con:
        _prune(con, now)
        if _limited(con, ip_hash):
            return "limited"
        stored = _consume_webauthn_challenge(con, claimed_challenge, "authentication", now)
        credential = None
        if stored is not None and isinstance(credential_id, (bytes, bytearray)):
            credential = con.execute(
                "SELECT public_key, sign_count, user_handle FROM passkeys WHERE credential_id=?",
                (bytes(credential_id),),
            ).fetchone()
        if stored is None or credential is None or not _handles_match(credential["user_handle"], user_handle):
            _failure(con, ip_hash, now)
            return "invalid"
        try:
            new_count = verify(bytes(credential["public_key"]), credential["sign_count"], stored)
        except Exception:
            _failure(con, ip_hash, now)
            return "invalid"
        # A lower counter means the authenticator state went backwards (a cloned
        # or restored credential). Refuse it and keep the stored counter.
        if not isinstance(new_count, int) or new_count < credential["sign_count"]:
            _failure(con, ip_hash, now)
            return "invalid"
        con.execute(
            "UPDATE passkeys SET sign_count=?, last_used=? WHERE credential_id=?",
            (new_count, now, bytes(credential_id)),
        )
        con.execute("UPDATE failures SET cleared=1 WHERE ip_hash=?", (ip_hash,))
        _remember_session(con, nonce, expires, now)
        return "ok"

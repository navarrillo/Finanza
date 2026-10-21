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

import pyotp

APPLICATION_ID = 0x464E4155
SESSION_TTL = 3600
IDLE_TTL = 900
WINDOW = 300
IP_LIMIT = 8
GLOBAL_LIMIT = 30
CHALLENGE_TTL = 300
CHALLENGE_LIMIT = 5


class AuthStateError(Exception):
    pass


def mfa_required():
    value = os.environ.get("MFA_REQUIRED", "true").lower()
    if value not in {"true", "false"}:
        raise AuthStateError("MFA_REQUIRED must be true or false.")
    return value == "true"


def state_path(path=None):
    target = Path(path or os.environ.get("AUTH_DB_PATH", "private/auth/auth.db")).expanduser().resolve()
    finance = Path(os.environ.get("DB_PATH", "data/finanzas.db")).expanduser().resolve()
    if target == finance or (target.exists() and finance.exists() and target.samefile(finance)):
        raise AuthStateError("Authentication state must be separate from DB_PATH.")
    return target


def _check(con):
    if (con.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or con.execute("PRAGMA user_version").fetchone()[0] not in {1, 2}):
        raise AuthStateError("Not a Finanza authentication store.")
    row = con.execute("SELECT secret, last_step FROM configuration WHERE id=1").fetchone()
    if not row or not re.fullmatch(r"[A-Z2-7]{32}", row[0]):
        raise AuthStateError("Authentication setup is incomplete.")
    return row


@contextmanager
def store(path=None):
    con = None
    try:
        # mode=rw refuses to silently create an empty store at runtime.
        con = sqlite3.connect(state_path(path).as_uri() + "?mode=rw", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        _check(con)
        con.execute("BEGIN IMMEDIATE")
        if con.execute("PRAGMA user_version").fetchone()[0] == 1:
            # Migrate only our separate auth store; never the financial database.
            con.execute("CREATE TABLE challenges(nonce_hash TEXT PRIMARY KEY, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)")
            con.execute("PRAGMA user_version=2")
        yield con
        con.commit()
    except (sqlite3.Error, OSError) as exc:
        raise AuthStateError("Authentication state is unavailable.") from exc
    finally:
        if con is not None:
            con.close()


def validate_runtime():
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
            con.executemany("INSERT INTO recovery(hash) VALUES (?)", [(recovery_hash(c),) for c in codes])
        return codes
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        con = sqlite3.connect(target)
        try:
            con.executescript(f"""
                PRAGMA application_id={APPLICATION_ID};
                PRAGMA user_version=2;
                CREATE TABLE configuration(id INTEGER PRIMARY KEY CHECK(id=1), secret TEXT NOT NULL, last_step INTEGER NOT NULL);
                CREATE TABLE recovery(hash TEXT PRIMARY KEY);
                CREATE TABLE sessions(nonce_hash TEXT PRIMARY KEY, expires REAL NOT NULL, last_seen REAL NOT NULL);
                CREATE TABLE challenges(nonce_hash TEXT PRIMARY KEY, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE failures(ip_hash TEXT NOT NULL, created REAL NOT NULL, cleared INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX failure_dates ON failures(created);
            """)
            with con:
                con.execute("INSERT INTO configuration VALUES(1,?,?)", (secret, step))
                con.executemany("INSERT INTO recovery(hash) VALUES (?)", [(recovery_hash(c),) for c in codes])
        finally:
            con.close()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return codes


def nonce_hash(nonce):
    return hashlib.sha256(nonce.encode()).hexdigest()


def _prune(con, now):
    con.execute("DELETE FROM failures WHERE created<=?", (now - WINDOW,))
    con.execute("DELETE FROM sessions WHERE expires<=? OR last_seen<=?", (now, now - IDLE_TTL))
    con.execute("DELETE FROM challenges WHERE expires<=?", (now,))


def _limited(con, ip_hash):
    total, address = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(ip_hash=? AND cleared=0),0) FROM failures", (ip_hash,)
    ).fetchone()
    return total >= GLOBAL_LIMIT or address >= IP_LIMIT


def _failure(con, ip_hash, now):
    con.execute("INSERT INTO failures(ip_hash,created) VALUES(?,?)", (ip_hash, now))


def begin_login(credentials_ok, ip, nonce, expires, previous=None, now=None):
    """A correct password creates a bounded pending challenge, never a session."""
    now = time.time() if now is None else now
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()
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
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()
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
        con.execute("INSERT INTO sessions VALUES(?,?,?)", (nonce_hash(nonce), expires, now))
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

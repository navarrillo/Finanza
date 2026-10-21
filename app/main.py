import base64
import csv
import hashlib
import hmac
import io
import ipaddress
import json
import os
import secrets
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

from .analytics import dashboard, transactions
from .db import DataError, connect
from .i18n import current_language, request_language, error_body
from . import auth_store

STATIC = Path(__file__).parent / "static"
COOKIE = "finanzas_session"
# Failed logins in a five-minute window. The per-address cap stops one client.
# The global cap still applies when that client rotates addresses, and when
# every visitor shares the proxy address because forwarded headers are not trusted.
WINDOW_SECONDS = 300
PER_IP_MAX_ATTEMPTS = 8
GLOBAL_MAX_ATTEMPTS = 30
TOKEN_TTL = auth_store.SESSION_TTL
# Below this, a value is a repeated or tiny alphabet. Documented generators
# (scripts/setup.py: token_urlsafe(24) and token_hex(32)) stay above it.
MIN_DISTINCT_CHARACTERS = 8
# Published in README.md, docs/README.es.md and .env.example. A length check
# alone accepts them, and the repository is public.
DOCUMENTED_PLACEHOLDERS = (
    "your-own-unique-long-password",
    "your-own-random-secret-at-least-32-characters",
    "elige-una-clave-larga-y-privada",
    "pega-aqui-el-secreto-aleatorio-generado",
    "una-clave-privada-de-al-menos-12-caracteres",
    "un-secreto-aleatorio-de-al-menos-32-caracteres",
)

attempts = defaultdict(list)
global_attempts = []
# Local demo revocations live in memory. Production MFA also deletes the
# persistent session, so a restart cannot reactivate a copied cookie.
revoked = {}
state_lock = threading.Lock()


def weak_secret(value, minimum):
    """True when a credential is short, low-entropy, or a documented placeholder."""
    secret = value.strip()
    if len(secret) < minimum or len(set(secret)) < MIN_DISTINCT_CHARACTERS:
        return True
    folded = secret.casefold()
    if any(placeholder in folded for placeholder in DOCUMENTED_PLACEHOLDERS):
        return True
    # "abcabcabc" or "0123456789" repeated. Cap the block size; random tokens
    # from the documented generators are not exact repetitions.
    limit = min(len(secret) // 2, 64)
    for size in range(1, limit + 1):
        if len(secret) % size == 0 and secret[:size] * (len(secret) // size) == secret:
            return True
    return False


def unsafe_forwarded_allow_ips(value):
    """True when any peer could supply the client address used for login limits.

    Uvicorn trusts X-Forwarded-For only from FORWARDED_ALLOW_IPS. The value "*"
    or a zero-prefix network (0.0.0.0/0, ::/0) makes every peer trusted, and
    Uvicorn then keeps the leftmost forwarded address, which the client chose.
    """
    entries = [item.strip() for item in value.split(",") if item.strip()]
    if any(item == "*" for item in entries):
        return True
    for item in entries:
        if "/" not in item:
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            continue
        if network.prefixlen == 0:
            return True
    return False


def require_runtime_config():
    password = os.environ.get("APP_PASSWORD", "")
    secret = os.environ.get("SESSION_SECRET", "")
    if weak_secret(password, 12):
        raise RuntimeError(
            "APP_PASSWORD must be your own high-entropy password of at least "
            "12 characters, not a placeholder from the README or .env.example. "
            "Generate credentials with: python3 scripts/setup.py"
        )
    if weak_secret(secret, 32):
        raise RuntimeError(
            "SESSION_SECRET must be your own high-entropy secret of at least "
            "32 characters, not a placeholder from the README or .env.example. "
            "Generate credentials with: python3 scripts/setup.py"
        )
    forwarded = os.environ.get("FORWARDED_ALLOW_IPS")
    if forwarded is not None and unsafe_forwarded_allow_ips(forwarded):
        raise RuntimeError(
            "FORWARDED_ALLOW_IPS must be the reverse proxy address the app sees, "
            "never '*' or a network that contains every address. Trusting every "
            "peer lets clients spoof X-Forwarded-For and bypass the per-address "
            "login limit."
        )
    auth_store.validate_runtime()


@asynccontextmanager
async def lifespan(app):
    require_runtime_config()
    yield


app = FastAPI(
    title="Finanzas personales",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def cookie_secure():
    return os.environ.get("COOKIE_SECURE", "true").lower() == "true"


def signing_key():
    # The password is part of the key, so replacing APP_PASSWORD invalidates
    # every cookie, including ones copied earlier and ones still presented
    # after a restart.
    digest = hashlib.sha256(json.dumps([
        os.environ["APP_PASSWORD"], os.environ.get("APP_USER", "admin"),
        auth_store.mfa_required(),
    ]).encode()).digest()
    return hmac.new(os.environ["SESSION_SECRET"].encode(), digest, hashlib.sha256).digest()


def make_token(data=None):
    payload = base64.urlsafe_b64encode(
        json.dumps(
            data or {"exp": int(time.time()) + TOKEN_TTL, "nonce": secrets.token_hex(16)}
        ).encode()
    ).decode()
    return (
        payload
        + "."
        + hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    )


def _purge_revoked(now):
    for nonce, exp in list(revoked.items()):
        if exp <= now:
            del revoked[nonce]


def auth(request: Request):
    try:
        payload, signature = request.cookies.get(COOKIE, "").rsplit(".", 1)
        expected = hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(payload))
        if data["exp"] <= time.time() or not isinstance(data.get("nonce"), str):
            raise ValueError
        with state_lock:
            _purge_revoked(time.time())
            if data["nonce"] in revoked:
                raise ValueError
        if auth_store.mfa_required() and not auth_store.active_session(data["nonce"]):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, {"key": "errors.auth"})
    return data


def same_origin(request):
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != (
        os.environ.get("PUBLIC_ORIGIN")
        or f"{request.url.scheme}://{request.url.netloc}"
    ).rstrip("/"):
        raise HTTPException(403, {"key": "errors.origin"})
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, {"key": "errors.origin"})


@app.middleware("http")
async def privacy_headers(request, call_next):
    token = current_language.set(request_language(request))
    try:
        response = await call_next(request)
    finally:
        current_language.reset(token)
    response.headers["Content-Language"] = request_language(request)
    # HSTS only on the HTTPS configuration. Sending it on local HTTP would
    # make browsers refuse that host. One year, without includeSubDomains or
    # preload, so a proxy can forward this single policy or replace it.
    if cookie_secure():
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        }
    )
    return response


@app.exception_handler(DataError)
async def data_error(request, exc):
    return JSONResponse(error_body(exc.key, exc.params), status_code=422)


@app.exception_handler(auth_store.AuthStateError)
async def auth_state_error(request, exc):
    # Missing/corrupt/read-only state never falls back to password-only access.
    return JSONResponse(error_body("errors.authState"), status_code=503)


@app.exception_handler(sqlite3.Error)
async def sqlite_error(request, exc):
    return JSONResponse(error_body("errors.database"), status_code=503)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    detail = exc.detail if isinstance(exc.detail, dict) else {"key": "errors.request"}
    return JSONResponse(
        error_body(detail["key"], detail.get("params")),
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(error_body("errors.request"), status_code=422)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def health():
    # No financial information and no filesystem paths in the public health check.
    return {"status": "ok"}


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=512)
    code: str = Field(default="", max_length=64)


@app.get("/api/auth/config")
def auth_config():
    # Only public policy, never enrollment keys, recovery codes or state paths.
    return {"mfa_required": auth_store.mfa_required()}


def client_address(request: Request):
    """Address Uvicorn stored as the client.

    With --proxy-headers, Uvicorn rewrites it from X-Forwarded-For only when
    the TCP peer is listed in FORWARDED_ALLOW_IPS, and it keeps the nearest
    untrusted address (proxies append, so a client-supplied prefix is not
    used). This function must not read X-Forwarded-For itself: a direct
    caller would then choose their own limit bucket.
    """
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _prune_attempts(now):
    for key in list(attempts):
        attempts[key] = [stamp for stamp in attempts[key] if now - stamp < WINDOW_SECONDS]
        if not attempts[key]:
            del attempts[key]
    global_attempts[:] = [
        stamp for stamp in global_attempts if now - stamp < WINDOW_SECONDS
    ]


def _limited(ip, now):
    _prune_attempts(now)
    return (
        len(attempts.get(ip, ())) >= PER_IP_MAX_ATTEMPTS
        or len(global_attempts) >= GLOBAL_MAX_ATTEMPTS
    )


@app.post("/api/login")
def login(body: Login, request: Request):
    same_origin(request)
    ip = client_address(request)
    now = time.monotonic()
    with state_lock:
        if _limited(ip, now):
            raise HTTPException(429, {"key": "errors.rateLimit"})
    good_user = secrets.compare_digest(
        body.username.encode(), os.environ.get("APP_USER", "admin").encode()
    )
    # Fixed-size digests avoid length-dependent password comparisons.
    given = hashlib.sha256(body.password.encode()).digest()
    actual = hashlib.sha256(os.environ["APP_PASSWORD"].encode()).digest()
    credentials_ok = good_user and secrets.compare_digest(given, actual)
    data = {"exp": int(time.time()) + TOKEN_TTL, "nonce": secrets.token_hex(16)}
    if auth_store.mfa_required():
        result = auth_store.authenticate(credentials_ok, body.code.strip(), ip, data["nonce"], data["exp"])
        if result == "limited":
            raise HTTPException(429, {"key": "errors.rateLimit"})
        if result != "ok":
            # Same response for wrong user, password, TOTP or recovery code.
            raise HTTPException(401, {"key": "errors.credentials"})
    elif not credentials_ok:
        with state_lock:
            if _limited(ip, now):
                raise HTTPException(429, {"key": "errors.rateLimit"})
            attempts[ip].append(now)
            global_attempts.append(now)
        raise HTTPException(401, {"key": "errors.credentials"})
    with state_lock:
        # A successful sign-in clears this address. It is not a failed attempt,
        # so it does not consume the global budget.
        attempts.pop(ip, None)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE,
        make_token(data),
        max_age=TOKEN_TTL,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(),
    )
    return response


@app.post("/api/logout")
def logout(request: Request, session=Depends(auth)):
    same_origin(request)
    if auth_store.mfa_required():
        auth_store.revoke_session(session["nonce"])
    now = time.time()
    with state_lock:
        _purge_revoked(now)
        revoked[session["nonce"]] = session["exp"]
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        COOKIE,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(),
    )
    return response


@app.get("/api/session")
def session(_=Depends(auth)):
    return {"username": os.environ.get("APP_USER", "admin")}


@app.get("/api/dashboard")
def get_dashboard(
    currency: Literal["SEK", "EUR"] = "SEK",
    month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    _=Depends(auth),
):
    if month:
        valid_date(month + "-01")
    with connect() as con:
        return dashboard(con, currency, month)


def valid_date(value):
    if value:
        try:
            date.fromisoformat(value)
        except ValueError:
            raise HTTPException(422, {"key": "errors.invalidDate"})
    return value


def filters(
    search: str = Query("", max_length=200),
    start: str = "",
    end: str = "",
    bank: str = Query("", max_length=200),
    category: str = Query("", max_length=200),
    min_amount: float | None = None,
    max_amount: float | None = None,
):
    valid_date(start)
    valid_date(end)
    if start and end and start > end:
        raise HTTPException(422, {"key": "errors.dateRange"})
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(422, {"key": "errors.amountRange"})
    return dict(
        search=search,
        start=start,
        end=end,
        bank=bank,
        category=category,
        min_amount=min_amount,
        max_amount=max_amount,
    )


@app.get("/api/transactions")
def get_transactions(
    currency: Literal["SEK", "EUR"] = "SEK",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    f=Depends(filters),
    _=Depends(auth),
):
    with connect() as con:
        return transactions(con, currency, f, page, page_size)


@app.get("/api/export.csv")
def export(
    currency: Literal["SEK", "EUR"] = "SEK", f=Depends(filters), _=Depends(auth)
):
    with connect() as con:
        result = transactions(con, currency, f, export=True)
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    fields = [
        "id",
        "split_part",
        "booking_date",
        "bank",
        "account_name",
        "description",
        "counterparty",
        "category",
        "subcategory",
        "amount",
        "currency",
        "converted_amount",
        "is_internal_transfer",
        "is_one_off",
        "is_pass_through",
    ]
    writer.writerow(fields + ["consolidated_currency"])

    def safe(value):
        if isinstance(value, str) and value.lstrip().startswith(
            ("=", "+", "-", "@", "\t", "\r")
        ):
            return "'" + value
        return value

    for row in result["rows"]:
        writer.writerow([safe(row.get(k, "")) for k in fields] + [currency])
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="transactions.csv"'},
    )

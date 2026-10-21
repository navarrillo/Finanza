import base64
import csv
import hashlib
import hmac
import io
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

STATIC = Path(__file__).parent / "static"
COOKIE = "finanzas_session"
attempts = defaultdict(list)
attempt_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    if len(os.environ.get("APP_PASSWORD", "")) < 12:
        raise RuntimeError(
            "Configura APP_PASSWORD con al menos 12 caracteres antes de iniciar la aplicación."
        )
    if len(os.environ.get("SESSION_SECRET", "")) < 32:
        raise RuntimeError("Configura SESSION_SECRET con al menos 32 caracteres.")
    yield


app = FastAPI(
    title="Finanzas personales",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def signing_key():
    return os.environ["SESSION_SECRET"].encode()


def make_token():
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"exp": int(time.time()) + 28800, "nonce": secrets.token_hex(16)}
        ).encode()
    ).decode()
    return (
        payload
        + "."
        + hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    )


def auth(request: Request):
    try:
        payload, signature = request.cookies.get(COOKIE, "").rsplit(".", 1)
        expected = hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        if json.loads(base64.urlsafe_b64decode(payload))["exp"] <= time.time():
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, {"key": "errors.auth"})


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
    response.headers.update(
        {
            "Cache-Control": "no-store",
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


@app.post("/api/login")
def login(body: Login, request: Request):
    same_origin(request)
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with attempt_lock:
        for key in list(attempts):
            attempts[key] = [t for t in attempts[key] if now - t < 300]
            if not attempts[key]:
                del attempts[key]
        if len(attempts[ip]) >= 8:
            raise HTTPException(429, {"key": "errors.rateLimit"})
        attempts[ip].append(now)
    good_user = secrets.compare_digest(
        body.username.encode(), os.environ.get("APP_USER", "admin").encode()
    )
    # Fixed-size digests avoid length-dependent password comparisons.
    given = hashlib.sha256(body.password.encode()).digest()
    actual = hashlib.sha256(os.environ["APP_PASSWORD"].encode()).digest()
    if not (good_user and secrets.compare_digest(given, actual)):
        raise HTTPException(401, {"key": "errors.credentials"})
    with attempt_lock:
        attempts.pop(ip, None)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE,
        make_token(),
        max_age=28800,
        httponly=True,
        samesite="strict",
        secure=os.environ.get("COOKIE_SECURE", "true").lower() == "true",
    )
    return response


@app.post("/api/logout")
def logout(request: Request, _=Depends(auth)):
    same_origin(request)
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
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

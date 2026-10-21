"""API messages use the same local catalogs as the browser. No external services."""

import json
import re
from contextvars import ContextVar
from pathlib import Path

LOCALES = Path(__file__).parent / "static" / "locales"
MANIFEST = json.loads((LOCALES / "manifest.json").read_text(encoding="utf-8"))
DEFAULT = MANIFEST["default"]
CATALOGS = {
    item["code"]: json.loads(
        (LOCALES / f'{item["code"]}.json').read_text(encoding="utf-8")
    )
    for item in MANIFEST["languages"]
}
current_language = ContextVar("finanza_language", default=DEFAULT)


def match_language(value):
    value = (value or "").lower().replace("_", "-")
    exact = next((code for code in CATALOGS if code.lower() == value), None)
    return exact or next(
        (code for code in CATALOGS if code.lower() == value.split("-")[0]), None
    )


def request_language(request):
    explicit = match_language(
        request.headers.get("x-finanza-language")
    ) or match_language(request.query_params.get("lang"))
    if explicit:
        return explicit
    candidates = []
    for part in request.headers.get("accept-language", "").split(","):
        sections = part.strip().split(";")
        try:
            quality = float(
                next(
                    (
                        item.strip()[2:]
                        for item in sections[1:]
                        if item.strip().startswith("q=")
                    ),
                    "1",
                )
            )
        except ValueError:
            continue
        if 0 < quality <= 1:
            candidates.append((quality, sections[0]))
    for _, candidate in sorted(candidates, key=lambda item: -item[0]):
        if code := match_language(candidate):
            return code
    return DEFAULT


def translate(key, params=None, language=None):
    params = params or {}
    code = language or current_language.get()
    value = CATALOGS.get(code, {}).get(key, CATALOGS[DEFAULT].get(key, key))
    # API errors are simple strings. Plurals are selected with Intl in the UI.
    if isinstance(value, dict):
        value = value.get(
            "one" if params.get("count") == 1 else "other", value.get("other", key)
        )
    return re.sub(r"\{(\w+)\}", lambda m: str(params.get(m[1], m[0])), value)


def error_body(key, params=None):
    return {"detail": translate(key, params), "error_key": key, "params": params or {}}

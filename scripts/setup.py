#!/usr/bin/env python3
"""Set up local data and unique credentials, without overwriting existing files."""

import argparse
import os
import secrets
from pathlib import Path

try:  # Both `python scripts/setup.py` and `python -m scripts.setup`.
    from .create_demo import create_demo
    from .init_db import create_empty_database
except ImportError:
    from create_demo import create_demo
    from init_db import create_empty_database

ROOT = Path(__file__).resolve().parents[1]


def setup(root=ROOT, demo=False):
    root = Path(root)
    filename = "demo.db" if demo else "finanzas.db"
    database = root / "data" / filename
    if not database.exists():
        if demo:
            create_demo(database)
        else:
            create_empty_database(database)
        print(f"Database ready: data/{filename}")
    else:
        print(f"Kept existing database: data/{filename}")
    env_path = root / ".env"
    (root / "private" / "auth").mkdir(parents=True, exist_ok=True, mode=0o700)
    # O_EXCL avoids clobbering credentials; restrictive permissions from creation.
    try:
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("Kept existing .env. Check its DB_PATH/DB_FILENAME and DEMO_MODE.")
    else:
        password = secrets.token_urlsafe(24)
        secret = secrets.token_hex(32)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(
                f"APP_USER=admin\nAPP_PASSWORD={password}\nSESSION_SECRET={secret}\n"
                f"COOKIE_SECURE=false\nDB_PATH=./data/{filename}\n"
                f"DB_DIRECTORY=./data\nDB_FILENAME={filename}\n"
                f"DEMO_MODE={'true' if demo else 'false'}\n"
                f"MFA_REQUIRED={'false' if demo else 'true'}\n"
                "AUTH_DB_PATH=./private/auth/auth.db\nAUTH_DIRECTORY=./private/auth\n"
            )
        print("Created .env with unique credentials (owner read/write only).")
    print("Read APP_USER and APP_PASSWORD in your local .env to sign in.")
    if not demo:
        print("Before starting: install requirements and run python3 scripts/manage_auth.py setup")
    print("Next: docker compose up -d --build")
    print("For deployment over HTTPS, set COOKIE_SECURE=true and PUBLIC_ORIGIN. Passkeys use that origin.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Use synthetic demo data.")
    setup(demo=parser.parse_args().demo)

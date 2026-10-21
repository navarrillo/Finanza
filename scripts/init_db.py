#!/usr/bin/env python3
"""Create a NEW importer database. This CLI is never imported by the dashboard."""

import argparse
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "finanzas_esquema.sql"


def create_empty_database(output):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to overwrite or migrate any existing file, including an empty one.
    with path.open("xb"):
        pass
    con = None
    try:
        con = sqlite3.connect(path)
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        con.commit()
    except Exception:
        if con is not None:
            con.close()
        path.unlink()  # Only the new file exclusively created above.
        raise
    else:
        con.close()
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/finanzas.db")
    path = create_empty_database(parser.parse_args().output)
    print(f"Created {path}: canonical schema and categories, no financial records.")

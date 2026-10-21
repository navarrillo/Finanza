#!/usr/bin/env python3
"""Standalone fixture generator. NEVER imported by the runtime. Refuses overwrite."""

import argparse
import math
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA = (Path(__file__).resolve().parents[1] / "finanzas_esquema.sql").read_text()


def create_demo(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation, even if a real DB happens to have this filename.
    with path.open("xb"):
        pass
    con = sqlite3.connect(path)
    rng = random.Random(73)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO institutions VALUES(?,?,?,?)",
        [
            (1, "demo_eur", "Aurora Demo", "ES"),
            (2, "demo_sek", "Norte Demo", "SE"),
            (3, "demo_invest", "Horizonte Demo", "ES"),
        ],
    )
    con.executemany(
        "INSERT INTO accounts(id,institution_id,account_type,name,iban,currency,is_joint) VALUES(?,?,?,?,?,?,?)",
        [
            (1, 1, "checking", "Cuenta diaria EUR", "ES-DEMO-0001", "EUR", 0),
            (2, 2, "checking", "Cuenta diaria SEK", "SE-DEMO-0002", "SEK", 0),
            (3, 2, "checking", "Cuenta común", "SE-DEMO-0003", "SEK", 1),
            (4, 3, "investment", "Cuenta de inversión", "ES-DEMO-0004", "EUR", 0),
        ],
    )
    con.execute(
        "INSERT INTO categories(id,name,parent_id,kind) VALUES(22,'Compra semanal',1,'expense')"
    )
    category_ids = {
        1: 16,
        2: 4,
        3: 1,
        4: 3,
        5: 2,
        6: 10,
        7: 8,
        8: 21,
        9: 17,
        10: 15,
        11: 14,
        12: 18,
        13: 20,
        14: 22,
    }
    start, end = date(2022, 12, 30), date(2026, 10, 31)
    day = start
    while day <= end:
        if day.weekday() < 5:
            rate = round(11.15 + 0.35 * math.sin((day - start).days / 80), 5)
            con.execute(
                "INSERT INTO fx_rates(rate_date,base,quote,rate) VALUES(?,?,?,?)",
                (day.isoformat(), "EUR", "SEK", rate),
            )
        day += timedelta(days=1)
    events = []

    def add(
        account,
        y,
        m,
        d,
        text,
        amount,
        cat,
        sub="",
        internal=0,
        oneoff=0,
        passthrough=0,
        split=None,
    ):
        events.append(
            (
                f"{y}-{m:02d}-{d:02d}",
                account,
                text,
                round(amount, 2),
                cat,
                sub,
                internal,
                oneoff,
                passthrough,
                split,
            )
        )

    for y in range(2023, 2027):
        for m in range(1, 13):
            if (y, m) > (2026, 10):
                continue
            sweden = (y, m) >= (2025, 3)
            personal = 2 if sweden else 1
            scale = 11.2 if sweden else 1
            add(
                personal,
                y,
                m,
                25,
                "Nómina · Demo AB" if sweden else "Nómina · Empresa ficticia",
                (3250 + rng.randint(-120, 220)) * scale,
                1,
                "Salario",
            )
            if sweden:
                add(
                    2,
                    y,
                    m,
                    2,
                    "Aportación a la cuenta común",
                    -8400,
                    8,
                    "Vida en común",
                )
                add(3, y, m, 2, "Aportación personal", 8400, 11)
                add(3, y, m, 2, "Aportación de pareja", 8400, 11)
                add(3, y, m, 3, "Alquiler · Cuenta común", -11200, 2, "Alquiler")
                add(
                    3,
                    y,
                    m,
                    12,
                    "ICA · Compra compartida",
                    -3200 - rng.randint(0, 600),
                    3,
                    "Supermercado",
                )
                add(3, y, m, 18, "Electricidad y fibra", -950, 2, "Suministros")
                add(1, y, m, 5, "Seguro y gastos España", -95, 2, "Seguros")
            else:
                add(1, y, m, 2, "Alquiler", -780, 2, "Alquiler")
            for d in (4, 11, 19, 27):
                add(
                    personal,
                    y,
                    m,
                    d,
                    "ICA Nära" if sweden else "Mercadona",
                    -rng.uniform(35, 85) * scale,
                    14,
                    "Supermercado",
                )
            for d in (7, 15, 23):
                add(
                    personal,
                    y,
                    m,
                    d,
                    rng.choice(["Café Linné", "Restaurante · Centro", "Cine Fyris"]),
                    -rng.uniform(20, 65) * scale,
                    5,
                    "Restaurantes",
                )
            add(
                personal,
                y,
                m,
                8,
                "UL · Abono transporte" if sweden else "Abono transporte",
                -85 * scale,
                4,
                "Transporte público",
            )
            add(
                personal,
                y,
                m,
                10,
                "Compra compartida · anticipo",
                -80 * scale,
                6,
                "Compra compartida",
                passthrough=1,
            )
            add(
                personal,
                y,
                m,
                14,
                "Devolución compra compartida",
                40 * scale,
                10,
                "Compra compartida",
                passthrough=1,
            )
            add(
                personal,
                y,
                m,
                16,
                "Farmacia",
                -rng.uniform(12, 45) * scale,
                7,
                "Farmacia",
            )
            add(
                personal,
                y,
                m,
                20,
                "Compra de hogar y alimentación",
                -120 * scale,
                6,
                "Hogar",
                split=[(-80 * scale, 6, "Hogar"), (-40 * scale, 14, "Supermercado")],
            )
            add(
                personal,
                y,
                m,
                22,
                "Pago con tarjeta · pendiente",
                -rng.uniform(5, 28) * scale,
                9,
            )
            add(personal, y, m, 26, "Aportación a inversión", -250 * scale, 12)
            if sweden:
                add(2, y, m, 28, "Transferencia a cuenta EUR", -1120, 11, internal=1)
                add(
                    1,
                    y,
                    m,
                    28,
                    "Transferencia desde cuenta SEK",
                    100,
                    11,
                    internal=1,
                )
            if (y, m) == (2026, 5):
                add(
                    2,
                    y,
                    m,
                    9,
                    "Compra coche · gasto puntual",
                    -95000,
                    4,
                    "Coche",
                    oneoff=1,
                )
            if (y, m) == (2026, 8):
                add(
                    2, y, m, 9, "Depósito de alquiler recuperable", -12000, 13, "Fianza"
                )
    balances = {1: 15000.0, 2: 125000.0, 3: 22000.0, 4: 0.0}
    for tid, (
        day,
        account,
        text,
        amount,
        cat,
        sub,
        internal,
        oneoff,
        passthrough,
        split,
    ) in enumerate(sorted(events, key=lambda e: (e[0], e[1])), 1):
        currency = "EUR" if account in (1, 4) else "SEK"
        balances[account] += amount
        con.execute(
            "INSERT INTO transactions(id,account_id,booking_date,description,counterparty,txn_type,amount,currency,balance_after,dedup_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                tid,
                account,
                day,
                text,
                text.split(" · ")[0],
                "transfer" if cat == 11 else "card",
                amount,
                currency,
                round(balances[account], 2),
                f"demo-{tid}",
            ),
        )
        con.execute(
            "INSERT INTO txn_classification(transaction_id,category_id,subcategory,is_internal_transfer,is_one_off,is_pass_through,method) VALUES(?,?,?,?,?,?,?)",
            (tid, category_ids[cat], sub, internal, oneoff, passthrough, "demo"),
        )
        if split:
            for part, (a, c, s) in enumerate(split, 1):
                con.execute(
                    "INSERT INTO txn_splits(transaction_id,part_no,amount,category_id,subcategory,is_one_off,is_pass_through) VALUES(?,?,?,?,?,?,?)",
                    (tid, part, round(a, 2), category_ids[c], s, 0, 0),
                )
    for account in (1, 2, 3):
        con.execute(
            "INSERT INTO balance_snapshots(account_id,as_of,balance,currency) VALUES(?,?,?,?)",
            (
                account,
                "2026-10-31",
                round(balances[account], 2),
                "EUR" if account == 1 else "SEK",
            ),
        )
    con.commit()
    con.close()
    print(
        f"Datos ficticios creados en {path}. Ninguna base existente se ha modificado."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/demo.db")
    create_demo(parser.parse_args().output)

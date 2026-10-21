import os
from collections import defaultdict
from datetime import date
from .db import DataError, FX, optional_rows, rows


def months_ending(month, length=12):
    year, m = map(int, month.split("-"))
    total = year * 12 + m - 1
    return [
        f"{(total-i)//12:04d}-{(total-i)%12+1:02d}" for i in reversed(range(length))
    ]


def measures(t):
    """Daily weights ONLY for FX conversion; monthly view remains authoritative.

    A reconciliation check prevents guessing if the upstream view uses another
    convention. Never sum splits' balance_after or count split lines as movements.
    """
    if t["is_internal_transfer"]:
        return {}
    a = float(t["amount"])
    kind = t["category_kind"]
    outflow = -a if a < 0 and kind not in ("investment", "asset") else 0
    expense = outflow if not t["is_one_off"] else 0
    reimbursement = (
        a if a > 0 and (kind == "reimbursement" or t["is_pass_through"]) else 0
    )
    return {
        "income": (
            a
            if a > 0
            and kind not in ("reimbursement", "investment", "asset")
            and not t["is_pass_through"]
            else 0
        ),
        "expenses": expense,
        "reimbursements": reimbursement,
        "one_off_expenses": outflow if t["is_one_off"] else 0,
        "invested": -a if kind == "investment" else 0,
        "to_assets": -a if kind == "asset" else 0,
    }


def monthly(con, transactions, fx, end):
    weights = defaultdict(list)
    for t in transactions:
        for key, value in measures(t).items():
            if value:
                weights[(t["month"], t["currency"], key)].append(
                    (value, t["booking_date"])
                )
    native = rows(
        con,
        "SELECT * FROM v_monthly_cashflow WHERE month >= ? AND month <= ? ORDER BY month",
        (months_ending(end, 13)[0], end),
    )
    result = {}
    for item in native:
        m = item["month"]
        acc = result.setdefault(
            m,
            dict(
                month=m,
                income=0,
                reimbursements=0,
                expenses=0,
                net_expenses=0,
                one_off_expenses=0,
                invested=0,
                to_assets=0,
            ),
        )
        converted = {}
        for key in (
            "income",
            "reimbursements",
            "expenses",
            "one_off_expenses",
            "invested",
            "to_assets",
        ):
            value = float(item[key] or 0)
            if item["currency"] == fx.target:
                converted[key] = value
            else:
                parts = weights[(m, item["currency"], key)]
                native_sum = sum(a for a, _ in parts)
                if abs(native_sum - value) > 0.02:
                    raise DataError(
                        "errors.viewMismatch",
                        metric=key,
                        month=m,
                        currency=item["currency"],
                    )
                converted[key] = sum(
                    fx.amount(a, item["currency"], d) for a, d in parts
                )
            acc[key] += converted[key]
        # Preserve the authoritative net definition; verify before daily conversion.
        if item["currency"] == fx.target:
            acc["net_expenses"] += float(item["net_expenses"] or 0)
        else:
            if (
                abs(
                    float(item["net_expenses"] or 0)
                    - (
                        float(item["expenses"] or 0)
                        - float(item["reimbursements"] or 0)
                    )
                )
                > 0.02
            ):
                raise DataError("errors.netMismatch")
            acc["net_expenses"] += converted["expenses"] - converted["reimbursements"]
    series = []
    for m in months_ending(end, 13):
        item = result.get(
            m,
            dict(
                month=m,
                **{
                    k: 0
                    for k in (
                        "income",
                        "reimbursements",
                        "expenses",
                        "net_expenses",
                        "one_off_expenses",
                        "invested",
                        "to_assets",
                    )
                },
            ),
        )
        item["has_data"] = m in result
        item["savings"] = item["income"] - item["net_expenses"]
        item["savings_rate"] = (
            item["savings"] / item["income"] * 100 if item["income"] else None
        )
        item["regular_expenses"] = item["net_expenses"]
        item["total_net_expenses"] = item["net_expenses"] + item["one_off_expenses"]
        item["savings_after_one_off"] = item["savings"] - item["one_off_expenses"]
        series.append(item)
    return series


def balance_data(con, fx, end):
    accounts = rows(
        con,
        """SELECT a.id, a.name, a.currency, a.is_joint, i.name AS bank
                            FROM accounts a JOIN institutions i ON i.id=a.institution_id""",
    )
    cutoff = months_ending(end, 1)[0] + "-31"
    # Base transactions are used exclusively for balances: one real movement per id.
    history = rows(
        con,
        """SELECT id, account_id, booking_date AS as_of, balance_after AS balance, currency
                           FROM transactions WHERE balance_after IS NOT NULL AND booking_date <= ?
                           ORDER BY booking_date, id""",
        (cutoff,),
    )
    snaps = rows(
        con,
        "SELECT account_id, as_of, balance, currency FROM balance_snapshots WHERE as_of <= ? ORDER BY as_of",
        (cutoff,),
    )
    by_account = defaultdict(dict)
    for t in history + snaps:  # Explicit snapshots take precedence on the same date.
        by_account[t["account_id"]][t["as_of"]] = t
    output = []
    for a in accounts:
        points = sorted(by_account[a["id"]].values(), key=lambda r: r["as_of"])
        latest = points[-1] if points else None
        output.append(
            {
                **a,
                "points": points,
                "balance": latest["balance"] if latest else None,
                "as_of": latest["as_of"] if latest else None,
                "converted_balance": (
                    fx.amount(latest["balance"], latest["currency"], latest["as_of"])
                    if latest
                    else None
                ),
            }
        )
    return output


def dashboard(con, currency, month=None):
    all_tx = rows(
        con,
        "SELECT * FROM v_transactions ORDER BY booking_date DESC, id DESC, split_part",
    )
    available = sorted({t["month"] for t in all_tx}, reverse=True)
    end = month or (available[0] if available else date.today().strftime("%Y-%m"))
    fx = FX(con, currency)
    start = months_ending(end, 13)[0]
    tx = [t for t in all_tx if start <= t["month"] <= end]
    series = monthly(con, tx, fx, end)
    category_map = {}
    for t in tx:
        values = measures(t)
        net = (
            values.get("expenses", 0)
            + values.get("one_off_expenses", 0)
            - values.get("reimbursements", 0)
        )
        if not net:
            continue
        name = t["parent_category"] or t["category"] or "Otros"
        key = (name, t["category"] or "Otros", t["subcategory"] or "", t["bank"])
        entry = category_map.setdefault(
            key,
            {
                "name": name,
                "category": t["category"] or "Otros",
                "subcategory": t["subcategory"] or "",
                "bank": t["bank"],
                "months": {},
                "one_off": {},
            },
        )
        value = fx.amount(net, t["currency"], t["booking_date"])
        entry["months"][t["month"]] = entry["months"].get(t["month"], 0) + value
        if values.get("one_off_expenses", 0):
            entry["one_off"][t["month"]] = entry["one_off"].get(t["month"], 0) + value
    joint = rows(
        con, "SELECT * FROM v_joint_transactions ORDER BY booking_date DESC, id DESC"
    )
    joint = [t for t in joint if t["booking_date"][:7] <= end]
    joint_month = [t for t in joint if t["booking_date"][:7] == end]
    joint_in = sum(
        fx.amount(t["amount"], t["currency"], t["booking_date"])
        for t in joint_month
        if t["amount"] > 0
    )
    joint_out = sum(
        fx.amount(-t["amount"], t["currency"], t["booking_date"])
        for t in joint_month
        if t["amount"] < 0
    )
    personal_contributions = sum(
        fx.amount(-t["amount"], t["currency"], t["booking_date"])
        for t in tx
        if t["month"] == end and t["amount"] < 0 and t["category"] == "Gastos comunes"
    )
    for t in joint[:200]:
        t["converted_amount"] = fx.amount(t["amount"], t["currency"], t["booking_date"])
    metrics = (
        "income",
        "reimbursements",
        "expenses",
        "net_expenses",
        "savings",
        "invested",
        "one_off_expenses",
        "to_assets",
        "regular_expenses",
        "total_net_expenses",
        "savings_after_one_off",
    )
    window = series[-12:]
    totals = {k: sum(s[k] for s in window) for k in metrics}
    totals["savings_rate"] = (
        totals["savings"] / totals["income"] * 100 if totals["income"] else None
    )
    observed = [s for s in window if s["has_data"]]
    average = {
        k: sum(s[k] for s in observed) / len(observed) if observed else 0
        for k in metrics
    }
    # The supplied view ALREADY excludes one-offs from expenses/net_expenses.
    average["savings_rate"] = (
        (average["savings"] / average["income"] * 100) if average["income"] else None
    )
    return {
        "demo": os.environ.get("DEMO_MODE", "false").lower() == "true",
        "currency": currency,
        "month": end,
        "months": available,
        "banks": sorted({t["bank"] for t in all_tx}),
        "institutions": rows(
            con, "SELECT id, code, name FROM institutions ORDER BY name, id"
        ),
        "categories": sorted({t["category"] or "Otros" for t in all_tx}),
        "series": series[-12:],
        "current": series[-1],
        "previous": series[-2],
        "totals": totals,
        "average": average,
        "observed_months": len(observed),
        "category_rows": list(category_map.values()),
        "accounts": balance_data(con, fx, end),
        "transaction_count": con.execute(
            "SELECT COUNT(DISTINCT id) FROM v_transactions WHERE month=?", (end,)
        ).fetchone()[0],
        "joint": {
            "income": joint_in,
            "expenses": joint_out,
            "contributions": personal_contributions,
            "transactions": joint[:200],
            "total_lines": len(joint),
            "total_transactions": con.execute(
                "SELECT COUNT(DISTINCT id) FROM v_joint_transactions WHERE booking_date <= ?",
                (end + "-31",),
            ).fetchone()[0],
        },
        "optional": {
            name: optional_rows(con, name)
            for name in (
                "budgets",
                "documents",
                "instruments",
                "holding_snapshots",
                "recurring_series",
            )
        },
    }


def transactions(con, currency, filters, page=1, page_size=25, export=False):
    clauses, params = [], []
    for column, op, key in [
        ("booking_date", ">=", "start"),
        ("booking_date", "<=", "end"),
        ("bank", "=", "bank"),
        ("COALESCE(category, 'Otros')", "=", "category"),
        ("amount", ">=", "min_amount"),
        ("amount", "<=", "max_amount"),
    ]:
        if filters.get(key) is not None and filters[key] != "":
            clauses.append(f"{column} {op} ?")
            params.append(filters[key])
    if filters.get("search"):
        clauses.append(
            "(description LIKE ? ESCAPE '\\' OR counterparty LIKE ? ESCAPE '\\')"
        )
        term = (
            filters["search"]
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        params += [f"%{term}%"] * 2
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    counts = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT id) FROM v_transactions" + where, params
    ).fetchone()
    sql = (
        "SELECT * FROM v_transactions"
        + where
        + " ORDER BY booking_date DESC, id DESC, split_part"
    )
    if not export:
        sql += " LIMIT ? OFFSET ?"
        params += [page_size, (page - 1) * page_size]
    data = rows(con, sql, params)
    fx = FX(con, currency)
    for t in data:
        t["category"] = t["category"] or "Otros"
        t["converted_amount"] = fx.amount(t["amount"], t["currency"], t["booking_date"])
    return {
        "rows": data,
        "total_lines": counts[0],
        "total_transactions": counts[1],
        "page": page,
        "page_size": page_size,
    }

"""Reads a PayPal activity export (Activity > Statements > Download, CSV) and finds what
needs to stop: subscriptions, automatic billing agreements and open invoices.

Deterministic on purpose: a family must be able to trust that nothing is cancelled because a
model guessed. The rules are explained back to them line by line.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median

AUTOMATIC_TYPES = re.compile(r"subscription|pre-?approved|billing agreement|recurring|automatic", re.I)
INVOICE_TYPES = re.compile(r"invoice", re.I)
PERSONAL_TYPES = re.compile(r"(personal|mobile|general) payment|payment sent to|transfer", re.I)
MANUAL_TYPES = re.compile(r"express checkout|website payment|general payment|payment sent|mobile payment|purchase", re.I)


def _f(x: str) -> float:
    try:
        return float(str(x).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _slash_order(values: list[str]) -> str:
    """PayPal exports use MM/DD/YYYY in some regions and DD/MM/YYYY in others (India included).
    Decide per file: if any first part is above 12 it must be the day."""
    firsts, seconds = [], []
    for v in values:
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})/\d{4}", v or "")
        if m:
            firsts.append(int(m.group(1))); seconds.append(int(m.group(2)))
    if any(f > 12 for f in firsts):
        return "dmy"
    return "mdy"


def _date(s: str, order: str = "mdy") -> date | None:
    s = (s or "").strip()
    slash = ("%m/%d/%Y", "%d/%m/%Y") if order == "mdy" else ("%d/%m/%Y", "%m/%d/%Y")
    for fmt in ("%Y-%m-%d", *slash, "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse(csv_bytes: bytes) -> list[dict]:
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    raw = [{(k or "").strip().strip('"'): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))]
    order = _slash_order([r.get("Date", "") for r in raw])
    rows = []
    for r in raw:
        d = _date(r.get("Date", ""), order)
        if not d:
            continue
        rows.append({
            "date": d, "name": r.get("Name", ""), "type": r.get("Type", ""), "status": r.get("Status", ""),
            "currency": r.get("Currency", "USD"), "gross": _f(r.get("Gross", "0")),
            "from": r.get("From Email Address", ""), "to": r.get("To Email Address", ""),
            "txn": r.get("Transaction ID", ""), "subscription": r.get("Subscription Number", ""),
            "invoice": r.get("Invoice Number", ""), "item": r.get("Item Title", ""),
        })
    return rows


CANONICAL = [(7, "weekly"), (14, "fortnightly"), (30.44, "monthly"), (91.31, "quarterly"), (182.6, "half-yearly"), (365.25, "yearly")]


def estimate_period(dates: list[date], titles: list[str]) -> tuple[float | None, str, float]:
    """Robust period estimate: median inter-arrival gap, snapped to a canonical billing period when
    within 15%. Confidence falls as the gaps scatter (MAD / median). With one charge, fall back to
    the item title ('annual', 'monthly')."""
    if len(dates) >= 2:
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        if gaps:
            g = median(gaps)
            mad = median([abs(x - g) for x in gaps])
            conf = max(0.0, 1 - mad / g) if g else 0.0
            for days, label in CANONICAL:
                if abs(g - days) / days <= 0.15:
                    return days, label, round(conf, 2)
            return float(g), f"every ~{int(g)} days", round(conf, 2)
    text = " ".join(titles).lower()
    if re.search(r"annual|yearly|12[- ]month|per year|/yr", text):
        return 365.25, "yearly", 0.6
    if re.search(r"monthly|per month|/mo", text):
        return 30.44, "monthly", 0.6
    return None, "single", 0.0


def price_note(paid: list[dict]) -> str | None:
    amts = [(i["date"], abs(i["gross"])) for i in paid]
    for (d0, a0), (d1, a1) in zip(amts, amts[1:]):
        if abs(a1 - a0) > 0.005:
            return f"price went from {a0:.2f} to {a1:.2f} on {d1.isoformat()}"
    return None


def analyse(rows: list[dict], date_of_death: str) -> dict:
    dod = date.fromisoformat(date_of_death[:10])
    holder_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["gross"] < 0 and r["from"]:
            holder_counts[r["from"].lower()] += 1
    holder_email = max(holder_counts, key=holder_counts.get) if holder_counts else ""

    by_party: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["gross"] < 0 or INVOICE_TYPES.search(r["type"]):
            key = (r["to"] or r["name"]).lower()
            by_party[key].append(r)

    findings, manual_after = [], []
    for key, items in by_party.items():
        items.sort(key=lambda x: x["date"])
        name = items[-1]["name"]
        currency = items[-1]["currency"]
        subs = sorted({i["subscription"] for i in items if i["subscription"]})
        invoices = [i for i in items if INVOICE_TYPES.search(i["type"]) and re.search(r"unpaid|pending|sent|due", i["status"], re.I)]
        paid = [i for i in items if i["gross"] < 0 and re.search(r"complete", i["status"], re.I)]
        automatic = [i for i in paid if AUTOMATIC_TYPES.search(i["type"]) or i["subscription"]]
        amounts = [abs(i["gross"]) for i in paid]
        period, cadence, conf = estimate_period([i["date"] for i in paid], [i["item"] for i in items])
        regular = period is not None and conf >= 0.7 and len(paid) >= 3
        # tolerate price rises: same amount, or within 25% when the timing is clearly periodic
        same_amount = len(amounts) >= 3 and (max(amounts) - min(amounts)) <= (0.25 if regular else 0.05) * max(amounts)

        if automatic or subs:
            n = len(automatic)
            kind, reason = "SUBSCRIPTION", (f"{n} automatic {cadence} charge{'s' if n != 1 else ''}"
                                            + (f", subscription {subs[0]}" if subs else ""))
        elif same_amount and regular:
            kind, reason = "SUBSCRIPTION", f"same amount charged {cadence}, {len(paid)} times: looks like a saved billing agreement"
        elif invoices:
            kind, reason = "INVOICE", f"open invoice {invoices[-1]['invoice'] or ''} sent to this account".strip()
        elif PERSONAL_TYPES.search(items[-1]["type"]) and not re.search(r"(shop|store|inc|ltd|llc|club|plus|\.com)", name, re.I):
            kind, reason = "PERSONAL", "payment to a person, not a business: left alone"
        else:
            kind, reason = "ONE_OFF", "one-off purchase: nothing to cancel"

        after = [{"date": i["date"].isoformat(), "amount": abs(i["gross"]), "transaction_id": i["txn"]}
                 for i in paid if i["date"] > dod and (kind == "SUBSCRIPTION")]
        for i in paid:
            if i["date"] > dod and not (AUTOMATIC_TYPES.search(i["type"]) or i["subscription"]) and kind != "SUBSCRIPTION" \
                    and MANUAL_TYPES.search(i["type"]):
                manual_after.append({"name": i["name"], "date": i["date"].isoformat(), "amount": abs(i["gross"])})

        next_charge, yearly = None, 0.0
        if kind == "SUBSCRIPTION" and period and paid:
            nxt = paid[-1]["date"] + timedelta(days=round(period))
            today = date.today()
            while nxt <= today:                     # it would already have been charged; keep rolling forward
                nxt += timedelta(days=round(period))
            next_charge = nxt.isoformat()
            yearly = round(amounts[-1] * 365.25 / period, 2)
        findings.append({
            "period_days": period, "next_charge": next_charge, "yearly_cost": yearly,
            "price_note": price_note(paid) if kind == "SUBSCRIPTION" else None,
            "merchant_name": name, "merchant_email": items[-1]["to"], "kind": kind,
            "recurring": kind == "SUBSCRIPTION", "cadence": cadence,
            # subscriptions: what it costs now (after any price rise); others: the typical amount
            "typical_amount": (round(amounts[-1], 2) if kind == "SUBSCRIPTION" else round(median(amounts), 2)) if amounts
            else (abs(invoices[-1]["gross"]) if invoices else 0.0),
            "currency": currency,
            "references_json": {"subscription_ids": subs, "transaction_ids": [i["txn"] for i in paid],
                                "invoice_ids": [i["invoice"] for i in invoices if i["invoice"]]},
            "charges_after_death": after, "owed_back": round(sum(a["amount"] for a in after), 2),
            "reason": reason, "selected": 1 if kind in ("SUBSCRIPTION", "INVOICE") else 0,
            "last_date": items[-1]["date"].isoformat(),
        })

    order = {"SUBSCRIPTION": 0, "INVOICE": 1, "ONE_OFF": 2, "PERSONAL": 3}
    findings.sort(key=lambda f: (order[f["kind"]], f["next_charge"] or "9999", f["merchant_name"]))  # soonest charge first
    holder_name = ""  # PayPal exports name the counterparty, not the account holder
    return {
        "account_holder_email": holder_email,
        "account_holder": holder_name,
        "findings": findings,
        "manual_payments_after_death": manual_after,
        "rows": len(rows),
        "period": [min(r["date"] for r in rows).isoformat(), max(r["date"] for r in rows).isoformat()] if rows else None,
    }

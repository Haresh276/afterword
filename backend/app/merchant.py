"""The merchant's agent. It receives a signed Afterword notice, checks it against the merchant's
own PayPal records, and proposes exactly three kinds of action:

    cancel_subscription  (toolkit)    for each active subscription of the deceased
    refund_capture       (REST)       for each charge captured after the date of death
    cancel_sent_invoice  (toolkit)    for each unpaid invoice addressed to them

Nothing runs until a person approves, or the merchant's own written auto-close rule does
(auto-close only applies when every check passes and the refund is under the merchant's cap).
"""
from __future__ import annotations

import os
import random
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import certificate, db, letters, notices
from . import paypal_gateway as pp

MIN_SCORE = float(os.getenv("AFTERWORD_MIN_SCORE", "0.8"))
AUTOPILOT_DELAY = os.getenv("AFTERWORD_AUTOPILOT_DELAY", "2-6")


def _delay() -> float:
    lo, _, hi = AUTOPILOT_DELAY.partition("-")
    return random.uniform(float(lo), float(hi or lo))


# ------------------------------------------------------------------------- receive & review
def receive(notice_id: str, background: bool = True) -> dict:
    n = db.get("notices", notice_id)
    m = db.get("merchants", n["merchant_id"])
    sig_ok = notices.verify(n["payload"], n["signature"])
    db.update("notices", notice_id, {"status": "RECEIVED", "received_at": db.now()})
    db.audit(f"merchant:{m['name']}", "RECEIVED notice", notice_id, {"signature_valid": sig_ok})
    _set_finding(n, "RECEIVED")
    review = build_review(n, m, sig_ok)
    db.update("notices", notice_id, {"status": "IN_REVIEW" if sig_ok else "REJECTED", "review": review})
    if not sig_ok:
        db.audit(f"merchant:{m['name']}", "REJECTED notice: bad signature", notice_id)
        return db.get("notices", notice_id)
    if m["auto_close"] and review["auto_eligible"]:
        if background:
            threading.Thread(target=_autopilot, args=(notice_id,), daemon=True).start()
        else:
            _autopilot(notice_id, delay=False)
    return db.get("notices", notice_id)


def build_review(n: dict, m: dict, sig_ok: bool) -> dict:
    body = n["payload"]
    dod = body["deceased"]["date_of_death"]
    who = body["deceased"]
    actor = f"merchant_agent:{m['name']}"
    base_checks = [
        {"check": "Notice signature verified with the Afterword hub key", "passed": sig_ok, "detail": notices.PROTOCOL},
        {"check": f"Certificate verification score at least {int(MIN_SCORE * 100)}%",
         "passed": body["verification"]["score"] >= MIN_SCORE,
         "detail": f"{int(body['verification']['score'] * 100)}% ({body['verification']['checks_passed']}/{body['verification']['checks_total']} checks)"},
    ]
    matches, proposals = [], []
    start = datetime.fromisoformat(dod).replace(tzinfo=timezone.utc) + timedelta(days=1)
    end = datetime.now(timezone.utc) + timedelta(minutes=5)

    # ---- subscriptions: match identity against PayPal's own record, then find post-death captures
    for sid in body["references"].get("subscription_ids", []):
        rec = db.query("SELECT * FROM merchant_customers WHERE merchant_id=? AND paypal_subscription_id=?", (m["id"], sid))
        try:
            details = pp.call("show_subscription_details", {"subscription_id": sid}, actor=actor, ref=n["id"])
        except Exception as exc:  # noqa: BLE001
            matches.append({"type": "subscription", "id": sid, "found": False, "detail": str(exc)[:200]})
            continue
        sub = details.get("subscriber") or {}
        pp_name = " ".join(filter(None, [(sub.get("name") or {}).get("given_name"), (sub.get("name") or {}).get("surname")]))
        pp_email = (sub.get("email_address") or "").lower()
        name_ok = certificate.names_match(pp_name, who["name"])
        email_ok = pp_email == (who.get("paypal_email") or "").lower()
        status = details.get("status")
        ident = [{"check": "Subscriber name on PayPal matches the certificate", "passed": name_ok, "detail": f"PayPal: {pp_name or '?'}"},
                 {"check": "Subscriber email on PayPal matches the notice", "passed": email_ok, "detail": pp_email or "?"},
                 {"check": "Subscription belongs to this merchant", "passed": bool(rec), "detail": rec[0]["plan_name"] if rec else "not in our records"}]
        txs = pp.call("list_subscription_transactions", {"subscription_id": sid, "start_time": pp.iso(start), "end_time": pp.iso(end)},
                      actor=actor, ref=n["id"]).get("transactions", [])
        caps = [t for t in txs if t.get("status") == "COMPLETED"]
        matches.append({"type": "subscription", "id": sid, "found": True, "status": status, "paypal_name": pp_name,
                        "paypal_email": pp_email, "plan": rec[0]["plan_name"] if rec else None,
                        "charges_after_death": [{"id": t["id"], "time": t["time"],
                                                 "amount": float(t["amount_with_breakdown"]["gross_amount"]["value"]),
                                                 "currency": t["amount_with_breakdown"]["gross_amount"]["currency_code"]} for t in caps]})
        if status in ("ACTIVE", "SUSPENDED"):
            proposals.append({"tool": "cancel_subscription", "amount": 0.0,
                              "params": {"subscription_id": sid, "payload": {"reason": f"Account holder died on {dod}. Afterword notice {n['id']}."}},
                              "rationale": f"Stop future charges on {rec[0]['plan_name'] if rec else sid}",
                              "checks": base_checks + ident})
        for t in caps:
            amt = float(t["amount_with_breakdown"]["gross_amount"]["value"])
            cur = t["amount_with_breakdown"]["gross_amount"]["currency_code"]
            after = t["time"][:10] > dod
            proposals.append({"tool": "refund_capture", "amount": amt,
                              "params": {"capture_id": t["id"], "amount": f"{amt:.2f}", "currency": cur,
                                         "note": "Refund of a charge taken after the account holder died. We're sorry for your loss."},
                              "rationale": f"Return {cur} {amt:.2f} charged on {t['time'][:10]}, after the date of death",
                              "checks": base_checks + ident + [
                                  {"check": "Charge was captured after the date of death", "passed": after, "detail": f"{t['time'][:10]} > {dod}"}]})

    # ---- invoices
    for iid in body["references"].get("invoice_ids", []):
        rec = db.query("SELECT * FROM merchant_invoices WHERE merchant_id=? AND paypal_invoice_id=?", (m["id"], iid))
        try:
            inv = pp.call("get_invoice", {"invoice_id": iid}, actor=actor, ref=n["id"])
        except Exception as exc:  # noqa: BLE001
            matches.append({"type": "invoice", "id": iid, "found": False, "detail": str(exc)[:200]})
            continue
        to = ((inv.get("primary_recipients") or [{}])[0].get("billing_info") or {}).get("email_address", "").lower()
        matches.append({"type": "invoice", "id": iid, "found": True, "status": inv.get("status"), "to": to})
        if inv.get("status") in ("UNPAID", "SENT", "PARTIALLY_PAID", "SCHEDULED"):
            proposals.append({"tool": "cancel_sent_invoice", "amount": 0.0,
                              "params": {"invoice_id": iid, "note": "Cancelled because the account holder has died. Nothing is owed.",
                                         "send_to_recipient": False},
                              "rationale": f"Cancel unpaid invoice {iid}",
                              "checks": base_checks + [
                                  {"check": "Invoice is addressed to the deceased's PayPal email", "passed": to == (who.get('paypal_email') or '').lower(), "detail": to},
                                  {"check": "Invoice belongs to this merchant", "passed": bool(rec), "detail": rec[0]["description"] if rec else "not in our records"}]})

    refund_total = round(sum(p["amount"] for p in proposals if p["tool"] == "refund_capture"), 2)
    all_pass = bool(proposals) and all(c["passed"] for p in proposals for c in p["checks"])
    auto_eligible = all_pass and refund_total <= float(m["auto_refund_cap"] or 0)

    for p in proposals:
        pp.propose(notice_id=n["id"], merchant_id=m["id"], tool=p["tool"], params=p["params"], rationale=p["rationale"],
                   proposed_by=actor, checks=p["checks"], amount=p["amount"])
    return {"matches": matches, "refund_total": refund_total, "all_checks_pass": all_pass,
            "auto_eligible": auto_eligible, "auto_rule": (f"Auto-close when every check passes and refunds total ≤ ${m['auto_refund_cap']:.0f}"
                                                          if m["auto_close"] else "Every case is reviewed by a person"),
            "proposed": len(proposals)}


# ------------------------------------------------------------------------- decisions
def _autopilot(notice_id: str, delay: bool = True) -> None:
    m = db.get("merchants", db.get("notices", notice_id)["merchant_id"])
    for a in db.query("SELECT * FROM pending_actions WHERE notice_id=? AND status='PENDING' ORDER BY created_at", (notice_id,)):
        if delay:
            time.sleep(_delay())
        try:
            pp.approve(a["id"], f"policy:{m['name']} auto-close rule")
        except Exception as exc:  # noqa: BLE001
            db.audit("system", "autopilot stopped", notice_id, {"action": a["id"], "error": str(exc)})
            return
    finalize(notice_id)


def approve_all(notice_id: str, who: str = "human") -> dict:
    errors = []
    for a in db.query("SELECT * FROM pending_actions WHERE notice_id=? AND status='PENDING' ORDER BY created_at", (notice_id,)):
        try:
            pp.approve(a["id"], who)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{a['tool']}: {exc}")
    out = finalize(notice_id)
    if errors:
        out["errors"] = errors
    return out


def finalize(notice_id: str) -> dict:
    n = db.get("notices", notice_id)
    acts = db.query("SELECT * FROM pending_actions WHERE notice_id=?", (notice_id,))
    if any(a["status"] == "PENDING" for a in acts):
        return n
    done = [a for a in acts if a["status"] == "EXECUTED"]
    outcome = {
        "cancelled": [a["params"]["subscription_id"] for a in done if a["tool"] == "cancel_subscription"],
        "refunded": [{"capture_id": a["params"]["capture_id"], "amount": a["amount"]} for a in done if a["tool"] == "refund_capture"],
        "invoices_cancelled": [a["params"]["invoice_id"] for a in done if a["tool"] == "cancel_sent_invoice"],
    }
    outcome["refunded_total"] = round(sum(r["amount"] for r in outcome["refunded"]), 2)
    m = db.get("merchants", n["merchant_id"])
    status = "CLOSED" if done and all(a["status"] == "EXECUTED" for a in acts) else ("ACTIONED" if done else "IN_REVIEW")
    vals: dict = {"status": status, "outcome": outcome, "closed_at": db.now() if status == "CLOSED" else None}
    if status == "CLOSED":
        # no more "we miss you" or "payment failed" emails addressed to the person who died
        refs = n["payload"]["references"]
        for sid in refs.get("subscription_ids", []):
            for cust in db.query("SELECT id FROM merchant_customers WHERE merchant_id=? AND paypal_subscription_id=?", (m["id"], sid)):
                db.update("merchant_customers", cust["id"], {"status": "DECEASED", "emails_suppressed": 1})
        outcome["emails_stopped"] = True
        vals["reply"] = letters.merchant_reply(m, n, outcome)
        receipt = {"protocol": notices.RECEIPT_PROTOCOL, "notice_id": notice_id, "merchant": {"id": m["id"], "name": m["name"]},
                   "outcome": outcome, "closed_at": vals["closed_at"],
                   "approved_by": sorted({a["decided_by"] for a in done if a.get("decided_by")})}
        vals.update(outcome=outcome, receipt=receipt, receipt_sig=notices.sign(receipt, party=m["id"]))
    db.update("notices", notice_id, vals)
    if status == "CLOSED":
        db.audit(f"merchant:{m['name']}", "CLOSED notice + signed receipt", notice_id, outcome)
        confirm(notice_id)
        f = db.query("SELECT * FROM findings WHERE notice_id=?", (notice_id,))
        if f:
            db.update("findings", f[0]["id"], {"status": "CLOSED"})
        _maybe_close_case(n["case_id"])
    return db.get("notices", notice_id)


def confirm(notice_id: str) -> dict:
    """The hub doesn't take a merchant's word for it: it re-reads PayPal's own records."""
    n = db.get("notices", notice_id)
    out = n.get("outcome") or {}
    items = []
    for sid in out.get("cancelled", []):
        st = pp.call("show_subscription_details", {"subscription_id": sid}, actor="hub_verifier", ref=notice_id).get("status")
        items.append({"what": f"subscription {sid}", "paypal_status": st, "ok": st == "CANCELLED"})
    for r in out.get("refunded", []):
        st = pp.call("get_capture", {"capture_id": r["capture_id"]}, actor="hub_verifier", ref=notice_id).get("status")
        items.append({"what": f"charge {r['capture_id']} (${r['amount']:.2f})", "paypal_status": st,
                      "ok": st in ("REFUNDED", "PARTIALLY_REFUNDED")})
    for iid in out.get("invoices_cancelled", []):
        st = pp.call("get_invoice", {"invoice_id": iid}, actor="hub_verifier", ref=notice_id).get("status")
        items.append({"what": f"invoice {iid}", "paypal_status": st, "ok": st == "CANCELLED"})
    conf = {"all_confirmed": bool(items) and all(i["ok"] for i in items), "items": items, "checked_at": db.now(),
            "receipt_signature_valid": notices.verify(n["receipt"], n["receipt_sig"], party=n["merchant_id"]) if n.get("receipt") else False}
    db.update("notices", notice_id, {"confirmation": conf})
    db.audit("hub_verifier", "CONFIRMED on PayPal" if conf["all_confirmed"] else "COULD NOT CONFIRM on PayPal", notice_id, conf)
    return conf


def _set_finding(n: dict, status: str) -> None:
    f = db.query("SELECT id FROM findings WHERE notice_id=?", (n["id"],))
    if f:
        db.update("findings", f[0]["id"], {"status": status})


def _maybe_close_case(case_id: str) -> None:
    fs = db.query("SELECT * FROM findings WHERE case_id=? AND notice_id IS NOT NULL", (case_id,))
    if fs and all(f["status"] == "CLOSED" for f in fs):
        db.update("cases", case_id, {"status": "CLOSED"})
        db.audit("system", "ALL NOTICES CLOSED", case_id)

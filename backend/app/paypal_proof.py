"""PayPal sandbox proof, two ways, so a judge never has to take our word for it.

1. live_check(): when PAYPAL_CLIENT_ID/SECRET are set on the server, a button makes real PayPal sandbox
   calls right now (OAuth, Agent Toolkit create_product / create_subscription_plan /
   create_subscription / show_subscription_details, then deactivates the plan) and shows PayPal's
   own ids and debug ids. Safe to press any number of times: nothing is charged, nothing is shared.
2. evidence(): the recorded result of scripts/sandbox_run.py, a full Afterword run against real
   sandbox subscriptions (subscribe as the buyer -> pause -> cancel -> refund -> re-read PayPal),
   committed to evidence/sandbox_run.json.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import db
from . import paypal_gateway as pp

EVIDENCE = Path(__file__).resolve().parents[2] / "evidence" / "sandbox_run.json"
_lock = threading.Lock()
_last = {"at": 0.0, "result": None}
COOLDOWN = 20  # seconds between live checks, so a refresh-happy visitor can't hammer the sandbox


def _step(steps: list, title: str, fn, show):
    t0 = time.perf_counter()
    try:
        out = fn()
        steps.append({"step": title, "ok": True, "ms": round((time.perf_counter() - t0) * 1000), **show(out),
                      "debug_id": (out or {}).get("_paypal_debug_id", "") if isinstance(out, dict) else ""})
        return out
    except Exception as exc:  # noqa: BLE001
        steps.append({"step": title, "ok": False, "ms": round((time.perf_counter() - t0) * 1000), "error": str(exc)[:300]})
        raise


def live_check() -> dict:
    if not pp.HAS_KEYS:
        return {"available": False, "reason": "This server has no PayPal sandbox keys. The recorded run below is the proof."}
    with _lock:
        if time.time() - _last["at"] < COOLDOWN and _last["result"]:
            return {**_last["result"], "cached": True}
        steps: list[dict] = []
        t = pp.live()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            _step(steps, "Sign in to PayPal sandbox (OAuth 2.0 client credentials)",
                  lambda: {"token": bool(t.api._paypal_client.get_access_token())}, lambda o: {"detail": "access token issued"})
            prod = _step(steps, "Agent Toolkit: create_product", lambda: t.run("create_product", {
                "name": f"Afterword live check {stamp}", "type": "SERVICE"}), lambda o: {"id": o.get("id"), "detail": o.get("name", "")})
            plan = _step(steps, "Agent Toolkit: create_subscription_plan", lambda: t.run("create_subscription_plan", {
                "product_id": prod["id"], "name": "Live check monthly",
                "billing_cycles": [{"frequency": {"interval_unit": "MONTH", "interval_count": 1}, "tenure_type": "REGULAR", "sequence": 1,
                                    "total_cycles": 0, "pricing_scheme": {"fixed_price": {"currency_code": "USD", "value": "1.00"}}}],
                "payment_preferences": {"auto_bill_outstanding": True, "payment_failure_threshold": 1}}),
                lambda o: {"id": o.get("id"), "detail": o.get("status", "")})
            sub = _step(steps, "Agent Toolkit: create_subscription", lambda: t.run("create_subscription", {
                "plan_id": plan["id"], "subscriber": {"name": {"given_name": "Eleanor", "surname": "Hayes"}, "email_address": "eleanor.hayes@example.com"},
                "application_context": {"brand_name": "Afterword live check", "user_action": "SUBSCRIBE_NOW",
                                        "return_url": "https://example.com/return", "cancel_url": "https://example.com/cancel"}}),
                lambda o: {"id": o.get("id"), "detail": o.get("status", "")})
            _step(steps, "Agent Toolkit: show_subscription_details", lambda: t.run("show_subscription_details", {"subscription_id": sub["id"]}),
                  lambda o: {"id": o.get("id"), "detail": f"status {o.get('status')}, subscriber "
                             f"{((o.get('subscriber') or {}).get('name') or {}).get('given_name', '')} {((o.get('subscriber') or {}).get('name') or {}).get('surname', '')}"})
            _step(steps, "REST: deactivate the plan again (clean up)", lambda: t._rest("POST", f"/v1/billing/plans/{plan['id']}/deactivate"),
                  lambda o: {"id": plan["id"], "detail": "plan inactive"})
            ok = True
        except Exception:  # noqa: BLE001
            ok = False
        res = {"available": True, "ok": ok, "at": db.now(), "steps": steps, "environment": "api-m.sandbox.paypal.com"}
        db.audit("hub", "PAYPAL live check " + ("passed" if ok else "failed"), None, {"steps": [(s["step"], s["ok"]) for s in steps]})
        _last.update(at=time.time(), result=res)
        return res


def evidence() -> dict:
    if not EVIDENCE.exists():
        return {"recorded": False}
    try:
        return {"recorded": True, **json.loads(EVIDENCE.read_text(encoding="utf-8"))}
    except ValueError:
        return {"recorded": False, "error": "evidence file unreadable"}

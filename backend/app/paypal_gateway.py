"""PayPal gateway: the ONLY place PayPal is called from.

* sandbox mode -> PayPal Agent Toolkit (paypal-agent-toolkit, Python) against the PayPal sandbox.
* mock mode    -> an offline emulator of the same endpoints, used when no credentials are set,
                  so the UI, the tests and demo rehearsals run anywhere.

The toolkit gives an LLM direct access to money-moving endpoints and has no approval hook,
amount ceiling or payee validation. Afterword never hands those tools to a model. Agents only
PROPOSE an action; it lands in `pending_actions` with deterministic checks, a SHA-256 of the exact
payload and an expiry, and runs only after a person (or a merchant's own written auto-close rule)
approves. Every call is written to the hash-chained audit log.

Toolkit tools used: create_product, create_subscription_plan, create_subscription,
show_subscription_details, cancel_subscription, get_invoice, list_invoices, send_invoice,
cancel_sent_invoice. The Python toolkit has no refund or subscription-transactions tool, so those
two go through the toolkit's own authenticated client, with a PayPal-Request-Id for idempotency.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import string
from datetime import datetime, timezone
from typing import Any

from . import db

CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
HAS_KEYS = bool(CLIENT_ID and CLIENT_SECRET)
# The shared demo runs on the emulator unless AFTERWORD_PAYPAL_MODE=sandbox (set by scripts/sandbox_run.py),
# because a hosted demo on real sandbox subscriptions would be used up by the first judge who runs it.
# With keys present, the live PayPal check (/api/paypal/live-check) still talks to the real sandbox.
MODE = "sandbox" if HAS_KEYS and os.getenv("AFTERWORD_PAYPAL_MODE", "").lower() == "sandbox" else "mock"
APPROVAL_TTL_HOURS = int(os.getenv("AFTERWORD_APPROVAL_TTL_HOURS", "24"))

MONEY_TOOLS = {"cancel_subscription", "refund_capture", "cancel_sent_invoice", "suspend_subscription", "activate_subscription"}
REVERSIBLE = {"suspend_subscription", "activate_subscription"}  # safe to run during the objection window


# =========================================================================== transports
class _Toolkit:
    def __init__(self) -> None:
        from paypal_agent_toolkit.shared.api import PayPalAPI
        from paypal_agent_toolkit.shared.configuration import Context

        self.api = PayPalAPI(client_id=CLIENT_ID, secret=CLIENT_SECRET, context=Context(sandbox=True))

    def _rest(self, method: str, uri: str, payload: dict | None = None, request_id: str | None = None) -> dict:
        import requests

        client = self.api._paypal_client
        headers = {"Authorization": f"Bearer {client.get_access_token()}", "Content-Type": "application/json",
                   "Prefer": "return=representation"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        r = requests.request(method, f"{client.base_url}{uri}", headers=headers, json=payload, timeout=30)
        debug = r.headers.get("paypal-debug-id", "")
        if r.status_code >= 400:
            err = RuntimeError(f"PayPal {r.status_code}: {r.text[:600]}")
            err.status, err.debug_id = r.status_code, debug  # type: ignore[attr-defined]
            raise err
        out = r.json() if r.text else {}
        if isinstance(out, dict):
            out["_paypal_debug_id"] = debug
        return out

    def run(self, method: str, params: dict, request_id: str | None = None) -> dict:
        if method == "refund_capture":
            body = {"note_to_payer": params.get("note", "")[:255]}
            if params.get("amount"):
                body["amount"] = {"value": f"{float(params['amount']):.2f}", "currency_code": params["currency"]}
            try:
                return self._rest("POST", f"/v2/payments/captures/{params['capture_id']}/refund", body, request_id)
            except RuntimeError as exc:  # some subscription payments are v1 sales, not v2 captures
                if getattr(exc, "status", 0) != 404:
                    raise
                sale = {"amount": {"total": body["amount"]["value"], "currency": body["amount"]["currency_code"]}} if "amount" in body else {}
                out = self._rest("POST", f"/v1/payments/sale/{params['capture_id']}/refund", sale, request_id)
                return {**out, "status": (out.get("state") or "").upper() or out.get("status"), "api": "v1/sale"}
        if method == "get_capture":
            try:
                return self._rest("GET", f"/v2/payments/captures/{params['capture_id']}")
            except RuntimeError as exc:
                if getattr(exc, "status", 0) != 404:
                    raise
                out = self._rest("GET", f"/v1/payments/sale/{params['capture_id']}")
                state = (out.get("state") or "").lower()
                return {**out, "status": {"refunded": "REFUNDED", "partially_refunded": "PARTIALLY_REFUNDED", "completed": "COMPLETED"}.get(state, state.upper())}
        if method in ("suspend_subscription", "activate_subscription"):  # REST: the Python toolkit has no tool for these
            verb = "suspend" if method == "suspend_subscription" else "activate"
            return self._rest("POST", f"/v1/billing/subscriptions/{params['subscription_id']}/{verb}",
                              {"reason": params.get("reason", "")[:128]}, request_id)
        if method == "list_subscription_transactions":
            return self._rest("GET", f"/v1/billing/subscriptions/{params['subscription_id']}/transactions"
                                     f"?start_time={params['start_time']}&end_time={params['end_time']}")
        if method == "create_invoice_draft":
            return self._rest("POST", "/v2/invoicing/invoices", params)
        try:
            raw = self.api.run(method, params)
        except Exception as exc:  # noqa: BLE001
            # The toolkit serialises unset optional fields as null. If PayPal ever rejects that on a create
            # call, retry the same payload (without nulls) through the toolkit's own authenticated client.
            code = getattr(getattr(exc, "response", None), "status_code", 0)
            uri = {"create_product": "/v1/catalogs/products", "create_subscription_plan": "/v1/billing/plans",
                   "create_subscription": "/v1/billing/subscriptions"}.get(method)
            if uri and code in (400, 422):
                return {**self._rest("POST", uri, params), "_via": "rest-fallback"}
            body = getattr(getattr(exc, "response", None), "text", "")[:600]
            raise RuntimeError(f"PayPal {code or ''} {method}: {body or exc}") from exc
        try:
            return json.loads(raw) if isinstance(raw, str) and raw else (raw if isinstance(raw, dict) else {})
        except ValueError:
            return {"raw": raw}


class _Mock:
    """Offline emulator of the PayPal Subscriptions, Payments and Invoicing endpoints we use."""

    def __init__(self) -> None:
        db.conn().executescript("""
        CREATE TABLE IF NOT EXISTS mock_subscriptions (id TEXT PRIMARY KEY, plan_id TEXT, plan_name TEXT, given_name TEXT,
            surname TEXT, email TEXT, status TEXT, amount REAL, currency TEXT, start_time TEXT);
        CREATE TABLE IF NOT EXISTS mock_captures (id TEXT PRIMARY KEY, subscription_id TEXT, time TEXT, amount REAL,
            currency TEXT, status TEXT);
        CREATE TABLE IF NOT EXISTS mock_invoices (id TEXT PRIMARY KEY, email TEXT, amount REAL, currency TEXT,
            description TEXT, status TEXT);
        CREATE TABLE IF NOT EXISTS mock_refunds (request_id TEXT PRIMARY KEY, refund_id TEXT, capture_id TEXT, amount REAL);
        """)
        db.conn().commit()

    @staticmethod
    def rid(prefix: str, n: int = 12) -> str:
        return prefix + "".join(random.choices(string.ascii_uppercase + string.digits, k=n))

    # ---- seeding helpers (demo data only)
    def seed_subscription(self, plan_name: str, given: str, surname: str, email: str, amount: float, currency: str,
                          start_time: str) -> str:
        sid = "I-" + self.rid("", 12)
        db.conn().execute("INSERT INTO mock_subscriptions VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (sid, "P-" + self.rid("", 20), plan_name, given, surname, email, "ACTIVE", amount, currency, start_time))
        db.conn().commit()
        return sid

    def seed_capture(self, subscription_id: str, time: str, amount: float, currency: str) -> str:
        cid = self.rid("", 17)
        db.conn().execute("INSERT INTO mock_captures VALUES (?,?,?,?,?,?)", (cid, subscription_id, time, amount, currency, "COMPLETED"))
        db.conn().commit()
        return cid

    def seed_invoice(self, email: str, amount: float, currency: str, description: str) -> str:
        iid = "INV2-" + "-".join(self.rid("", 4) for _ in range(5))
        db.conn().execute("INSERT INTO mock_invoices VALUES (?,?,?,?,?,?)", (iid, email, amount, currency, description, "UNPAID"))
        db.conn().commit()
        return iid

    def _one(self, sql: str, args: tuple) -> dict | None:
        r = db.conn().execute(sql, args).fetchone()
        return dict(r) if r else None

    def run(self, method: str, params: dict, request_id: str | None = None) -> dict:
        with db._lock:  # each emulated call is atomic, like a real API call
            return self._run(method, params, request_id)

    def _run(self, method: str, params: dict, request_id: str | None = None) -> dict:
        c = db.conn()
        if method == "show_subscription_details":
            s = self._one("SELECT * FROM mock_subscriptions WHERE id=?", (params["subscription_id"],))
            if not s:
                return {"name": "RESOURCE_NOT_FOUND"}
            return {"id": s["id"], "status": s["status"], "plan_id": s["plan_id"], "start_time": s["start_time"],
                    "subscriber": {"name": {"given_name": s["given_name"], "surname": s["surname"]}, "email_address": s["email"]},
                    "billing_info": {"outstanding_balance": {"currency_code": s["currency"], "value": "0.00"}}}
        if method == "list_subscription_transactions":
            rows = c.execute("SELECT * FROM mock_captures WHERE subscription_id=? AND time>=? AND time<=? ORDER BY time",
                             (params["subscription_id"], params["start_time"], params["end_time"])).fetchall()
            return {"transactions": [{"id": r["id"], "status": r["status"], "time": r["time"],
                                      "amount_with_breakdown": {"gross_amount": {"currency_code": r["currency"], "value": f"{r['amount']:.2f}"}}}
                                     for r in rows]}
        if method == "get_capture":
            cap = self._one("SELECT * FROM mock_captures WHERE id=?", (params["capture_id"],))
            if not cap:
                return {"name": "RESOURCE_NOT_FOUND"}
            return {"id": cap["id"], "status": cap["status"], "amount": {"currency_code": cap["currency"], "value": f"{cap['amount']:.2f}"}}
        if method == "cancel_subscription":
            s = self._one("SELECT status FROM mock_subscriptions WHERE id=?", (params["subscription_id"],))
            if not s:
                raise RuntimeError("PayPal 404: RESOURCE_NOT_FOUND")
            if s["status"] == "CANCELLED":
                raise RuntimeError("PayPal 422: SUBSCRIPTION_STATUS_INVALID (already cancelled)")
            c.execute("UPDATE mock_subscriptions SET status='CANCELLED' WHERE id=?", (params["subscription_id"],))
            c.commit()
            return {}
        if method in ("suspend_subscription", "activate_subscription"):
            s = self._one("SELECT status FROM mock_subscriptions WHERE id=?", (params["subscription_id"],))
            if not s:
                raise RuntimeError("PayPal 404: RESOURCE_NOT_FOUND")
            want, need = ("SUSPENDED", "ACTIVE") if method == "suspend_subscription" else ("ACTIVE", "SUSPENDED")
            if s["status"] != need:
                raise RuntimeError(f"PayPal 422: SUBSCRIPTION_STATUS_INVALID ({s['status'].lower()})")
            c.execute("UPDATE mock_subscriptions SET status=? WHERE id=?", (want, params["subscription_id"]))
            c.commit()
            return {}
        if method == "refund_capture":
            if request_id:
                hit = self._one("SELECT * FROM mock_refunds WHERE request_id=?", (request_id,))
                if hit:  # PayPal-Request-Id replay: same refund, nothing new moves
                    return {"id": hit["refund_id"], "status": "COMPLETED", "replayed": True,
                            "amount": {"value": f"{hit['amount']:.2f}"}}
            cap = self._one("SELECT * FROM mock_captures WHERE id=?", (params["capture_id"],))
            if not cap:
                raise RuntimeError("PayPal 404: capture not found")
            if cap["status"] == "REFUNDED":
                raise RuntimeError("PayPal 422: CAPTURE_FULLY_REFUNDED")
            amount = float(params.get("amount") or cap["amount"])
            if amount > cap["amount"] + 0.001:
                raise RuntimeError("PayPal 422: REFUND_AMOUNT_EXCEEDED")
            rid = self.rid("", 17)
            c.execute("UPDATE mock_captures SET status=? WHERE id=?",
                      ("REFUNDED" if abs(amount - cap["amount"]) < 0.001 else "PARTIALLY_REFUNDED", cap["id"]))
            if request_id:
                c.execute("INSERT INTO mock_refunds VALUES (?,?,?,?)", (request_id, rid, cap["id"], amount))
            c.commit()
            return {"id": rid, "status": "COMPLETED", "amount": {"value": f"{amount:.2f}", "currency_code": cap["currency"]}}
        if method == "get_invoice":
            inv = self._one("SELECT * FROM mock_invoices WHERE id=?", (params["invoice_id"],))
            if not inv:
                return {"name": "RESOURCE_NOT_FOUND"}
            return {"id": inv["id"], "status": inv["status"],
                    "primary_recipients": [{"billing_info": {"email_address": inv["email"]}}],
                    "amount": {"currency_code": inv["currency"], "value": f"{inv['amount']:.2f}"},
                    "detail": {"note": inv["description"]}}
        if method == "cancel_sent_invoice":
            c.execute("UPDATE mock_invoices SET status='CANCELLED' WHERE id=?", (params["invoice_id"],))
            c.commit()
            return {}
        raise ValueError(f"mock: unsupported method {method}")


_transport: Any = None


def live() -> "_Toolkit":
    """A real-sandbox client regardless of MODE (for the live check and the sandbox run)."""
    if not HAS_KEYS:
        raise RuntimeError("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET first.")
    return _Toolkit()


def transport():
    global _transport
    if _transport is None:
        _transport = _Toolkit() if MODE == "sandbox" else _Mock()
    return _transport


def call(method: str, params: dict, *, actor: str, ref: str | None = None, approved_action: str | None = None) -> dict:
    """Single choke point for every PayPal call. Money tools require an approved action id."""
    if method in MONEY_TOOLS and not approved_action:
        db.audit(actor, f"BLOCKED {method}", ref, {"reason": "money-moving call without approval"})
        raise PermissionError(f"{method} requires an approved action")
    request_id = f"afterword-{approved_action}" if approved_action else None
    try:
        result = transport().run(method, params, request_id=request_id)
    except Exception as exc:  # noqa: BLE001
        db.audit(actor, f"paypal.{method} FAILED", ref, {"mode": MODE, "params": params, "error": str(exc)})
        raise
    db.audit(actor, f"paypal.{method}", ref, {"mode": MODE, "params": params, "result": result,
                                              "action_id": approved_action, "paypal_request_id": request_id})
    return result


# =========================================================================== approval gate
def params_hash(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def propose(*, notice_id: str, merchant_id: str, tool: str, params: dict, rationale: str, proposed_by: str,
            checks: list[dict], amount: float = 0.0, not_before: str | None = None) -> dict:
    phase = "now" if tool in REVERSIBLE or not not_before else "after_window"
    ttl = db.later(hours=APPROVAL_TTL_HOURS)
    action = {"id": db.new_id("act"), "notice_id": notice_id, "merchant_id": merchant_id, "tool": tool,
              "params": params, "rationale": rationale, "policy_checks": checks, "status": "PENDING",
              "proposed_by": proposed_by, "created_at": db.now(), "params_hash": params_hash(params),
              "expires_at": max(ttl, not_before or ""), "amount": amount, "phase": phase,
              "not_before": not_before if phase == "after_window" else None}
    db.insert("pending_actions", action)
    db.audit(proposed_by, f"PROPOSED {tool}", notice_id, {"action_id": action["id"], "rationale": rationale,
                                                         "checks": checks, "payload_sha256": action["params_hash"]})
    return db.get("pending_actions", action["id"])


def reject(action_id: str, who: str, reason: str = "") -> dict:
    a = db.get("pending_actions", action_id)
    if not a or a["status"] != "PENDING":
        raise ValueError("action is not pending")
    db.update("pending_actions", action_id, {"status": "REJECTED", "decided_by": who, "decided_at": db.now(), "result": reason})
    db.audit(who, f"REJECTED {a['tool']}", a["notice_id"], {"action_id": action_id, "reason": reason})
    return db.get("pending_actions", action_id)


class WindowOpen(PermissionError):
    """An irreversible action asked to run before the objection window closed."""
    def __init__(self, until: str):
        self.until = until
        super().__init__(f"Irreversible: waits until {until} in case the account holder objects.")


def approve(action_id: str, who: str) -> dict:
    a = db.get("pending_actions", action_id)
    if not a or a["status"] != "PENDING":
        raise ValueError("action is not pending")
    if params_hash(a["params"]) != a.get("params_hash"):
        db.audit("system", f"BLOCKED {a['tool']}", a["notice_id"], {"action_id": action_id, "reason": "payload changed after proposal"})
        raise PermissionError("The payload changed after it was proposed; nothing was sent.")
    if a.get("expires_at") and a["expires_at"] < db.now():
        db.update("pending_actions", action_id, {"status": "EXPIRED", "decided_by": "system", "decided_at": db.now()})
        db.audit("system", f"EXPIRED {a['tool']}", a["notice_id"], {"action_id": action_id})
        raise PermissionError(f"This approval expired after {APPROVAL_TTL_HOURS} hours.")
    if a.get("not_before") and a["not_before"] > db.now():
        raise WindowOpen(a["not_before"])
    failed = [c for c in a["policy_checks"] if not c["passed"]]
    if failed:
        db.audit("system", f"BLOCKED {a['tool']}", a["notice_id"], {"action_id": action_id, "failed_checks": failed})
        raise PermissionError("Checks failed: " + "; ".join(c["check"] for c in failed))
    db.update("pending_actions", action_id, {"status": "APPROVED", "decided_by": who, "decided_at": db.now()})
    db.audit(who, f"APPROVED {a['tool']}", a["notice_id"], {"action_id": action_id})
    try:
        result = call(a["tool"], a["params"], actor="paypal_agent", ref=a["notice_id"], approved_action=action_id)
    except Exception as exc:  # noqa: BLE001
        db.update("pending_actions", action_id, {"status": "FAILED", "result": str(exc)[:1000]})
        raise
    db.update("pending_actions", action_id, {"status": "EXECUTED", "result": json.dumps(result, default=str)[:4000]})
    return db.get("pending_actions", action_id)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

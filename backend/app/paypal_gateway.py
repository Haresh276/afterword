"""PayPal gateway: the ONLY place money-moving calls happen.

* Real mode  -> PayPal Agent Toolkit (paypal-agent-toolkit, Python) against the sandbox.
* Mock mode  -> a faithful offline emulator, used when no PayPal credentials are set
                (lets the UI, tests and demo rehearsals run anywhere).

The toolkit itself has no approval hook, amount ceiling or payee validation, and in
v1.11.0 `create_invoice` *auto-sends* the invoice the moment it is created. So ProofPay
never exposes create/send/remind to an LLM directly: agents may only PROPOSE an action;
it lands in `pending_actions`, passes deterministic policy checks, and executes only
after a human clicks Approve. Every call is written to the audit log.
"""
from __future__ import annotations

import json
import os
import random
import string
from datetime import date
from typing import Any

from . import db

CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
MODE = "sandbox" if CLIENT_ID and CLIENT_SECRET else "mock"

AMOUNT_CEILING = float(os.getenv("PROOFPAY_AMOUNT_CEILING", "10000"))
PROOF_THRESHOLD = float(os.getenv("PROOFPAY_PROOF_THRESHOLD", "0.7"))
BUSINESS_NAME = os.getenv("PROOFPAY_BUSINESS_NAME", "")

MONEY_TOOLS = {"create_invoice", "send_invoice", "send_invoice_reminder", "cancel_sent_invoice"}
READ_TOOLS = {"get_invoice", "list_invoices"}


# --------------------------------------------------------------------------- transport
class _Toolkit:
    def __init__(self) -> None:
        from paypal_agent_toolkit.shared.api import PayPalAPI
        from paypal_agent_toolkit.shared.configuration import Context

        self.api = PayPalAPI(client_id=CLIENT_ID, secret=CLIENT_SECRET, context=Context(sandbox=True))

    def run(self, method: str, params: dict) -> dict:
        raw = self.api.run(method, params)
        try:
            return json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            return {"raw": raw}


class _Mock:
    """Offline emulator of the PayPal Invoicing v2 endpoints the toolkit wraps."""

    def __init__(self) -> None:
        db.conn().execute(
            "CREATE TABLE IF NOT EXISTS mock_invoices (id TEXT PRIMARY KEY, body TEXT, status TEXT)"
        )
        db.conn().commit()

    @staticmethod
    def _id() -> str:
        return "INV2-" + "-".join("".join(random.choices(string.ascii_uppercase + string.digits, k=4)) for _ in range(5))

    def _load(self, inv_id: str) -> dict | None:
        row = db.conn().execute("SELECT body, status FROM mock_invoices WHERE id=?", (inv_id,)).fetchone()
        if not row:
            return None
        body = json.loads(row["body"])
        body["status"] = row["status"]
        return body

    def _save(self, inv_id: str, body: dict, status: str) -> None:
        db.conn().execute(
            "INSERT OR REPLACE INTO mock_invoices (id, body, status) VALUES (?, ?, ?)",
            (inv_id, json.dumps(body), status),
        )
        db.conn().commit()

    def set_status(self, inv_id: str, status: str) -> None:
        body = self._load(inv_id)
        if body:
            self._save(inv_id, body, status)

    def run(self, method: str, params: dict) -> dict:
        if method == "create_invoice":
            inv_id = self._id()
            body = {
                "id": inv_id,
                **params,
                "detail": {
                    **params.get("detail", {}),
                    "metadata": {"recipient_view_url": f"https://www.sandbox.paypal.com/invoice/p/#{inv_id}"},
                },
            }
            # mirror toolkit v1.11.0 behaviour: create auto-sends
            self._save(inv_id, body, "UNPAID")
            href = f"https://api-m.sandbox.paypal.com/v2/invoicing/invoices/{inv_id}"
            return {
                "createResult": {"rel": "self", "href": href, "method": "GET"},
                "sendResult": json.dumps({"href": body["detail"]["metadata"]["recipient_view_url"]}),
            }
        if method == "get_invoice":
            body = self._load(params["invoice_id"])
            return body or {"name": "RESOURCE_NOT_FOUND"}
        if method == "list_invoices":
            rows = db.conn().execute("SELECT id FROM mock_invoices").fetchall()
            items = [self._load(r["id"]) for r in rows]
            return {"items": items, "total_items": len(items)}
        if method == "send_invoice_reminder":
            return {}  # PayPal returns 204 No Content
        if method == "cancel_sent_invoice":
            self.set_status(params["invoice_id"], "CANCELLED")
            return {}
        raise ValueError(f"mock: unsupported method {method}")


_transport: Any = None


def transport():
    global _transport
    if _transport is None:
        _transport = _Toolkit() if MODE == "sandbox" else _Mock()
    return _transport


def call(method: str, params: dict, *, actor: str, milestone_id: str | None = None, approved_action: str | None = None) -> dict:
    """Single choke point for every PayPal call. Money tools require an approved action id."""
    if method in MONEY_TOOLS and not approved_action:
        db.audit(actor, f"BLOCKED {method}", milestone_id, {"reason": "money-moving call without human approval"})
        raise PermissionError(f"{method} requires an approved action")
    try:
        result = transport().run(method, params)
        db.audit(actor, f"paypal.{method}", milestone_id, {"mode": MODE, "params": params, "result": result, "action_id": approved_action})
        return result
    except Exception as exc:  # noqa: BLE001
        db.audit(actor, f"paypal.{method} FAILED", milestone_id, {"mode": MODE, "params": params, "error": str(exc)})
        raise


# --------------------------------------------------------------------------- policy
def _contract_for(ms: dict) -> dict:
    return db.get("contracts", ms["contract_id"])


def build_invoice_params(ms: dict) -> dict:
    c = _contract_for(ms)
    given, _, surname = (c.get("client_name") or "").partition(" ")
    params: dict = {
        "detail": {"currency_code": c["currency"], "invoice_date": date.today().isoformat()},
        "invoicer": {"business_name": (BUSINESS_NAME or c.get("freelancer_name") or "ProofPay Freelancer")[:300]},
        "primary_recipients": [
            {"billing_info": {"email_address": c["client_email"], "name": {"given_name": given or None, "surname": surname or None}}}
        ],
        "items": [
            {
                "name": f"{c['title']} - Milestone {ms['seq']}: {ms['name']}"[:200],
                "quantity": "1",
                "unit_amount": {"currency_code": c["currency"], "value": f"{float(ms['amount']):.2f}"},
                "unit_of_measure": "AMOUNT",
            }
        ],
    }
    return params


def policy_checks(ms: dict, params: dict, override_reason: str | None = None) -> list[dict]:
    c = _contract_for(ms)
    item = params["items"][0]
    amount = float(item["unit_amount"]["value"])
    recipient = params["primary_recipients"][0]["billing_info"]["email_address"]
    already = db.query(
        "SELECT COALESCE(SUM(amount),0) AS s FROM milestones WHERE contract_id=? AND invoice_id IS NOT NULL AND id<>?",
        (c["id"], ms["id"]),
    )[0]["s"]
    score = ms.get("proof_score")
    checks = [
        {"check": "Amount matches agreed milestone", "passed": abs(amount - float(ms["amount"])) < 0.005,
         "detail": f"{amount:.2f} vs contract {float(ms['amount']):.2f}"},
        {"check": "Under per-invoice ceiling", "passed": amount <= AMOUNT_CEILING,
         "detail": f"{amount:.2f} <= {AMOUNT_CEILING:.2f}"},
        {"check": "Payee is the contract's client", "passed": recipient.lower() == (c["client_email"] or "").lower(),
         "detail": recipient},
        {"check": "Currency matches contract", "passed": item["unit_amount"]["currency_code"] == c["currency"],
         "detail": c["currency"]},
        {"check": "Contract total not exceeded", "passed": already + amount <= float(c["total_amount"]) + 0.005,
         "detail": f"{already + amount:.2f} of {float(c['total_amount']):.2f}"},
        {"check": "No duplicate invoice for milestone", "passed": not ms.get("invoice_id"),
         "detail": ms.get("invoice_id") or "none yet"},
    ]
    proof_ok = score is not None and score >= PROOF_THRESHOLD
    checks.append({
        "check": f"Proof of work verified (>= {int(PROOF_THRESHOLD * 100)}%)",
        "passed": proof_ok or bool(override_reason),
        "detail": (f"score {score:.0%}" if score is not None else "not checked") + (f" | override: {override_reason}" if override_reason and not proof_ok else ""),
    })
    return checks


# --------------------------------------------------------------------------- approval gate
def propose(milestone_id: str, tool: str, params: dict, rationale: str, proposed_by: str, checks: list[dict]) -> dict:
    existing = db.query(
        "SELECT * FROM pending_actions WHERE milestone_id=? AND tool=? AND status='PENDING'", (milestone_id, tool)
    )
    for e in existing:
        db.update("pending_actions", e["id"], {"status": "REJECTED", "decided_by": "system", "decided_at": db.now(),
                                               "result": "superseded by newer proposal"})
    action = {
        "id": db.new_id("act"), "milestone_id": milestone_id, "tool": tool, "params": params,
        "rationale": rationale, "policy_checks": checks, "status": "PENDING",
        "proposed_by": proposed_by, "created_at": db.now(),
    }
    db.insert("pending_actions", action)
    db.audit(proposed_by, f"PROPOSED {tool}", milestone_id, {"action_id": action["id"], "rationale": rationale, "checks": checks})
    return db.get("pending_actions", action["id"])


def reject(action_id: str, who: str = "human", reason: str = "") -> dict:
    a = db.get("pending_actions", action_id)
    if not a or a["status"] != "PENDING":
        raise ValueError("action is not pending")
    db.update("pending_actions", action_id, {"status": "REJECTED", "decided_by": who, "decided_at": db.now(), "result": reason})
    db.audit(who, f"REJECTED {a['tool']}", a["milestone_id"], {"action_id": action_id, "reason": reason})
    if a["tool"] == "create_invoice":
        db.update("milestones", a["milestone_id"], {"status": "VERIFIED", "updated_at": db.now()})
    return db.get("pending_actions", action_id)


def approve(action_id: str, who: str = "human") -> dict:
    a = db.get("pending_actions", action_id)
    if not a or a["status"] != "PENDING":
        raise ValueError("action is not pending")
    failed = [c for c in a["policy_checks"] if not c["passed"]]
    if failed:
        db.audit("system", f"BLOCKED {a['tool']}", a["milestone_id"], {"action_id": action_id, "failed_checks": failed})
        raise PermissionError("policy checks failed: " + "; ".join(c["check"] for c in failed))
    db.update("pending_actions", action_id, {"status": "APPROVED", "decided_by": who, "decided_at": db.now()})
    db.audit(who, f"APPROVED {a['tool']}", a["milestone_id"], {"action_id": action_id})
    try:
        result = call(a["tool"], a["params"], actor="paypal_agent", milestone_id=a["milestone_id"], approved_action=action_id)
    except Exception as exc:  # noqa: BLE001
        db.update("pending_actions", action_id, {"status": "FAILED", "result": str(exc)})
        raise
    db.update("pending_actions", action_id, {"status": "EXECUTED", "result": json.dumps(result, default=str)[:4000]})
    _apply_result(a, result)
    return db.get("pending_actions", action_id)


def _apply_result(action: dict, result: dict) -> None:
    ms_id = action["milestone_id"]
    if action["tool"] == "create_invoice":
        create = result.get("createResult", result)
        href = create.get("href", "")
        inv_id = href.rstrip("/").split("/")[-1] if "/invoicing/invoices/" in href else create.get("id")
        details = call("get_invoice", {"invoice_id": inv_id}, actor="paypal_agent", milestone_id=ms_id) if inv_id else {}
        url = (details.get("detail", {}).get("metadata", {}) or {}).get("recipient_view_url")
        db.update("milestones", ms_id, {
            "invoice_id": inv_id, "invoice_url": url, "invoice_status": details.get("status", "SENT"),
            "status": "INVOICED", "sent_at": db.now(), "updated_at": db.now(),
        })
    elif action["tool"] == "send_invoice_reminder":
        ms = db.get("milestones", ms_id)
        db.update("milestones", ms_id, {"reminders_sent": (ms.get("reminders_sent") or 0) + 1, "updated_at": db.now()})


# --------------------------------------------------------------------------- sync
PAID_STATES = {"PAID", "MARKED_AS_PAID"}


def sync_statuses() -> list[dict]:
    changed = []
    for ms in db.query("SELECT * FROM milestones WHERE invoice_id IS NOT NULL AND status IN ('INVOICED')"):
        try:
            inv = call("get_invoice", {"invoice_id": ms["invoice_id"]}, actor="system", milestone_id=ms["id"])
        except Exception:  # noqa: BLE001
            continue
        status = inv.get("status")
        url = (inv.get("detail", {}).get("metadata", {}) or {}).get("recipient_view_url") or ms.get("invoice_url")
        if status and status != ms.get("invoice_status"):
            upd = {"invoice_status": status, "invoice_url": url, "updated_at": db.now()}
            if status in PAID_STATES:
                upd.update(status="PAID", paid_at=db.now())
                db.audit("system", "PAYMENT RECEIVED", ms["id"], {"invoice_id": ms["invoice_id"]})
            elif status == "CANCELLED":
                upd.update(status="CANCELLED")
            db.update("milestones", ms["id"], upd)
            changed.append({"milestone_id": ms["id"], "status": status})
    return changed


def mock_pay(invoice_id: str) -> None:
    if MODE != "mock":
        raise PermissionError("Simulated payments only exist in mock mode. In sandbox, pay the invoice as the sandbox buyer.")
    transport().set_status(invoice_id, "PAID")
    db.audit("system", "MOCK client paid invoice", None, {"invoice_id": invoice_id})

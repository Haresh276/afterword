"""Afterword as an MCP server, so a merchant's own AI support agent can plug in directly.

Mounted at /mcp (streamable HTTP). Tools are read-and-verify only: an agent can see which
bereavement notices are waiting, check the hub signature, and read what Afterword proposes.
Approving money movement stays with a person or the merchant's written auto-close rule.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

import hashlib
import hmac

from . import db, notices


def merchant_key(merchant_id: str) -> str:
    """Each business's MCP key: an HMAC of its id under a hub secret. Shown once in that business's console."""
    secret = db.kv_get("mcp_secret")
    if not secret:
        import secrets as _s
        secret = _s.token_hex(32)
        db.kv_set("mcp_secret", secret)
    return "awk_" + hmac.new(secret.encode(), merchant_id.encode(), hashlib.sha256).hexdigest()[:32]


def _authorised(merchant_id: str, key: str) -> bool:
    return bool(key) and hmac.compare_digest(merchant_key(merchant_id), key)


DENIED = {"error": "merchant_key does not match merchant_id. Each business only sees its own notices."}

server = MCPServer(
    "afterword",
    title="Afterword bereavement notices",
    instructions=("Bereavement notices from families, signed by the Afterword hub. Use list_notices to see what is "
                  "waiting for a business, get_notice to read one with its PayPal checks and proposed actions, and "
                  "verify_notice to check the Ed25519 signature. These tools never move money."),
)


def _summary(n: dict) -> dict:
    p = n["payload"]
    claimant = {k: v for k, v in p["claimant"].items() if k in ("name", "relationship")}   # no contact details over MCP
    return {"notice_id": n["id"], "merchant_id": n["merchant_id"], "status": n["status"],
            "deceased": p["deceased"], "claimant": claimant, "verification_score": p["verification"]["score"],
            "refund_due": (n.get("review") or {}).get("refund_total", 0), "created_at": n["created_at"]}


@server.tool(description="List bereavement notices for a business (merchant_id like 'm_streambox'), newest first. "
                         "merchant_key is the key shown in that business's Afterword console.")
def list_notices(merchant_id: str, merchant_key: str, status: str = "") -> list[dict]:
    if not _authorised(merchant_id, merchant_key):
        return [DENIED]
    rows = db.query("SELECT * FROM notices WHERE merchant_id=? ORDER BY created_at DESC", (merchant_id,))
    return [_summary(n) for n in rows if not status or n["status"] == status.upper()]


@server.tool(description="Read one notice: identity, PayPal matches, proposed actions with their checks, and outcome.")
def get_notice(notice_id: str, merchant_key: str) -> dict:
    n = db.get("notices", notice_id)
    if not n or not _authorised(n["merchant_id"], merchant_key):
        return {"error": "notice not found"}
    acts = db.query("SELECT tool, params, rationale, policy_checks, status, amount, params_hash FROM pending_actions WHERE notice_id=?",
                    (notice_id,))
    return {**_summary(n), "signature_valid": notices.verify(n["payload"], n["signature"]),
            "references": n["payload"]["references"], "matches": (n.get("review") or {}).get("matches", []),
            "proposed_actions": acts, "outcome": n.get("outcome"), "reply_to_family": n.get("reply")}


@server.tool(description="Verify a notice's Ed25519 signature against the Afterword hub public key.")
def verify_notice(notice_id: str) -> dict:
    n = db.get("notices", notice_id)
    if not n:
        return {"error": "notice not found"}
    return {"notice_id": notice_id, "protocol": notices.PROTOCOL, "signature_valid": notices.verify(n["payload"], n["signature"]),
            "hub_public_key": notices.public_key_b64()}


@server.tool(description=("Before your billing or collections agent retries a failed payment, sends a dunning email or opens "
                          "a collections case, ask whether this customer has been reported deceased to your business. "
                          "Answers only for notices addressed to merchant_id (proved by merchant_key), never for other businesses' customers."))
def check_customer_before_collecting(merchant_id: str, merchant_key: str, paypal_email: str) -> dict:
    if not _authorised(merchant_id, merchant_key):
        return DENIED
    email = (paypal_email or "").strip().lower()
    for n in db.query("SELECT * FROM notices WHERE merchant_id=? ORDER BY created_at DESC", (merchant_id,)):
        p = n["payload"]
        if (p["deceased"].get("paypal_email") or "").lower() != email:
            continue
        if n["status"] == "WITHDRAWN":
            return {"reported_deceased": False, "note": "A report was withdrawn: the account holder said they are alive.", "notice_id": n["id"]}
        return {"reported_deceased": True, "notice_id": n["id"], "status": n["status"], "date_of_death": p["deceased"].get("date_of_death"),
                "signature_valid": notices.verify(p, n["signature"]),
                "advice": "Do not retry the charge, send payment reminders or start collections. Handle the notice instead."}
    return {"reported_deceased": False}


def asgi_app():
    from mcp.server.transport_security import TransportSecuritySettings

    return server.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True,
                                      transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

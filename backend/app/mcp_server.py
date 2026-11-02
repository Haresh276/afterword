"""Afterword as an MCP server, so a merchant's own AI support agent can plug in directly.

Mounted at /mcp (streamable HTTP). Tools are read-and-verify only: an agent can see which
bereavement notices are waiting, check the hub signature, and read what Afterword proposes.
Approving money movement stays with a person or the merchant's written auto-close rule.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import db, notices

server = MCPServer(
    "afterword",
    title="Afterword bereavement notices",
    instructions=("Bereavement notices from families, signed by the Afterword hub. Use list_notices to see what is "
                  "waiting for a business, get_notice to read one with its PayPal checks and proposed actions, and "
                  "verify_notice to check the Ed25519 signature. These tools never move money."),
)


def _summary(n: dict) -> dict:
    p = n["payload"]
    return {"notice_id": n["id"], "merchant_id": n["merchant_id"], "status": n["status"],
            "deceased": p["deceased"], "claimant": p["claimant"], "verification_score": p["verification"]["score"],
            "refund_due": (n.get("review") or {}).get("refund_total", 0), "created_at": n["created_at"]}


@server.tool(description="List bereavement notices for a business (merchant_id like 'm_streambox'), newest first.")
def list_notices(merchant_id: str, status: str = "") -> list[dict]:
    rows = db.query("SELECT * FROM notices WHERE merchant_id=? ORDER BY created_at DESC", (merchant_id,))
    return [_summary(n) for n in rows if not status or n["status"] == status.upper()]


@server.tool(description="Read one notice: identity, PayPal matches, proposed actions with their checks, and outcome.")
def get_notice(notice_id: str) -> dict:
    n = db.get("notices", notice_id)
    if not n:
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


def asgi_app():
    from mcp.server.transport_security import TransportSecuritySettings

    return server.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True,
                                      transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

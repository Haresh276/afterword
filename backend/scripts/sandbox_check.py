"""Day-1 check: proves your PayPal sandbox credentials work end to end.

    cd backend && python scripts/sandbox_check.py

Creates ONE $1.00 sandbox invoice to DEMO_CLIENT_EMAIL through the PayPal Agent Toolkit
and prints the link the client would pay. Sandbox only - no real money moves.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402,F401  (loads .env)
from paypal_agent_toolkit.shared.api import PayPalAPI  # noqa: E402
from paypal_agent_toolkit.shared.configuration import Context  # noqa: E402

cid, sec, to = os.getenv("PAYPAL_CLIENT_ID"), os.getenv("PAYPAL_CLIENT_SECRET"), os.getenv("DEMO_CLIENT_EMAIL")
if not (cid and sec and to):
    sys.exit("Set PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET and DEMO_CLIENT_EMAIL in .env first.")

api = PayPalAPI(client_id=cid, secret=sec, context=Context(sandbox=True))
print("1/3 token ok:", bool(api._paypal_client.get_access_token()))
res = json.loads(api.run("create_invoice", {
    "detail": {"currency_code": "USD"},
    "invoicer": {"business_name": "ProofPay sandbox check"},
    "primary_recipients": [{"billing_info": {"email_address": to}}],
    "items": [{"name": "Sandbox connectivity check", "quantity": "1", "unit_amount": {"currency_code": "USD", "value": "1.00"}}],
}))
href = res.get("createResult", res).get("href", "")
inv = href.rstrip("/").split("/")[-1]
print("2/3 invoice created + sent:", inv)
got = json.loads(api.run("get_invoice", {"invoice_id": inv}))
print("3/3 status:", got.get("status"), "| pay it here as the sandbox buyer:",
      got.get("detail", {}).get("metadata", {}).get("recipient_view_url"))

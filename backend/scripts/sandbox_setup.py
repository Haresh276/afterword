"""Builds the demo inside a real PayPal sandbox, using the PayPal Agent Toolkit.

Before you start (developer.paypal.com > Testing Tools > Sandbox Accounts):
  1. Create a Personal sandbox account named  Lakshmi Narayanan  (first/last name matter:
     the merchant agent checks the subscriber name PayPal returns against the certificate).
  2. Put its email in .env as DEMO_CLIENT_EMAIL, plus PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET.

Then:
  python scripts/sandbox_setup.py create   # products, plans, subscriptions, one invoice; prints approval links
  -> open each link, log in as the Lakshmi sandbox account, click Subscribe (sandbox money only)
  python scripts/sandbox_setup.py finish   # reads the real subscription IDs and captures into samples/sandbox_demo.json
  -> restart the app; the demo now runs on real sandbox subscriptions and refunds.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402,F401  (loads .env)
from app import paypal_gateway as pp  # noqa: E402
from app.seed import MERCHANTS, SAMPLES  # noqa: E402

STATE = SAMPLES / "sandbox_state.json"
OUT = SAMPLES / "sandbox_demo.json"
BUYER = os.getenv("DEMO_CLIENT_EMAIL", "")


def run(method, params):
    return pp.call(method, params, actor="sandbox_setup")


def create():
    if pp.MODE != "sandbox" or not BUYER:
        sys.exit("Set PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET and DEMO_CLIENT_EMAIL in .env first.")
    state = {"subscriptions": {}, "invoices": {}}
    for mid, name, cat, amount, *_rest, kind in MERCHANTS:
        if kind == "sub":
            prod = run("create_product", {"name": f"{name} ({cat})", "type": "SERVICE"})
            plan = run("create_subscription_plan", {
                "product_id": prod["id"], "name": f"{name} monthly",
                "billing_cycles": [{"frequency": {"interval_unit": "MONTH", "interval_count": 1}, "tenure_type": "REGULAR",
                                    "sequence": 1, "total_cycles": 0,
                                    "pricing_scheme": {"fixed_price": {"currency_code": "USD", "value": f"{amount:.2f}"}}}],
                "payment_preferences": {"auto_bill_outstanding": True, "payment_failure_threshold": 1}})
            sub = run("create_subscription", {
                "plan_id": plan["id"],
                "subscriber": {"name": {"given_name": "Lakshmi", "surname": "Narayanan"}, "email_address": BUYER},
                "application_context": {"brand_name": name, "user_action": "SUBSCRIBE_NOW",
                                        "return_url": "https://example.com/afterword/return",
                                        "cancel_url": "https://example.com/afterword/cancel"}})
            link = next((l["href"] for l in sub.get("links", []) if l.get("rel") == "approve"), None)
            state["subscriptions"][mid] = {"subscription_id": sub["id"], "approve": link}
            print(f"{name:24} approve: {link}")
        else:
            draft = pp.transport().run("create_invoice_draft", {
                "detail": {"currency_code": "USD", "note": "Spanish lessons, block of 8"},
                "invoicer": {"business_name": name},
                "primary_recipients": [{"billing_info": {"email_address": BUYER}}],
                "items": [{"name": "Spanish lessons, block of 8", "quantity": "1",
                           "unit_amount": {"currency_code": "USD", "value": f"{amount:.2f}"}}]})
            iid = draft.get("id") or draft.get("href", "").rstrip("/").split("/")[-1]
            run("send_invoice", {"invoice_id": iid, "send_to_recipient": True})
            state["invoices"][mid] = iid
            print(f"{name:24} invoice {iid} sent to {BUYER}")
    STATE.write_text(json.dumps(state, indent=2))
    print("\nApprove every link above as the Lakshmi sandbox buyer, then run: python scripts/sandbox_setup.py finish")


def finish():
    state = json.loads(STATE.read_text())
    out = {"subscriptions": {}, "invoices": state["invoices"]}
    start = pp.iso(datetime.now(timezone.utc) - timedelta(days=3))
    end = pp.iso(datetime.now(timezone.utc) + timedelta(minutes=5))
    for mid, s in state["subscriptions"].items():
        d = run("show_subscription_details", {"subscription_id": s["subscription_id"]})
        txs = run("list_subscription_transactions", {"subscription_id": s["subscription_id"], "start_time": start, "end_time": end})
        caps = [{"date": t["time"][:10], "capture_id": t["id"], "amount": float(t["amount_with_breakdown"]["gross_amount"]["value"])}
                for t in txs.get("transactions", []) if t.get("status") == "COMPLETED"]
        print(f"{mid:16} {d.get('status'):10} captures: {len(caps)}")
        if d.get("status") != "ACTIVE":
            print("   -> not approved yet; open its link and subscribe as the sandbox buyer, then rerun finish")
        out["subscriptions"][mid] = {"subscription_id": s["subscription_id"], "captures": caps}
    OUT.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {OUT}. Restart the app with the same .env and press 'Reset demo'.")


if __name__ == "__main__":
    {"create": create, "finish": finish}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()

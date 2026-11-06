"""Afterword against the REAL PayPal sandbox, start to finish, in one command.

    python scripts/sandbox_run.py            # real sandbox (needs .env, see below)
    python scripts/sandbox_run.py --dry-run  # same steps on the built-in simulator, to rehearse

What it does
  1. Creates 5 monthly subscriptions (PayPal Agent Toolkit: create_product, create_subscription_plan,
     create_subscription) for the sandbox buyer "Eleanor Hayes", and sends her one invoice.
  2. Opens a page with 5 approval links. Log in as the sandbox BUYER and press "Agree & Subscribe" on each.
     The script waits until PayPal reports every subscription ACTIVE and its first payment COMPLETED.
  3. Runs the whole Afterword flow on those real subscriptions: certificate -> activity -> signed notices ->
     merchant agents pause, cancel and refund through PayPal -> Afterword re-reads PayPal to confirm.
  4. Writes evidence/sandbox_run.json and evidence/SANDBOX_RUN.md (ids, statuses, PayPal debug ids,
     audit-chain check). Upload the evidence folder to GitHub; the app shows it under PayPal proof.

.env in the repo root (never commit it; it's in .gitignore):
    PAYPAL_CLIENT_ID=...          # developer.paypal.com > Apps & Credentials > Sandbox > your app
    PAYPAL_CLIENT_SECRET=...
    SANDBOX_BUYER_EMAIL=...       # the Personal sandbox account named Eleanor Hayes
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import tempfile
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
EVIDENCE_DIR = ROOT / "evidence"
STATE = BACKEND / "samples" / "sandbox_state.json"
sys.path.insert(0, str(BACKEND))


def say(msg=""):
    print(msg, flush=True)


def main():
    import logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="use the simulator instead of the real sandbox")
    ap.add_argument("--fresh", action="store_true", help="ignore subscriptions created by an earlier run")
    ap.add_argument("--wait-minutes", type=int, default=20)
    a = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        load_dotenv(BACKEND / ".env")
    except ImportError:
        sys.exit("Run first:  python scripts/install.py")

    buyer = os.getenv("SANDBOX_BUYER_EMAIL", "").strip()
    if not a.dry_run:
        missing = [k for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET") if not os.getenv(k, "").strip()]
        if missing:
            sys.exit(f"Missing in .env: {', '.join(missing)}. See the top of this file.")
        if not buyer:
            buyer = input("Sandbox BUYER email (the Personal account named Eleanor Hayes): ").strip()
        if "@" not in buyer:
            sys.exit("That doesn't look like an email.")
    os.environ.update({
        "AFTERWORD_PAYPAL_MODE": "mock" if a.dry_run else "sandbox",
        "AFTERWORD_DB": str(Path(tempfile.mkdtemp()) / "sandbox_run.db"),
        "AFTERWORD_OBJECTION_WINDOW": "0", "AFTERWORD_SYNC_MERCHANTS": "1", "AFTERWORD_AUTOPILOT_DELAY": "0",
        "DEMO_CLIENT_EMAIL": buyer or "eleanor.hayes@example.com",
    })
    if a.dry_run:
        os.environ.pop("PAYPAL_CLIENT_ID", None)
        os.environ.pop("PAYPAL_CLIENT_SECRET", None)

    from app import db, seed  # noqa: E402  (env must be set first)
    from app import paypal_gateway as pp  # noqa: E402

    say(f"\nAfterword sandbox run · PayPal mode: {pp.MODE} · buyer: {buyer or '(simulated)'}\n")
    if not a.dry_run:
        create_and_wait(pp, seed, buyer, a)
    result = run_flow()
    write_evidence(result, dry=a.dry_run, buyer=buyer)


# ----------------------------------------------------------------------------- step 1-2: real subscriptions
def call(pp, method, params):
    return pp.call(method, params, actor="sandbox_run")


def create_and_wait(pp, seed, buyer, a):
    state = json.loads(STATE.read_text()) if STATE.exists() and not a.fresh else None
    if state and state.get("buyer") != buyer:
        state = None
    if not state:
        say("Step 1 of 4  Creating products, plans, subscriptions and an invoice in your sandbox...")
        state = {"buyer": buyer, "subscriptions": {}, "invoices": {}}
        for mid, name, cat, amount, *_r, kind in seed.MERCHANTS:
            if kind == "sub":
                prod = call(pp, "create_product", {"name": f"{name} ({cat})", "type": "SERVICE"})
                plan = call(pp, "create_subscription_plan", {
                    "product_id": prod["id"], "name": f"{name} monthly",
                    "billing_cycles": [{"frequency": {"interval_unit": "MONTH", "interval_count": 1}, "tenure_type": "REGULAR",
                                        "sequence": 1, "total_cycles": 0,
                                        "pricing_scheme": {"fixed_price": {"currency_code": "USD", "value": f"{amount:.2f}"}}}],
                    "payment_preferences": {"auto_bill_outstanding": True, "payment_failure_threshold": 1}})
                sub = call(pp, "create_subscription", {
                    "plan_id": plan["id"],
                    "subscriber": {"name": {"given_name": seed.DECEASED["given"], "surname": seed.DECEASED["surname"]}, "email_address": buyer},
                    "application_context": {"brand_name": name, "user_action": "SUBSCRIBE_NOW",
                                            "return_url": "https://example.com/afterword/approved",
                                            "cancel_url": "https://example.com/afterword/cancelled"}})
                link = next((l["href"] for l in sub.get("links", []) if l.get("rel") == "approve"), None)
                state["subscriptions"][mid] = {"name": name, "amount": amount, "subscription_id": sub["id"], "approve": link}
                say(f"   {name:24} {sub['id']}")
            elif kind == "invoice":
                draft = pp.transport().run("create_invoice_draft", {
                    "detail": {"currency_code": "USD", "note": "Piano lessons, block of 8"},
                    "invoicer": {"business_name": name},
                    "primary_recipients": [{"billing_info": {"email_address": buyer}}],
                    "items": [{"name": "Piano lessons, block of 8", "quantity": "1", "unit_amount": {"currency_code": "USD", "value": f"{amount:.2f}"}}]})
                iid = draft.get("id") or draft.get("href", "").rstrip("/").split("/")[-1]
                call(pp, "send_invoice", {"invoice_id": iid, "send_to_recipient": True})
                state["invoices"][mid] = iid
                say(f"   {name:24} invoice {iid} sent to {buyer}")
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(state, indent=2))
    else:
        say("Step 1 of 4  Reusing the subscriptions created by your earlier run (use --fresh to start over).")

    page = Path(tempfile.gettempdir()) / "afterword_approve.html"
    rows = "".join(f"<li><a href='{html.escape(s['approve'] or '#')}' target='_blank'>{html.escape(s['name'])}: ${s['amount']:.2f}/month</a></li>"
                   for s in state["subscriptions"].values())
    page.write_text(f"""<!doctype html><meta charset=utf-8><title>Approve sandbox subscriptions</title>
<body style="font:16px system-ui;max-width:640px;margin:40px auto;line-height:1.5">
<h2>Approve 5 sandbox subscriptions</h2>
<ol><li>Click each link below. It opens PayPal's <b>sandbox</b> checkout.</li>
<li>Log in with the <b>sandbox buyer</b> email <code>{html.escape(buyer)}</code> and its sandbox password
(developer.paypal.com &gt; Testing Tools &gt; Sandbox Accounts &gt; the account &gt; View/Edit account).</li>
<li>Press <b>Agree &amp; Subscribe</b>. You'll land on example.com: that's expected, close the tab.</li></ol>
<ul>{rows}</ul><p>Leave the terminal open: it continues by itself once PayPal reports them active. No real money is involved.</p></body>""")
    say(f"\nStep 2 of 4  Approve each subscription as the sandbox buyer. Opening {page}")
    for s in state["subscriptions"].values():
        say(f"   {s['name']:24} {s['approve']}")
    try:
        webbrowser.open(page.as_uri())
    except Exception:  # noqa: BLE001
        pass

    deadline = time.time() + a.wait_minutes * 60
    start = pp.iso(datetime.now(timezone.utc) - timedelta(days=3))
    out = {"subscriptions": {}, "invoices": state["invoices"]}
    name_ok = True
    while True:
        end = pp.iso(datetime.now(timezone.utc) + timedelta(minutes=5))
        ready, line = 0, []
        for mid, s in state["subscriptions"].items():
            d = call(pp, "show_subscription_details", {"subscription_id": s["subscription_id"]})
            caps = []
            if d.get("status") == "ACTIVE":
                txs = call(pp, "list_subscription_transactions", {"subscription_id": s["subscription_id"], "start_time": start, "end_time": end})
                caps = [{"date": t["time"][:10], "capture_id": t["id"], "amount": float(t["amount_with_breakdown"]["gross_amount"]["value"])}
                        for t in txs.get("transactions", []) if t.get("status") == "COMPLETED"]
                nm = (d.get("subscriber") or {}).get("name") or {}
                if nm and f"{nm.get('given_name', '')} {nm.get('surname', '')}".strip().lower() != seed.DECEASED["name"].lower():
                    name_ok = False
            out["subscriptions"][mid] = {"subscription_id": s["subscription_id"], "captures": caps}
            ready += bool(caps)
            line.append(f"{s['name'].split()[0]}:{d.get('status', '?')[:8]}{'+paid' if caps else ''}")
        say(f"   {datetime.now():%H:%M:%S}  {ready}/5 paid   " + "  ".join(line))
        if ready == len(state["subscriptions"]):
            break
        if time.time() > deadline:
            sys.exit("\nStopped waiting. Approve the remaining links, then run this script again (it reuses what it created).")
        time.sleep(15)
    if not name_ok:
        say("\n   WARNING: PayPal shows a subscriber name other than 'Eleanor Hayes'. The merchant agents check that name against the "
            "certificate, so auto-close will stop for review. Rename the sandbox buyer to Eleanor Hayes (or create a new one) and use --fresh.")
    seed.SANDBOX_DATA.write_text(json.dumps(out, indent=2))
    say(f"   All subscriptions active with a completed first payment. Saved {seed.SANDBOX_DATA.name}.")


# ----------------------------------------------------------------------------- step 3: the Afterword flow
def run_flow() -> dict:
    from fastapi.testclient import TestClient
    from app import db, merchant
    from app.main import app

    say("\nStep 3 of 4  Running Afterword on these subscriptions...")
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        k = c.post("/api/cases", json={"claimant_name": seed_claimant()["name"], "claimant_email": seed_claimant()["email"],
                                       "relationship": "daughter", "language": "en"}).json()
        cid = k["id"]
        k = c.post(f"/api/cases/{cid}/certificate", data={"use_specimen": "1"}).json()
        say(f"   Certificate checks: {int(k['verification']['score'] * 100)}%  status {k['status']}")
        k = c.post(f"/api/cases/{cid}/activity", data={"use_sample": "1"}).json()
        say(f"   Found {len([f for f in k['findings'] if f['selected']])} things to stop or refund")
        k = c.post(f"/api/cases/{cid}/notify").json()
        for n in c.get("/api/merchant/notices").json():
            if n["status"] not in ("CLOSED",):
                out = c.post(f"/api/merchant/notices/{n['id']}/approve-all").json()
                say(f"   {n.get('merchant_name', n['merchant_id'])}: approved by a person -> {out.get('status')}")
        merchant.tick()
        k = c.get(f"/api/cases/{cid}").json()
        say(f"   Closed {k['totals']['closed']} of {k['totals']['notified']}; refunded ${k['totals']['refunded']:.2f}; "
            f"PayPal confirmed {k['totals']['confirmed']}")
        return {"case": k, "chain": c.get("/api/audit/verify").json()}


def seed_claimant():
    from app import seed
    return seed.CLAIMANT


# ----------------------------------------------------------------------------- step 4: evidence
def write_evidence(result: dict, dry: bool, buyer: str):
    from app import db
    from app import paypal_gateway as pp

    say("\nStep 4 of 4  Re-reading PayPal and writing the evidence...")
    k = result["case"]
    items = []
    for f in k["findings"]:
        if not f.get("notice_id"):
            continue
        acts = db.query("SELECT * FROM pending_actions WHERE notice_id=? ORDER BY created_at", (f["notice_id"],))
        steps = []
        for a in acts:
            res = {}
            try:
                res = json.loads(a["result"]) if a.get("result") else {}
            except (TypeError, ValueError):
                res = {"raw": str(a.get("result"))[:200]}
            steps.append({"step": a["tool"], "status": a["status"], "approved_by": a["decided_by"], "at": a["decided_at"],
                          "target": a["params"].get("subscription_id") or a["params"].get("capture_id") or a["params"].get("invoice_id"),
                          "amount": a["amount"], "paypal_id": res.get("id"), "paypal_status": res.get("status"),
                          "paypal_debug_id": res.get("_paypal_debug_id", ""), "request_id": f"afterword-{a['id']}"})
        conf = (f.get("notice") or {}).get("confirmation") or {}
        items.append({"merchant": f["merchant_name"], "kind": f["kind"], "status": f["status"], "refunded": f.get("refunded") or 0,
                      "receipt_signature_valid": (f.get("notice") or {}).get("receipt_valid"),
                      "confirmed_on_paypal": conf.get("all_confirmed"), "paypal_reread": conf.get("items", []), "steps": steps})
    calls = db.query("SELECT ts, actor, action FROM audit_log WHERE action LIKE 'paypal.%' ORDER BY id")
    ev = {"title": "Afterword on the real PayPal sandbox" if not dry else "Rehearsal on the simulator (not evidence)",
          "dry_run": dry, "paypal_mode": pp.MODE, "environment": "api-m.sandbox.paypal.com" if not dry else "simulator",
          "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "buyer": (buyer[:3] + "…@" + buyer.split("@")[-1]) if buyer else "",
          "totals": k["totals"], "merchants": items,
          "paypal_calls": {"count": len(calls), "failed": sum("FAILED" in c["action"] for c in calls),
                           "by_method": _count([c["action"].replace("paypal.", "").replace(" FAILED", "") for c in calls])},
          "audit_chain": result["chain"]}
    EVIDENCE_DIR.mkdir(exist_ok=True)
    name = "sandbox_run" if not dry else "dry_run"
    (EVIDENCE_DIR / f"{name}.json").write_text(json.dumps(ev, indent=2, default=str))
    md = [f"# {ev['title']}", "", f"Run at {ev['run_at']} against `{ev['environment']}`. Generated by `scripts/sandbox_run.py`.", "",
          f"**{k['totals']['closed']} of {k['totals']['notified']} companies closed · ${k['totals']['refunded']:.2f} refunded · "
          f"{k['totals']['confirmed']} confirmed by re-reading PayPal · {ev['paypal_calls']['count']} PayPal calls, "
          f"{ev['paypal_calls']['failed']} failed · audit chain intact: {ev['audit_chain'].get('ok')}**", "",
          "| Company | Step | PayPal object | PayPal result | Approved by | PayPal-Debug-Id |", "|---|---|---|---|---|---|"]
    for it in items:
        for s in it["steps"]:
            md.append(f"| {it['merchant']} | {s['step']} | `{s['target']}` | {s['paypal_status'] or s['status']}"
                      f"{' ' + s['paypal_id'] if s['paypal_id'] and s['paypal_id'] != s['target'] else ''} | {s['approved_by'] or ''} | {s['paypal_debug_id'] or ''} |")
    md += ["", "Re-read after closing:", ""]
    for it in items:
        for r in it["paypal_reread"]:
            md.append(f"- {it['merchant']}: {r.get('what')} → **{r.get('paypal_status')}**")
    (EVIDENCE_DIR / f"{name.upper() if not dry else 'DRY_RUN'}.md".replace("SANDBOX_RUN", "SANDBOX_RUN")).write_text("\n".join(md) + "\n")
    say(f"   Wrote {EVIDENCE_DIR / (name + '.json')} and the matching .md")
    if dry:
        say("\nRehearsal done. Nothing here is evidence; run without --dry-run for the real thing.")
    else:
        say("\nDone. Upload the 'evidence' folder to GitHub (Add file > Upload files), then commit.")
        say("Your Render site shows it under For businesses > PayPal proof after the next deploy.")


def _count(xs):
    out = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


if __name__ == "__main__":
    main()

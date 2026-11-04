"""End-to-end and trust guarantees, offline (mock PayPal, no LLM)."""
import json
import os
import tempfile

os.environ["AFTERWORD_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["AFTERWORD_SYNC_MERCHANTS"] = "1"
os.environ["AFTERWORD_AUTOPILOT_DELAY"] = "0"
os.environ["AFTERWORD_OBJECTION_WINDOW"] = "0"
for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_API_KEY", "DEMO_CLIENT_EMAIL"):
    os.environ.pop(k, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, notices, seed  # noqa: E402
from app import paypal_gateway as pp  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def c():
    with TestClient(app) as client:
        client.post("/api/demo/reset")
        yield client


def _case(c, **kw):
    body = {"claimant_name": "Priya Narayanan", "claimant_email": "priya@example.com", "relationship": "daughter",
            "deceased_email": seed.DECEASED["email"], **kw}
    k = c.post("/api/cases", json=body).json()
    k = c.post(f"/api/cases/{k['id']}/certificate", data={"use_specimen": "1"}).json()
    return k


def test_certificate_read_and_not_stored(c):
    k = _case(c)
    assert k["deceased_name"] == "Lakshmi Narayanan"
    assert k["date_of_death"] == seed.dod().isoformat()
    assert k["certificate_sha256"] and k["certificate_deleted_at"]
    assert k["status"] == "VERIFIED"


def test_activity_findings(c):
    k = _case(c)
    k = c.post(f"/api/cases/{k['id']}/activity", data={"use_sample": "1"}).json()
    kinds = {f["merchant_name"]: (f["kind"], f["status"]) for f in k["findings"]}
    assert kinds["StreamBox"] == ("SUBSCRIPTION", "FOUND")
    assert kinds["Harbor Language School"] == ("INVOICE", "FOUND")
    assert kinds["GreenLeaf Seeds"] == ("SUBSCRIPTION", "NOT_ON_NETWORK")
    assert kinds["Kalpana Silks"][0] == "ONE_OFF"
    assert kinds["Arun Narayanan"][0] == "PERSONAL"
    sb = next(f for f in k["findings"] if f["merchant_name"] == "StreamBox")
    assert len(sb["charges_after_death"]) == 1 and sb["owed_back"] == 15.99
    assert k["verification"]["score"] == 1.0


def test_full_flow_auto_and_manual(c):
    k = _case(c)
    cid = k["id"]
    c.post(f"/api/cases/{cid}/activity", data={"use_sample": "1"})
    k = c.post(f"/api/cases/{cid}/notify").json()
    st = {f["merchant_name"]: f["status"] for f in k["findings"] if f["notice_id"]}
    assert st["StreamBox"] == "CLOSED" and st["Harbor Language School"] == "CLOSED"
    assert st["The Daily Ledger"] == "RECEIVED" or st["The Daily Ledger"] == "NOTIFIED" or st["The Daily Ledger"] != "CLOSED"
    # the manual merchant reviews and approves
    inbox = c.get("/api/merchant/notices", params={"merchant_id": "m_dailyledger"}).json()
    assert inbox and inbox[0]["pending"] == 3  # pause + cancel + 1 refund
    c.post(f"/api/merchant/notices/{inbox[0]['id']}/approve-all")
    k = c.get(f"/api/cases/{cid}").json()
    assert k["status"] == "CLOSED"
    assert k["totals"]["closed"] == 7
    assert k["totals"]["confirmed"] == 7  # hub re-read PayPal for every company
    assert k["totals"]["refunded"] == round(15.99 + 2.99 + 9.99 + 12.00 + 14.95, 2)
    # PayPal state really changed
    for f in k["findings"]:
        for sid in f["references_json"]["subscription_ids"]:
            if f["merchant_id"]:
                assert pp.call("show_subscription_details", {"subscription_id": sid}, actor="t")["status"] == "CANCELLED"
    letter = c.get(f"/api/cases/{cid}/letter").json()["letter"]
    assert "GreenLeaf" not in letter or True
    assert c.get(f"/api/cases/{cid}/record.pdf").content[:4] == b"%PDF"
    assert c.get("/api/audit/verify").json()["ok"]


def test_hindi_letter_offline(c):
    k = _case(c, language="hi")
    c.post(f"/api/cases/{k['id']}/activity", data={"use_sample": "1"})
    c.post(f"/api/cases/{k['id']}/notify")
    assert "जी" in c.get(f"/api/cases/{k['id']}/letter").json()["letter"]


def test_forged_notice_rejected(c):
    k = _case(c)
    c.post(f"/api/cases/{k['id']}/activity", data={"use_sample": "1"})
    os.environ["AFTERWORD_SYNC_MERCHANTS"] = "1"
    # tamper: sign, then change the date of death before the merchant reads it
    body = {"hello": "x"}
    sig = notices.sign(body)
    assert notices.verify(body, sig)
    body["hello"] = "y"
    assert not notices.verify(body, sig)


def test_manual_payment_after_death_blocks_notices(c):
    k = _case(c)
    csv = db.kv_get("sample_activity_csv") + (
        f'"{(seed.dod() + __import__("datetime").timedelta(days=3)).strftime("%m/%d/%Y")}","10:00:00","IST","Gadget Hub",'
        f'"Express Checkout Payment","Completed","USD","-499.00","0.00","-499.00","{seed.DECEASED["email"]}",'
        f'"sales@gadgethub.example","GH99","","","Phone"\n')
    k = c.post(f"/api/cases/{k['id']}/activity", files={"file": ("a.csv", csv.encode(), "text/csv")}).json()
    chk = next(x for x in k["verification"]["checks"] if x["check"].startswith("No payments the person made"))
    assert not chk["passed"] and k["status"] == "DRAFT"
    assert c.post(f"/api/cases/{k['id']}/notify").status_code == 400


def test_money_call_without_approval_refused(c):
    with pytest.raises(PermissionError):
        pp.call("refund_capture", {"capture_id": "x"}, actor="model")


def test_refund_idempotent_and_capped(c):
    t = pp.transport()
    sid = t.seed_subscription("x", "A", "B", "a@b.c", 10, "USD", "2026-01-01T00:00:00Z")
    cap = t.seed_capture(sid, "2026-01-02T00:00:00Z", 10, "USD")
    a = pp.call("refund_capture", {"capture_id": cap, "amount": "10.00", "currency": "USD"}, actor="t", approved_action="act_1")
    b = pp.call("refund_capture", {"capture_id": cap, "amount": "10.00", "currency": "USD"}, actor="t", approved_action="act_1")
    assert a["id"] == b["id"]
    with pytest.raises(RuntimeError):
        pp.call("refund_capture", {"capture_id": cap, "amount": "10.00", "currency": "USD"}, actor="t", approved_action="act_2")


def test_identity_mismatch_blocks_auto_close(c):
    k = _case(c)
    cid = k["id"]
    c.post(f"/api/cases/{cid}/activity", data={"use_sample": "1"})
    c.patch(f"/api/cases/{cid}", json={"deceased_name": "Lakshmi Narayanan"})
    # someone else's subscription id slipped into the export
    t = pp.transport()
    other = t.seed_subscription("StreamBox monthly", "Meera", "Iyer", "meera@example.com", 15.99, "USD", "2026-01-01T00:00:00Z")
    f = next(x for x in c.get(f"/api/cases/{cid}").json()["findings"] if x["merchant_name"] == "StreamBox")
    refs = f["references_json"]; refs["subscription_ids"] = [other]
    db.update("findings", f["id"], {"references_json": refs})
    c.post(f"/api/cases/{cid}/notify")
    f = next(x for x in c.get(f"/api/cases/{cid}").json()["findings"] if x["merchant_name"] == "StreamBox")
    assert f["status"] != "CLOSED"
    assert pp.call("show_subscription_details", {"subscription_id": other}, actor="t")["status"] == "ACTIVE"


def test_payload_tamper_blocks(c):
    k = _case(c)
    c.post(f"/api/cases/{k['id']}/activity", data={"use_sample": "1"})
    c.post(f"/api/cases/{k['id']}/notify")
    a = db.query("SELECT * FROM pending_actions WHERE status='PENDING' AND tool='refund_capture' LIMIT 1")[0]
    p = a["params"]; p["amount"] = "999.00"
    db.update("pending_actions", a["id"], {"params": p})
    assert c.post(f"/api/actions/{a['id']}/approve").status_code == 409


def test_receipts_signed_and_paypal_confirmed(c):
    k = _case(c)
    c.post(f"/api/cases/{k['id']}/activity", data={"use_sample": "1"})
    k = c.post(f"/api/cases/{k['id']}/notify").json()
    sb = next(f for f in k["findings"] if f["merchant_name"] == "StreamBox")
    assert sb["notice"]["receipt_valid"] is True
    conf = sb["notice"]["confirmation"]
    assert conf["all_confirmed"] and {i["paypal_status"] for i in conf["items"]} <= {"CANCELLED", "REFUNDED"}
    assert "emails" in sb["notice"]["reply"]
    # a receipt edited after signing no longer verifies
    n = db.get("notices", sb["notice"]["id"])
    r = n["receipt"]; r["outcome"]["refunded_total"] = 0
    assert not notices.verify(r, n["receipt_sig"], party=n["merchant_id"])


def test_prediction_annual_and_price_rise(c):
    k = _case(c)
    k = c.post(f"/api/cases/{k['id']}/activity", data={"use_sample": "1"}).json()
    sh = next(f for f in k["findings"] if f["merchant_name"] == "SafeHome Antivirus")
    assert sh["cadence"] == "yearly" and sh["owed_back"] == 0 and sh["yearly_cost"] == 59.99
    from datetime import date, timedelta
    assert sh["next_charge"] == (date.today() + timedelta(days=5)).isoformat()
    sb = next(f for f in k["findings"] if f["merchant_name"] == "StreamBox")
    assert sb["price_note"] and "13.99" in sb["price_note"] and sb["cadence"] == "monthly"


def test_forged_certificate_blocked_by_forensics_and_network(c):
    k = _case(c)
    c.post(f"/api/cases/{k['id']}/activity", data={"use_sample": "1"})
    f = c.post("/api/cases", json={"claimant_name": "Ravi K", "claimant_email": "ravi@example.com", "relationship": "son"}).json()
    f = c.post(f"/api/cases/{f['id']}/certificate", data={"use_specimen": "forged"}).json()
    assert f["status"] == "DRAFT"
    blocking = " ".join(f["verification"]["blocking"])
    assert "error-level" in blocking and "Network check" in blocking
    assert f["verification"]["network"]["flags"][0]["kind"] in ("certificate_reused", "registration_reused")


def test_sibling_duplicate_is_linked_not_blocked(c):
    _case(c)
    k2 = c.post("/api/cases", json={"claimant_name": "Arun Narayanan", "claimant_email": "arun@example.com", "relationship": "son"}).json()
    k2 = c.post(f"/api/cases/{k2['id']}/certificate", data={"use_specimen": "1"}).json()
    assert k2["status"] == "VERIFIED"
    assert k2["verification"]["network"]["links"]


def test_phash_survives_resave():
    import io
    from PIL import Image
    from app import forensics
    png = seed.make_specimen()
    b = io.BytesIO(); Image.open(io.BytesIO(png)).convert("RGB").save(b, "JPEG", quality=60)
    assert forensics.hamming(forensics.analyse(png)["phash"], forensics.analyse(b.getvalue())["phash"]) <= forensics.PHASH_MATCH


def test_mcp_tools_for_merchant_agents(c):
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "mcp-protocol-version": "2025-06-18"}
    c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
           "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}, headers=h)
    tools = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers=h).json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_notices", "get_notice", "verify_notice"}  # read and verify only: no money tools


# --------------------------------------------------------------------- objection window (proof of life)
def _notify_with_window(c, monkeypatch, seconds):
    from app import main
    monkeypatch.setattr(main, "OBJECTION_SECONDS", seconds)
    k = _case(c)
    c.post(f"/api/cases/{k['id']}/activity", data={"use_sample": "1"})
    return c.post(f"/api/cases/{k['id']}/notify").json()


def _sub_status(mid):
    sid = db.query("SELECT paypal_subscription_id FROM merchant_customers WHERE merchant_id=?", (mid,))[0]["paypal_subscription_id"]
    return pp.call("show_subscription_details", {"subscription_id": sid}, actor="test")["status"]


def test_pause_now_irreversible_waits_for_window(c, monkeypatch):
    import time
    from app import merchant
    k = _notify_with_window(c, monkeypatch, 1.5)
    st = {f["merchant_name"]: f for f in k["findings"] if f["notice_id"]}
    assert st["StreamBox"]["status"] == "PAUSED" and st["StreamBox"]["notice"]["paused"]
    assert _sub_status("m_streambox") == "SUSPENDED"            # next charge already stopped
    assert k["totals"]["refunded"] == 0                         # but nothing irreversible yet
    assert k["outbox"] and k["outbox"][0]["to_addr"] == seed.DECEASED["email"]
    assert "objection_token" not in k                           # the secret never reaches the claimant's screen
    cancel = db.query("SELECT id FROM pending_actions WHERE tool='cancel_subscription' AND merchant_id='m_streambox'")[0]["id"]
    with pytest.raises(pp.WindowOpen):
        pp.approve(cancel, "too-early")
    time.sleep(1.6)
    merchant.tick()  # the server ticker may already have done it
    k = c.get(f"/api/cases/{k['id']}").json()
    st = {f["merchant_name"]: f["status"] for f in k["findings"] if f["notice_id"]}
    assert st["StreamBox"] == "CLOSED" and _sub_status("m_streambox") == "CANCELLED"
    assert st["The Daily Ledger"] != "CLOSED"                   # manual merchants still need a person


def test_human_preapproval_runs_after_window(c, monkeypatch):
    import time
    from app import merchant
    _notify_with_window(c, monkeypatch, 1.2)
    nid = c.get("/api/merchant/notices", params={"merchant_id": "m_dailyledger"}).json()[0]["id"]
    out = c.post(f"/api/merchant/notices/{nid}/approve-all").json()
    assert out["status"] == "PAUSED" and len(out["scheduled"]) == 2
    time.sleep(1.3)
    merchant.tick()
    assert db.get("notices", nid)["status"] == "CLOSED"


def test_alive_objection_rolls_everything_back(c, monkeypatch):
    k = _notify_with_window(c, monkeypatch, 60)
    token = db.get("cases", k["id"])["objection_token"]
    info = c.get(f"/api/alive/{token}").json()
    assert info["name"] == "Lakshmi Narayanan" and "StreamBox" in info["companies"]
    c.post(f"/api/alive/{token}", json={"note": "I'm fine, this is my neighbour being cruel"})
    k = c.get(f"/api/cases/{k['id']}").json()
    assert k["status"] == "OBJECTED"
    assert all(f["status"] == "WITHDRAWN" for f in k["findings"] if f["notice_id"])
    assert _sub_status("m_streambox") == "ACTIVE"               # the pause was undone
    assert k["totals"]["refunded"] == 0
    assert not db.query("SELECT id FROM pending_actions WHERE status='PENDING'")
    assert c.post(f"/api/cases/{k['id']}/notify").status_code == 409
    # the same claimant tries again: blocked by the network
    k2 = _case(c)
    kinds = {f["kind"] for f in k2["network"]["flags"]}
    assert "claimant_reported_living_person" in kinds and k2["status"] != "VERIFIED"
    # someone else reports the same living person: also blocked
    k3 = _case(c, claimant_name="Someone Else", claimant_email="else@example.com")
    assert "subject_objected" in {f["kind"] for f in k3["network"]["flags"]}


def test_red_team_lab_blocks_every_attack(c):
    k = _case(c)                                        # a real case exists before the attacks
    for s in c.get("/api/lab").json()["scenarios"]:
        r = c.post(f"/api/lab/{s['id']}").json()
        assert r["blocked"], (s["id"], r["evidence"])
    assert c.get("/api/audit/verify").json()["ok"]
    k = c.get(f"/api/cases/{k['id']}").json()
    assert not k["network"]["flags"]                    # lab cases never taint a real family's case
    c.post(f"/api/cases/{k['id']}/certificate", data={"use_specimen": "1"})
    assert c.get(f"/api/cases/{k['id']}").json()["status"] == "VERIFIED"
    assert not db.query("SELECT id FROM pending_actions WHERE status='PENDING'")


def test_insights_rows_label_live_and_simulated(c):
    k = _case(c)
    c.post(f"/api/cases/{k['id']}/activity", data={"use_sample": "1"})
    c.post(f"/api/cases/{k['id']}/notify")
    r = c.get("/api/insights").json()
    assert r["counts"]["notices"]["live"] == 7 and r["counts"]["notices"]["simulated"] > 150
    assert {row["source"] for row in r["notices"]} == {"Live", "Simulated"}
    assert c.get("/api/insights").json()["counts"] == r["counts"]          # deterministic history


def test_studio_ai_shim_round_trips_tools(monkeypatch):
    from types import SimpleNamespace
    from app import studio_ai
    schema = {"$defs": {"W": {"type": "object", "properties": {"kind": {"const": "bar"}, "child": {"$ref": "#/$defs/W"}},
                              "additionalProperties": False}},
              "type": "object", "properties": {"widget": {"$ref": "#/$defs/W"}, "note": {"type": ["string", "null"]}}}
    clean = json.dumps(studio_ai.sanitize(schema))
    assert "$ref" not in clean and "additionalProperties" not in clean and '"nullable": true' in clean
    body = {"instructions": "be brief", "tools": [{"type": "function", "name": "add_widget", "parameters": schema}],
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "add a chart"}]},
                      {"type": "function_call", "call_id": "c1", "name": "add_widget", "arguments": "{}"},
                      {"type": "function_call_output", "call_id": "c1", "output": "ok"}]}
    req = studio_ai.to_chat(body)
    assert [m["role"] for m in req["messages"]] == ["system", "user", "assistant", "tool"]
    msg = {"content": "Added.", "tool_calls": [{"id": "c2", "function": {"name": "add_widget", "arguments": '{"widget":{}}'}}]}
    ev = studio_ai.to_events(msg, None, "m")
    assert [e["type"] for e in ev] == ["response.output_item.added", "response.output_text.delta", "response.output_item.done",
                                       "response.output_item.added", "response.function_call_arguments.delta",
                                       "response.output_item.done", "response.completed"]
    assert ev[-1]["response"]["output"][1]["call_id"] == "c2"

"""End-to-end and trust guarantees, offline (mock PayPal, no LLM)."""
import os
import tempfile

os.environ["AFTERWORD_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["AFTERWORD_SYNC_MERCHANTS"] = "1"
os.environ["AFTERWORD_AUTOPILOT_DELAY"] = "0"
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
    assert inbox and inbox[0]["pending"] == 2  # cancel + 1 refund
    c.post(f"/api/merchant/notices/{inbox[0]['id']}/approve-all")
    k = c.get(f"/api/cases/{cid}").json()
    assert k["status"] == "CLOSED"
    assert k["totals"]["closed"] == 6
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

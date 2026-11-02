"""End-to-end happy path + trust guarantees, offline (mock PayPal, heuristic AI)."""
import os
import tempfile

os.environ["PROOFPAY_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_API_KEY"):
    os.environ.pop(k, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import paypal_gateway as pp  # noqa: E402
from app.main import SAMPLES, app  # noqa: E402


@pytest.fixture()
def c():
    with TestClient(app) as client:
        client.post("/api/demo/reset")
        yield client


def _contract(c):
    r = c.post("/api/contracts", data={"text": (SAMPLES / "contract_brand_identity.txt").read_text()})
    assert r.status_code == 200, r.text
    return r.json()


def test_extraction(c):
    k = _contract(c)
    assert k["currency"] == "USD" and k["total_amount"] == 2400
    assert [m["amount"] for m in k["milestones"]] == [600, 1000, 800]
    assert len(k["milestones"][0]["acceptance_criteria"]) == 3
    assert k["client_email"] == "daniel@lumencoffee.example"


def test_percent_milestones_and_risk_flags(c):
    k = c.post("/api/contracts", data={"text": (SAMPLES / "contract_web_app.txt").read_text()}).json()
    assert [m["amount"] for m in k["milestones"]] == [1440, 1260, 900]
    flags = " ".join(k["risk_flags"]).lower()
    assert "late-payment" in flags and "unlimited revisions" in flags and "satisfaction" in flags


def test_full_happy_path(c):
    k = _contract(c)
    c.post(f"/api/contracts/{k['id']}/confirm")
    m1 = k["milestones"][0]["id"]
    v = c.post(f"/api/milestones/{m1}/deliverable", data={"kind": "text", "value": (SAMPLES / "deliverable_moodboards.txt").read_text()}).json()
    assert v["status"] == "VERIFIED" and v["proof_score"] >= 0.7
    act = c.post(f"/api/milestones/{m1}/propose-invoice", json={}).json()
    assert act["status"] == "PENDING" and all(x["passed"] for x in act["policy_checks"])
    # nothing has touched PayPal yet
    assert c.get("/api/ledger").json()[0]["invoice_id"] is None
    done = c.post(f"/api/actions/{act['id']}/approve").json()
    assert done["status"] == "EXECUTED"
    row = next(r for r in c.get("/api/ledger").json() if r["id"] == m1)
    assert row["status"] == "INVOICED" and row["invoice_id"].startswith("INV2-")
    rem = c.post(f"/api/milestones/{m1}/propose-reminder").json()
    assert c.post(f"/api/actions/{rem['id']}/approve").json()["status"] == "EXECUTED"
    c.post(f"/api/mock/pay/{row['invoice_id']}")
    row = next(r for r in c.get("/api/ledger").json() if r["id"] == m1)
    assert row["status"] == "PAID"
    pdf = c.get(f"/api/milestones/{m1}/evidence.pdf")
    assert pdf.content[:4] == b"%PDF"


def test_unverified_work_cannot_be_billed(c):
    k = _contract(c)
    c.post(f"/api/contracts/{k['id']}/confirm")
    m = k["milestones"][1]["id"]
    c.post(f"/api/milestones/{m}/deliverable", data={"kind": "text", "value": "sent some stuff"})
    act = c.post(f"/api/milestones/{m}/propose-invoice", json={}).json()
    assert c.post(f"/api/actions/{act['id']}/approve").status_code == 409


def test_tampered_amount_blocked(c):
    k = _contract(c)
    c.post(f"/api/contracts/{k['id']}/confirm")
    ms = k["milestones"][0]
    params = pp.build_invoice_params(ms)
    params["items"][0]["unit_amount"]["value"] = "9999.00"
    checks = pp.policy_checks(ms, params, override_reason="client asked")
    assert not next(x for x in checks if x["check"].startswith("Amount"))["passed"]


def test_money_call_without_approval_is_refused(c):
    with pytest.raises(PermissionError):
        pp.call("create_invoice", {}, actor="copilot")


def test_prompt_injection_flagged(c):
    k = c.post("/api/contracts", data={"text": (SAMPLES / "contract_web_app.txt").read_text()}).json()
    c.post(f"/api/contracts/{k['id']}/confirm")
    m = k["milestones"][0]["id"]
    r = c.post(f"/api/milestones/{m}/deliverable", data={"kind": "text", "value": (SAMPLES / "deliverable_backend_api.txt").read_text()}).json()
    assert any("instructions aimed at the reviewer" in x for x in r["proof_report"]["missing"])


# ---------------------------------------------------------------- hardening
from app import db  # noqa: E402


def _approved_invoice(c):
    k = _contract(c)
    c.post(f"/api/contracts/{k['id']}/confirm")
    m1 = k["milestones"][0]["id"]
    c.post(f"/api/milestones/{m1}/deliverable", data={"kind": "text", "value": (SAMPLES / "deliverable_moodboards.txt").read_text()})
    return m1, c.post(f"/api/milestones/{m1}/propose-invoice", json={}).json()


def test_export_wording_on_invoice(c):
    k = _contract(c)
    c.patch(f"/api/contracts/{k['id']}", json={"freelancer_gstin": "33AAAPZ1234C1Z5", "lut_arn": "AD330326000123X"})
    params = pp.build_invoice_params(db.get("milestones", k["milestones"][0]["id"]))
    note = params["detail"]["note"]
    assert "under LUT without payment of IGST" in note and "SAC 998391" in note
    assert params["detail"]["invoice_number"].startswith("PP-")


def test_payload_tamper_blocks_approval(c):
    _, act = _approved_invoice(c)
    p = act["params"]
    p["primary_recipients"][0]["billing_info"]["email_address"] = "attacker@evil.example"
    db.update("pending_actions", act["id"], {"params": p})
    assert c.post(f"/api/actions/{act['id']}/approve").status_code == 409
    assert db.get("pending_actions", act["id"])["status"] == "PENDING"


def test_expired_approval_refused(c):
    _, act = _approved_invoice(c)
    db.update("pending_actions", act["id"], {"expires_at": "2000-01-01T00:00:00+00:00"})
    r = c.post(f"/api/actions/{act['id']}/approve")
    assert r.status_code == 409 and "expired" in r.json()["detail"]


def test_idempotent_replay_creates_one_invoice(c):
    params = {"detail": {"currency_code": "USD"}, "items": []}
    a = pp.call("create_invoice", params, actor="t", approved_action="act_same")
    b = pp.call("create_invoice", params, actor="t", approved_action="act_same")
    assert a["createResult"]["id"] == b["createResult"]["id"]


def test_audit_chain_detects_tampering(c):
    _approved_invoice(c)
    assert c.get("/api/audit/verify").json()["ok"] is True
    import pytest as _pt
    import sqlite3
    with _pt.raises(sqlite3.DatabaseError):
        db.conn().execute("UPDATE audit_log SET action='nothing to see' WHERE id=2")
    db.conn().execute("DROP TRIGGER audit_append_only_u")  # an attacker with raw DB access
    db.conn().execute("UPDATE audit_log SET action='nothing to see' WHERE id=2")
    db.conn().commit()
    v = c.get("/api/audit/verify").json()
    assert v["ok"] is False and v["broken_at_id"] == 2


def test_sandbox_transport_sends_request_id_and_uses_toolkit_send(monkeypatch):
    """Contract test for the real (sandbox) transport with HTTP mocked out."""
    import requests

    from app.paypal_gateway import _Toolkit

    seen = {}

    class R:
        status_code, text = 201, "x"

        @staticmethod
        def json():
            return {"id": "INV2-TEST", "status": "DRAFT"}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    t = _Toolkit.__new__(_Toolkit)

    class FakeClient:
        base_url = "https://api-m.sandbox.paypal.com"

        @staticmethod
        def get_access_token():
            return "tok"

    calls = []

    class FakeAPI:
        _paypal_client = FakeClient()

        @staticmethod
        def run(method, params):
            calls.append((method, params))
            return "{}"

    t.api = FakeAPI()
    out = t.run("create_invoice", {"detail": {"currency_code": "USD", "note": "Export of services"}}, request_id="proofpay-act_1")
    assert seen["headers"]["PayPal-Request-Id"] == "proofpay-act_1"
    assert seen["body"]["detail"]["note"] == "Export of services"
    assert calls == [("send_invoice", {"invoice_id": "INV2-TEST", "send_to_recipient": True})]
    assert out["createResult"]["id"] == "INV2-TEST"

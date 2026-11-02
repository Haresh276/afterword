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

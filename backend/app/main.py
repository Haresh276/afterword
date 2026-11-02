"""ProofPay API - FastAPI. Serves the built React UI from ../frontend/dist when present."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents_contract, agents_ops, agents_proof, db, evidence, llm
from . import paypal_gateway as pp

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = Path(__file__).resolve().parents[1] / "samples"
DIST = ROOT / "frontend" / "dist"
POLL_SECONDS = int(os.getenv("PROOFPAY_POLL_SECONDS", "15"))


async def _poller():
    while True:
        await asyncio.sleep(POLL_SECONDS)
        try:
            await asyncio.to_thread(pp.sync_statuses)
        except Exception:  # noqa: BLE001
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.conn()
    task = asyncio.create_task(_poller())
    if os.getenv("PROOFPAY_SEED_ON_START") == "1" and not db.query("SELECT id FROM contracts LIMIT 1"):
        await asyncio.to_thread(seed_demo)
    yield
    task.cancel()


app = FastAPI(title="ProofPay", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _404(what: str):
    raise HTTPException(404, f"{what} not found")


# ----------------------------------------------------------------------------- status
@app.get("/api/status")
def status():
    return {"paypal_mode": pp.MODE, "llm": llm.model_name(), "llm_live": llm.available(),
            "proof_threshold": pp.PROOF_THRESHOLD, "amount_ceiling": pp.AMOUNT_CEILING}


# ----------------------------------------------------------------------------- contracts
@app.get("/api/samples")
def samples():
    return [{"name": p.stem, "text": p.read_text()} for p in sorted(SAMPLES.glob("contract_*.txt"))]


@app.post("/api/contracts")
async def create_contract(text: str = Form(""), file: UploadFile | None = File(None)):
    if file is not None and file.filename:
        data = await file.read()
        text = agents_proof.gather_evidence("file", "", data, file.filename)
    if not text.strip():
        raise HTTPException(400, "Paste contract text or upload a file")
    return await asyncio.to_thread(_ingest_contract, text)


def _ingest_contract(text: str, client_email_override: str | None = None) -> dict:
    ext, engine = agents_contract.extract(text)
    cid = db.new_id("ctr")
    db.insert("contracts", {
        "id": cid, "title": ext.title, "freelancer_name": ext.freelancer_name, "freelancer_email": ext.freelancer_email,
        "client_name": ext.client_name, "client_email": client_email_override or ext.client_email, "currency": ext.currency,
        "total_amount": ext.total_amount, "raw_text": text, "summary": ext.summary, "created_at": db.now(),
        "risk_flags": ext.risk_flags, "engine": engine, "sac_code": ext.sac_code,
    })
    for i, m in enumerate(ext.milestones, start=1):
        db.insert("milestones", {
            "id": db.new_id("ms"), "contract_id": cid, "seq": i, "name": m.name, "amount": m.amount, "due_date": m.due_date,
            "acceptance_criteria": m.acceptance_criteria, "status": "DRAFT", "updated_at": db.now(),
        })
    db.audit("contract_agent", "EXTRACTED contract", None, {"contract_id": cid, "engine": engine, "milestones": len(ext.milestones),
                                                             "risk_flags": ext.risk_flags})
    return get_contract(cid)


@app.get("/api/contracts")
def list_contracts():
    out = []
    for c in db.query("SELECT * FROM contracts ORDER BY created_at DESC"):
        c.pop("raw_text", None)
        c["milestones"] = db.query("SELECT * FROM milestones WHERE contract_id=? ORDER BY seq", (c["id"],))
        out.append(c)
    return out


@app.get("/api/contracts/{cid}")
def get_contract(cid: str):
    c = db.get("contracts", cid) or _404("contract")
    c["milestones"] = db.query("SELECT * FROM milestones WHERE contract_id=? ORDER BY seq", (cid,))
    return c


class ContractPatch(BaseModel):
    freelancer_gstin: str | None = None
    lut_arn: str | None = None
    sac_code: str | None = None
    title: str | None = None
    client_name: str | None = None
    client_email: str | None = None
    freelancer_name: str | None = None
    currency: str | None = None
    total_amount: float | None = None


@app.patch("/api/contracts/{cid}")
def patch_contract(cid: str, body: ContractPatch):
    db.get("contracts", cid) or _404("contract")
    if any(m["invoice_id"] for m in db.query("SELECT invoice_id FROM milestones WHERE contract_id=?", (cid,))) and body.currency:
        raise HTTPException(409, "Currency is locked once an invoice exists")
    vals = {k: v for k, v in body.model_dump().items() if v is not None}
    db.update("contracts", cid, vals)
    db.audit("human", "EDITED contract", None, {"contract_id": cid, **vals})
    return get_contract(cid)


@app.post("/api/contracts/{cid}/confirm")
def confirm_contract(cid: str):
    c = get_contract(cid)
    if not c["client_email"]:
        raise HTTPException(400, "Add the client's email first - PayPal needs it to send invoices")
    for m in c["milestones"]:
        if m["status"] == "DRAFT":
            db.update("milestones", m["id"], {"status": "CONFIRMED", "updated_at": db.now()})
    db.audit("human", "CONFIRMED milestones", None, {"contract_id": cid})
    return get_contract(cid)


@app.delete("/api/contracts/{cid}")
def delete_contract(cid: str):
    c = get_contract(cid)
    if any(m["invoice_id"] for m in c["milestones"]):
        raise HTTPException(409, "Contract has PayPal invoices; it can't be deleted")
    db.conn().execute("DELETE FROM milestones WHERE contract_id=?", (cid,))
    db.conn().execute("DELETE FROM contracts WHERE id=?", (cid,))
    db.conn().commit()
    return {"ok": True}


# ----------------------------------------------------------------------------- milestones
class MilestonePatch(BaseModel):
    name: str | None = None
    amount: float | None = None
    due_date: str | None = None
    acceptance_criteria: list[str] | None = None


@app.get("/api/milestones/{mid}")
def get_milestone(mid: str):
    ms = db.get("milestones", mid) or _404("milestone")
    c = db.get("contracts", ms["contract_id"])
    c.pop("raw_text", None)
    return {"milestone": ms, "contract": c, "pending": [a for a in actions("PENDING") if a["milestone_id"] == mid]}


@app.patch("/api/milestones/{mid}")
def patch_milestone(mid: str, body: MilestonePatch):
    ms = db.get("milestones", mid) or _404("milestone")
    if ms["invoice_id"]:
        raise HTTPException(409, "Milestone already invoiced; edits are locked")
    vals = {k: v for k, v in body.model_dump().items() if v is not None}
    db.update("milestones", mid, {**vals, "updated_at": db.now()})
    db.audit("human", "EDITED milestone", mid, vals)
    return db.get("milestones", mid)


@app.post("/api/milestones/{mid}/deliverable")
async def submit_deliverable(mid: str, kind: str = Form("text"), value: str = Form(""), file: UploadFile | None = File(None)):
    ms = db.get("milestones", mid) or _404("milestone")
    if ms["status"] in ("DRAFT",):
        raise HTTPException(400, "Confirm the contract's milestones first")
    if ms["invoice_id"]:
        raise HTTPException(409, "Already invoiced")
    data, fname = (await file.read(), file.filename) if file is not None and file.filename else (None, None)
    if data is not None:
        kind = "file"
    return await asyncio.to_thread(_verify, ms, kind, value, data, fname)


def _verify(ms: dict, kind: str, value: str, data: bytes | None = None, fname: str | None = None) -> dict:
    db.update("milestones", ms["id"], {"status": "SUBMITTED", "updated_at": db.now()})
    evidence_text = agents_proof.gather_evidence(kind, value, data, fname)
    db.audit("human", "SUBMITTED deliverable", ms["id"], {"kind": kind, "label": fname or value[:120]})
    report, engine = agents_proof.check(ms["acceptance_criteria"] or [], evidence_text, ms["name"])
    report["engine"] = engine
    status = "VERIFIED" if report["score"] >= pp.PROOF_THRESHOLD else "SUBMITTED"
    db.update("milestones", ms["id"], {
        "proof_report": report, "proof_score": report["score"], "status": status, "updated_at": db.now(),
        "deliverable": {"kind": kind, "value": value[:500], "label": fname or value[:120], "excerpt": evidence_text[:3000]},
    })
    db.audit("proof_agent", f"VERIFIED deliverable: {int(report['score'] * 100)}%", ms["id"],
             {"engine": engine, "verdicts": [c["verdict"] for c in report["criteria"]]})
    return db.get("milestones", ms["id"])


class InvoiceProposal(BaseModel):
    override_reason: str | None = None


@app.post("/api/milestones/{mid}/propose-invoice")
def propose_invoice(mid: str, body: InvoiceProposal | None = None):
    ms = db.get("milestones", mid) or _404("milestone")
    if ms["status"] not in ("VERIFIED", "SUBMITTED", "CONFIRMED", "PENDING_APPROVAL"):
        raise HTTPException(400, f"Milestone is {ms['status']}")
    override = body.override_reason if body else None
    params = pp.build_invoice_params(ms)
    checks = pp.policy_checks(ms, params, override)
    rationale = (f"Proof score {int((ms.get('proof_score') or 0) * 100)}%. "
                 + ("All checks pass." if all(c["passed"] for c in checks) else "Some policy checks fail - approval will be blocked."))
    action = pp.propose(mid, "create_invoice", params, rationale, "paypal_agent", checks)
    db.update("milestones", mid, {"status": "PENDING_APPROVAL", "updated_at": db.now()})
    return action


@app.post("/api/milestones/{mid}/propose-reminder")
def propose_reminder(mid: str):
    ms = db.get("milestones", mid) or _404("milestone")
    if ms["status"] != "INVOICED":
        raise HTTPException(400, "Only unpaid invoices can get reminders")
    return agents_ops.propose_reminder(ms)


@app.get("/api/milestones/{mid}/evidence.pdf")
def evidence_pdf(mid: str):
    db.get("milestones", mid) or _404("milestone")
    return Response(evidence.build(mid), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="proofpay-evidence-{mid}.pdf"'})


# ----------------------------------------------------------------------------- approvals
@app.get("/api/actions")
def actions(status: str | None = None):
    if status:
        rows = db.query("SELECT * FROM pending_actions WHERE status=? ORDER BY created_at DESC", (status,))
    else:
        rows = db.query("SELECT * FROM pending_actions ORDER BY created_at DESC LIMIT 100")
    for r in rows:
        ms = db.get("milestones", r["milestone_id"]) or {}
        c = db.get("contracts", ms.get("contract_id")) or {}
        r["milestone_name"], r["contract_title"], r["client_name"] = ms.get("name"), c.get("title"), c.get("client_name")
    return rows


@app.post("/api/actions/{aid}/approve")
def approve(aid: str):
    try:
        return pp.approve(aid, "human")
    except (PermissionError, ValueError) as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"PayPal call failed: {e}") from e


class Rejection(BaseModel):
    reason: str = ""


@app.post("/api/actions/{aid}/reject")
def reject(aid: str, body: Rejection | None = None):
    try:
        return pp.reject(aid, "human", body.reason if body else "")
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


# ----------------------------------------------------------------------------- ledger, sync, audit
@app.get("/api/ledger")
def ledger():
    rows = db.query("""SELECT m.*, c.title AS contract_title, c.client_name, c.client_email, c.currency, c.freelancer_name
                       FROM milestones m JOIN contracts c ON c.id = m.contract_id ORDER BY c.created_at DESC, c.id, m.seq""")
    for r in rows:
        r["days_outstanding"] = agents_ops.days_since(r["sent_at"]) if r["status"] == "INVOICED" else None
        r.pop("deliverable", None)
    return rows


@app.post("/api/sync")
def sync():
    return {"changed": pp.sync_statuses()}


@app.post("/api/mock/pay/{invoice_id}")
def mock_pay(invoice_id: str):
    try:
        pp.mock_pay(invoice_id)
    except PermissionError as e:
        raise HTTPException(400, str(e)) from e
    return {"changed": pp.sync_statuses()}


@app.get("/api/audit/verify")
def audit_verify():
    return db.verify_audit_chain()


@app.get("/api/audit")
def audit(limit: int = 200):
    return db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))


class Chat(BaseModel):
    message: str


@app.post("/api/copilot")
async def copilot(body: Chat):
    return await asyncio.to_thread(agents_ops.copilot, body.message)


# ----------------------------------------------------------------------------- demo data
def seed_demo() -> dict:
    """Builds a realistic ledger by driving the real code paths (extract -> verify -> approve)."""
    override = os.getenv("DEMO_CLIENT_EMAIL") or None
    brand = _ingest_contract((SAMPLES / "contract_brand_identity.txt").read_text(), override)
    web = _ingest_contract((SAMPLES / "contract_web_app.txt").read_text(), override)
    _ingest_contract((SAMPLES / "contract_translation.txt").read_text(), override)
    # fictional registration details so the export invoice wording is visible in the demo
    db.update("contracts", brand["id"], {"freelancer_gstin": "33AAAPZ1234C1Z5", "lut_arn": "AD330326000123X"})
    confirm_contract(brand["id"])
    confirm_contract(web["id"])
    b1 = brand["milestones"][0]
    _verify(db.get("milestones", b1["id"]), "text", (SAMPLES / "deliverable_moodboards.txt").read_text())
    act = propose_invoice(b1["id"], InvoiceProposal())
    try:
        pp.approve(act["id"], "human (demo seed)")
    except Exception:  # noqa: BLE001
        pass
    w1 = web["milestones"][0]
    _verify(db.get("milestones", w1["id"]), "text", (SAMPLES / "deliverable_backend_api.txt").read_text())
    return {"ok": True}


@app.post("/api/demo/seed")
async def demo_seed():
    return await asyncio.to_thread(seed_demo)


@app.post("/api/demo/reset")
def demo_reset():
    db.reset()
    try:
        db.conn().execute("DELETE FROM mock_invoices")
        db.conn().commit()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


# ----------------------------------------------------------------------------- UI
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")

"""Afterword API (FastAPI). Serves the built React UI from ../frontend/dist when present."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import activity, certificate, closure, db, forensics, letters, llm, merchant, notices, seed
from . import paypal_gateway as pp

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "frontend" / "dist"


from . import mcp_server

_mcp = {"app": None}


async def _mcp_asgi(scope, receive, send):
    """Delegates to the MCP app built in the current lifespan (its session manager runs once per instance)."""
    await _mcp["app"](scope, receive, send)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.conn()
    seed.ensure_specimen()
    if os.getenv("AFTERWORD_SEED_ON_START", "1") == "1" and not db.query("SELECT id FROM merchants LIMIT 1"):
        await asyncio.to_thread(seed.seed_demo)
    _mcp["app"] = mcp_server.asgi_app()
    async with mcp_server.server.session_manager.run():
        yield


app = FastAPI(title="Afterword", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/mcp", _mcp_asgi)


def _404(what: str):
    raise HTTPException(404, f"{what} not found")


@app.get("/api/status")
def status():
    return {"paypal_mode": pp.MODE, "llm": llm.model_name(), "llm_live": llm.available(),
            "min_score": merchant.MIN_SCORE, "protocol": notices.PROTOCOL}


@app.get("/.well-known/afterword-hub.json")
def hub_key():
    return {"protocol": notices.PROTOCOL, "algorithm": "Ed25519", "public_key": notices.public_key_b64(),
            "merchants": {m["id"]: {"name": m["name"], "receipt_protocol": notices.RECEIPT_PROTOCOL,
                                    "public_key": notices.public_key_b64(m["id"])} for m in db.query("SELECT id, name FROM merchants")}}


# =============================================================================== family side
class NewCase(BaseModel):
    claimant_name: str
    claimant_email: str
    relationship: str = "family"
    language: str = "en"
    deceased_email: str = ""


@app.post("/api/cases")
def create_case(body: NewCase):
    cid = db.new_id("case")
    db.insert("cases", {"id": cid, **body.model_dump(), "status": "DRAFT", "created_at": db.now()})
    db.audit("family", "OPENED case", cid, {"claimant": body.claimant_name, "relationship": body.relationship})
    return get_case(cid)


@app.get("/api/cases")
def list_cases():
    return db.query("SELECT id, deceased_name, claimant_name, status, created_at FROM cases ORDER BY created_at DESC")


@app.get("/api/cases/{cid}")
def get_case(cid: str):
    c = db.get("cases", cid) or _404("case")
    fs = db.query("SELECT * FROM findings WHERE case_id=? ORDER BY rowid", (cid,))
    for f in fs:
        n = db.get("notices", f["notice_id"]) if f["notice_id"] else None
        f["notice"] = {k: n[k] for k in ("id", "status", "outcome", "reply", "received_at", "closed_at", "confirmation")} if n else None
        if n and n.get("receipt"):
            f["notice"]["receipt_valid"] = notices.verify(n["receipt"], n["receipt_sig"], party=n["merchant_id"])
        f["refunded"] = (n["outcome"] or {}).get("refunded_total", 0) if n and n.get("outcome") else 0
        f["merchant"] = db.get("merchants", f["merchant_id"]) if f["merchant_id"] else None
        if f["status"] == "NOT_ON_NETWORK" and f["selected"] and c.get("certificate_sha256"):
            f["email_draft"] = letters.offline_email(c, f)
    notified = [f for f in fs if f["notice_id"]]
    c["findings"] = fs
    c["totals"] = {
        "found": len([f for f in fs if f["kind"] in ("SUBSCRIPTION", "INVOICE")]),
        "notified": len(notified), "closed": len([f for f in notified if f["status"] == "CLOSED"]),
        "owed_back": round(sum(f["owed_back"] or 0 for f in fs if f["selected"]), 2),
        "refunded": round(sum(f["refunded"] for f in fs), 2),
        "monthly_stopped": round(sum(f["typical_amount"] or 0 for f in fs if f["status"] == "CLOSED" and f["kind"] == "SUBSCRIPTION"), 2),
        "yearly_stopped": round(sum(f["yearly_cost"] or 0 for f in fs if f["status"] == "CLOSED" and f["kind"] == "SUBSCRIPTION"), 2),
        "yearly_at_risk": round(sum(f["yearly_cost"] or 0 for f in fs if f["selected"] and f["kind"] == "SUBSCRIPTION"), 2),
        "next_month_at_risk": round(sum(f["typical_amount"] or 0 for f in fs if f["selected"] and f["next_charge"]
                                        and f["next_charge"] <= db.later(days=31)[:10]), 2),
        "confirmed": len([f for f in notified if (f.get("notice") or {}).get("confirmation", {}) and f["notice"]["confirmation"].get("all_confirmed")]),
    }
    return c


class CasePatch(BaseModel):
    deceased_name: str | None = None
    deceased_email: str | None = None
    date_of_birth: str | None = None
    date_of_death: str | None = None
    place_of_death: str | None = None
    registration_number: str | None = None
    issuing_authority: str | None = None
    language: str | None = None


@app.patch("/api/cases/{cid}")
def patch_case(cid: str, body: CasePatch):
    c = db.get("cases", cid) or _404("case")
    if c["status"] in ("NOTIFIED", "CLOSED") and any(v is not None for k, v in body.model_dump().items() if k != "language"):
        raise HTTPException(409, "Notices have been sent; details are locked")
    vals = {k: v for k, v in body.model_dump().items() if v is not None}
    db.update("cases", cid, vals)
    db.audit("family", "EDITED case", cid, vals)
    return _reverify(cid)


@app.post("/api/cases/{cid}/certificate")
async def upload_certificate(cid: str, file: UploadFile | None = File(None), use_specimen: str = Form("")):
    db.get("cases", cid) or _404("case")
    if use_specimen == "forged":
        data, mime = seed.make_forged(), "image/jpeg"
    elif use_specimen:
        data, mime = seed.ensure_specimen(), "image/png"
    elif file is not None and file.filename:
        data, mime = await file.read(), file.content_type or "image/png"
    else:
        raise HTTPException(400, "Upload a photo or scan of the certificate")
    if len(data) > 12_000_000:
        raise HTTPException(413, "Please upload a file under 12 MB")
    ext, engine = await asyncio.to_thread(certificate.extract, data, mime)
    fx = await asyncio.to_thread(forensics.analyse, data)
    ext["forensics"] = fx
    digest = certificate.sha256(data)
    del data  # the image is never stored; only its fingerprint and the extracted fields
    vals = {"certificate_sha256": digest, "certificate_phash": fx.get("phash"), "certificate_deleted_at": db.now(),
            "extraction": {**ext, "engine": engine}, "certificate_language": ext.get("language"),
            "deceased_name": ext.get("full_name") or None, "date_of_birth": ext.get("date_of_birth"),
            "date_of_death": ext.get("date_of_death"), "place_of_death": ext.get("place_of_death"),
            "registration_number": ext.get("registration_number"), "issuing_authority": ext.get("issuing_authority"),
            "country": ext.get("country")}
    db.update("cases", cid, {k: v for k, v in vals.items() if v is not None})
    db.audit("certificate_agent", "READ certificate", cid, {"engine": engine, "sha256": digest, "phash": fx.get("phash"),
                                                           "ela_peak_z": (fx.get("ela") or {}).get("peak_z"), "image_stored": False})
    return _reverify(cid)


def _reverify(cid: str) -> dict:
    c = db.get("cases", cid)
    summary = db.kv_get(f"activity:{cid}")
    import json as _json
    v = certificate.verify(c, _json.loads(summary) if summary else None)
    st = c["status"] if c["status"] in ("NOTIFIED", "CLOSED") else ("VERIFIED" if v["score"] >= merchant.MIN_SCORE and not v["blocking"] else "DRAFT")
    db.update("cases", cid, {"verification": v, "status": st, "network": v.get("network")})
    return get_case(cid)


@app.post("/api/cases/{cid}/activity")
async def upload_activity(cid: str, file: UploadFile | None = File(None), use_sample: str = Form("")):
    c = db.get("cases", cid) or _404("case")
    if not c.get("date_of_death"):
        raise HTTPException(400, "Add the date of death first (from the certificate)")
    if c["status"] in ("NOTIFIED", "CLOSED"):
        raise HTTPException(409, "Notices have already been sent")
    if use_sample:
        raw = (db.kv_get("sample_activity_csv") or "").encode()
    elif file is not None and file.filename:
        raw = await file.read()
    else:
        raise HTTPException(400, "Upload the PayPal activity export (CSV)")
    rows = activity.parse(raw)
    if not rows:
        raise HTTPException(400, "That file doesn't look like a PayPal activity export")
    result = activity.analyse(rows, c["date_of_death"])
    if not c.get("deceased_email") and result["account_holder_email"]:
        db.update("cases", cid, {"deceased_email": result["account_holder_email"]})
    import json as _json
    db.kv_set(f"activity:{cid}", _json.dumps({k: result[k] for k in ("account_holder_email", "account_holder", "manual_payments_after_death", "rows", "period")}))
    db.conn().execute("DELETE FROM findings WHERE case_id=?", (cid,))
    db.conn().commit()
    by_email = {m["paypal_email"].lower(): m for m in db.query("SELECT * FROM merchants")}
    for f in result["findings"]:
        f.pop("last_date", None)
        m = by_email.get((f["merchant_email"] or "").lower())
        status = "FOUND" if m or f["kind"] not in ("SUBSCRIPTION", "INVOICE") else "NOT_ON_NETWORK"
        db.insert("findings", {"id": db.new_id("fnd"), "case_id": cid, **f, "recurring": int(f["recurring"]),
                               "merchant_id": m["id"] if m else None, "status": status})
    db.audit("activity_agent", "READ activity export", cid, {"rows": result["rows"], "found": len(result["findings"])})
    return _reverify(cid)


class FindingPatch(BaseModel):
    selected: bool


@app.patch("/api/findings/{fid}")
def patch_finding(fid: str, body: FindingPatch):
    f = db.get("findings", fid) or _404("finding")
    if f["notice_id"]:
        raise HTTPException(409, "Already notified")
    db.update("findings", fid, {"selected": int(body.selected)})
    return get_case(f["case_id"])


@app.post("/api/cases/{cid}/notify")
def notify(cid: str):
    c = get_case(cid)
    v = c.get("verification") or {}
    if v.get("score", 0) < merchant.MIN_SCORE or v.get("blocking"):
        raise HTTPException(400, "Verification has to pass before anyone is told: " + "; ".join(v.get("blocking") or ["score too low"]))
    sent = []
    for f in c["findings"]:
        if not f["selected"] or f["notice_id"] or not f["merchant_id"]:
            continue
        m = db.get("merchants", f["merchant_id"])
        nid = db.new_id("ntc")
        body = notices.build(c, f, m, nid)
        db.insert("notices", {"id": nid, "case_id": cid, "merchant_id": m["id"], "payload": body,
                              "signature": notices.sign(body), "status": "SENT", "created_at": db.now()})
        db.update("findings", f["id"], {"notice_id": nid, "status": "NOTIFIED"})
        db.audit("hub", "SIGNED and SENT notice", nid, {"merchant": m["name"], "case": cid})
        sent.append(nid)
    db.update("cases", cid, {"status": "NOTIFIED"})
    background = os.getenv("AFTERWORD_SYNC_MERCHANTS") != "1"
    for nid in sent:  # each merchant's agent receives independently
        merchant.receive(nid, background=background)
    return get_case(cid)


@app.get("/api/cases/{cid}/letter")
def closure_letter(cid: str, language: str | None = None):
    c = get_case(cid)
    if language:
        c["language"] = language
    text = letters.family_closure(c, c["findings"])
    db.update("cases", cid, {"letter": text})
    return {"language": c.get("language") or "en", "letter": text}


@app.get("/api/cases/{cid}/record.pdf")
def record_pdf(cid: str):
    c = get_case(cid)
    return Response(closure.build(c), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="afterword-record-{cid}.pdf"'})


@app.get("/api/cases/{cid}/paypal-packet.pdf")
def paypal_packet(cid: str):
    c = get_case(cid)
    return Response(closure.paypal_packet(c), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="paypal-deceased-account-{cid}.pdf"'})


@app.post("/api/paypal/webhook")
async def paypal_webhook(request: Request):
    """PayPal tells us directly when a subscription is cancelled or a capture refunded.
    Verified with PayPal's verify-webhook-signature API; unverified events are ignored."""
    body = await request.json()
    hdr = {k: request.headers.get(k, "") for k in ("paypal-transmission-id", "paypal-transmission-time", "paypal-transmission-sig",
                                                     "paypal-cert-url", "paypal-auth-algo")}
    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "")
    if pp.MODE != "sandbox" or not webhook_id:
        raise HTTPException(400, "Webhook verification needs PayPal sandbox credentials and PAYPAL_WEBHOOK_ID")
    res = pp.transport()._rest("POST", "/v1/notifications/verify-webhook-signature", {
        "auth_algo": hdr["paypal-auth-algo"], "cert_url": hdr["paypal-cert-url"], "transmission_id": hdr["paypal-transmission-id"],
        "transmission_sig": hdr["paypal-transmission-sig"], "transmission_time": hdr["paypal-transmission-time"],
        "webhook_id": webhook_id, "webhook_event": body})
    if res.get("verification_status") != "SUCCESS":
        db.audit("paypal_webhook", "REJECTED unverified event", None, {"event": body.get("event_type")})
        raise HTTPException(400, "signature not verified")
    rid = (body.get("resource") or {}).get("id")
    db.audit("paypal_webhook", f"VERIFIED {body.get('event_type')}", rid, {"resource_id": rid})
    return {"ok": True}


# =============================================================================== merchant side
@app.get("/api/merchants")
def merchants():
    out = db.query("SELECT * FROM merchants ORDER BY name")
    for m in out:
        m["open"] = db.query("SELECT COUNT(*) AS n FROM notices WHERE merchant_id=? AND status IN ('RECEIVED','IN_REVIEW','ACTIONED')", (m["id"],))[0]["n"]
    return out


@app.get("/api/merchants/{mid}/stats")
def merchant_stats(mid: str):
    from datetime import datetime
    rows = db.query("SELECT received_at, closed_at, outcome, status FROM notices WHERE merchant_id=?", (mid,))
    mins = sorted((datetime.fromisoformat(r["closed_at"]) - datetime.fromisoformat(r["received_at"])).total_seconds() / 60
                  for r in rows if r["closed_at"] and r["received_at"])
    return {"notices": len(rows), "closed": len(mins),
            "median_minutes_to_close": round(mins[len(mins) // 2], 1) if mins else None,
            "refunded": round(sum((r["outcome"] or {}).get("refunded_total", 0) for r in rows), 2),
            "open": len([r for r in rows if r["status"] in ("RECEIVED", "IN_REVIEW", "ACTIONED")])}


class MerchantPatch(BaseModel):
    auto_close: bool | None = None
    auto_refund_cap: float | None = None


@app.patch("/api/merchants/{mid}")
def patch_merchant(mid: str, body: MerchantPatch):
    db.get("merchants", mid) or _404("merchant")
    vals = {k: (int(v) if isinstance(v, bool) else v) for k, v in body.model_dump().items() if v is not None}
    db.update("merchants", mid, vals)
    db.audit("merchant_admin", "CHANGED auto-close rule", mid, vals)
    return db.get("merchants", mid)


@app.get("/api/merchant/notices")
def merchant_notices(merchant_id: str | None = None):
    sql = """SELECT n.id, n.status, n.created_at, n.received_at, n.closed_at, n.review, n.outcome, n.payload,
                    m.name AS merchant_name, m.id AS merchant_id, m.auto_close
             FROM notices n JOIN merchants m ON m.id = n.merchant_id"""
    rows = db.query(sql + (" WHERE m.id=?" if merchant_id else "") + " ORDER BY n.created_at DESC", (merchant_id,) if merchant_id else ())
    out = []
    for r in rows:
        p = r.pop("payload")
        r["deceased"] = p["deceased"]["name"]
        r["date_of_death"] = p["deceased"]["date_of_death"]
        r["claimant"] = f"{p['claimant']['name']} ({p['claimant']['relationship']})"
        r["score"] = p["verification"]["score"]
        r["refund_due"] = (r.pop("review") or {}).get("refund_total", 0)
        r["refunded"] = (r.pop("outcome") or {}).get("refunded_total", 0)
        r["pending"] = db.query("SELECT COUNT(*) AS n FROM pending_actions WHERE notice_id=? AND status='PENDING'", (r["id"],))[0]["n"]
        out.append(r)
    return out


@app.get("/api/merchant/notices/{nid}")
def merchant_notice(nid: str):
    n = db.get("notices", nid) or _404("notice")
    n["merchant"] = db.get("merchants", n["merchant_id"])
    n["signature_valid"] = notices.verify(n["payload"], n["signature"])
    n["actions"] = db.query("SELECT * FROM pending_actions WHERE notice_id=? ORDER BY created_at", (nid,))
    return n


class Decision(BaseModel):
    reason: str = ""


@app.post("/api/merchant/notices/{nid}/approve-all")
def approve_all(nid: str):
    db.get("notices", nid) or _404("notice")
    return merchant.approve_all(nid, "human (merchant support)")


@app.post("/api/actions/{aid}/approve")
def approve(aid: str):
    try:
        a = pp.approve(aid, "human (merchant support)")
    except (PermissionError, ValueError) as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"PayPal call failed: {e}") from e
    merchant.finalize(a["notice_id"])
    return a


@app.post("/api/actions/{aid}/reject")
def reject(aid: str, body: Decision | None = None):
    try:
        a = pp.reject(aid, "human (merchant support)", body.reason if body else "")
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    merchant.finalize(a["notice_id"])
    return a


# =============================================================================== audit + demo
@app.get("/api/audit")
def audit(limit: int = 300):
    return db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))


@app.get("/api/audit/verify")
def audit_verify():
    return db.verify_audit_chain()


@app.get("/api/demo/specimen.png")
def specimen():
    return Response(seed.ensure_specimen(), media_type="image/png")


@app.get("/api/demo/sample-activity.csv")
def sample_csv():
    return PlainTextResponse(db.kv_get("sample_activity_csv") or "", media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="paypal-activity-sample.csv"'})


@app.post("/api/demo/reset")
def demo_reset():
    db.reset()
    pp._transport = None
    seed.seed_demo()
    return {"ok": True}


# =============================================================================== UI
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")

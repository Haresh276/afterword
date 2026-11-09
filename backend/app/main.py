"""Afterword API (FastAPI). Serves the built React UI from ../frontend/dist when present."""
from __future__ import annotations

import asyncio
import threading
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import activity, certificate, closure, db, forensics, insights, lab, letters, llm, merchant, notices, paypal_proof, seed, studio_ai
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
    stop = asyncio.Event()

    async def ticker():  # runs irreversible steps once their objection window has passed
        while not stop.is_set():
            try:
                await asyncio.to_thread(merchant.tick)
            except Exception as exc:  # noqa: BLE001
                db.audit("system", "tick failed", None, {"error": str(exc)[:300]})
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(ticker())
    probe_stop = threading.Event()
    threading.Thread(target=llm.keep_probing, args=(probe_stop,), daemon=True, name="llm-probe").start()
    async with mcp_server.server.session_manager.run():
        yield
    stop.set()
    probe_stop.set()
    await task


def _session(request: Request) -> str:
    """The visitor's demo world (sent by the UI as X-Afterword-Session). '' = the shared/real world."""
    raw = request.headers.get("x-afterword-session", "") or request.query_params.get("session", "")
    return raw if re.fullmatch(r"[A-Za-z0-9-]{8,64}", raw or "") else ""


app = FastAPI(title="Afterword", version="1.1.0", lifespan=lifespan,
              description="Afterword hub API: verified death notices for the subscription economy, on PayPal.")
OBJECTION_SECONDS = float(os.getenv("AFTERWORD_OBJECTION_WINDOW", "30"))


def _window_text(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:g} hours"
    return f"{seconds:g} seconds"
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/mcp", _mcp_asgi)


def _404(what: str):
    raise HTTPException(404, f"{what} not found")


@app.get("/api/status")
def status():
    return {"paypal_mode": pp.MODE, "paypal_keys": pp.HAS_KEYS, "llm": llm.model_name(), "llm_live": llm.available() and llm.healthy(),
            "letter_languages_offline": list(letters.OFFLINE),
            "min_score": merchant.MIN_SCORE, "protocol": notices.PROTOCOL,
            "objection_window_seconds": OBJECTION_SECONDS, "objection_window_text": _window_text(OBJECTION_SECONDS)}


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
def create_case(body: NewCase, request: Request):
    cid = db.new_id("case")
    db.insert("cases", {"id": cid, **body.model_dump(), "status": "DRAFT", "session": _session(request), "created_at": db.now()})
    db.audit("family", "OPENED case", cid, {"claimant": body.claimant_name, "relationship": body.relationship})
    return get_case(cid)


@app.get("/api/cases")
def list_cases(request: Request):
    return db.query("SELECT id, deceased_name, claimant_name, status, created_at FROM cases WHERE COALESCE(session, '')=? ORDER BY created_at DESC",
                    (_session(request),))


@app.get("/api/cases/{cid}")
def get_case(cid: str):
    c = db.get("cases", cid) or _404("case")
    fs = db.query("SELECT * FROM findings WHERE case_id=? ORDER BY rowid", (cid,))
    for f in fs:
        n = db.get("notices", f["notice_id"]) if f["notice_id"] else None
        f["notice"] = {k: n[k] for k in ("id", "status", "outcome", "reply", "received_at", "closed_at", "confirmation")} if n else None
        if n:
            acts = db.query("SELECT tool, status, phase, not_before FROM pending_actions WHERE notice_id=?", (n["id"],))
            f["notice"]["paused"] = any(a["tool"] == "suspend_subscription" and a["status"] == "EXECUTED" for a in acts)
            f["notice"]["waiting_until"] = min((a["not_before"] for a in acts if a["status"] == "PENDING" and a["not_before"]), default=None)
        if n and n.get("receipt"):
            f["notice"]["receipt_valid"] = notices.verify(n["receipt"], n["receipt_sig"], party=n["merchant_id"])
        f["refunded"] = (n["outcome"] or {}).get("refunded_total", 0) if n and n.get("outcome") else 0
        f["merchant"] = db.get("merchants", f["merchant_id"]) if f["merchant_id"] else None
        if f["status"] == "NOT_ON_NETWORK" and f["selected"] and c.get("certificate_sha256"):
            f["email_draft"] = letters.offline_email(c, f)
    notified = [f for f in fs if f["notice_id"]]
    c["findings"] = fs
    c["outbox"] = db.query("SELECT kind, to_addr, subject, body, created_at FROM outbox WHERE case_id=? ORDER BY created_at", (cid,))
    c.pop("objection_token", None)  # the secret only ever goes to the account holder's own address
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
        "paused": len([f for f in notified if (f.get("notice") or {}).get("paused")]),
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
    return await asyncio.to_thread(_ingest, cid, data, mime)


def _ingest(cid: str, data: bytes, mime: str) -> dict:
    ext, engine = certificate.extract(data, mime)
    fx = forensics.analyse(data)
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
        raw = seed.sample_csv((db.get("cases", cid) or {}).get("session") or "").encode()
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
    if c["status"] == "OBJECTED":
        raise HTTPException(409, "The account holder said they are alive. This case can't be sent.")
    sent = []
    if not db.get("cases", cid).get("objection_deadline"):
        token = secrets.token_urlsafe(18)
        deadline = db.later(seconds=OBJECTION_SECONDS)
        db.update("cases", cid, {"objection_token": token, "objection_deadline": deadline})
        c["objection_deadline"] = deadline
        if c.get("deceased_email"):
            subject, body = letters.objection_email(c, f"/alive/{token}", _window_text(OBJECTION_SECONDS))
            db.insert("outbox", {"id": db.new_id("mail"), "case_id": cid, "kind": "proof_of_life", "to_addr": c["deceased_email"],
                                 "subject": subject, "body": body, "created_at": db.now()})
            db.audit("hub", "SENT proof-of-life check to the account holder's own address", cid,
                     {"to": c["deceased_email"], "irreversible_not_before": deadline})
    else:
        c["objection_deadline"] = db.get("cases", cid)["objection_deadline"]
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


@app.get("/api/cases/{cid}/timeline")
def timeline(cid: str):
    """Bryntum Scheduler data: one row per company, charges before and after the death, what Afterword
    did and when, and every charge over the next year that won't be taken."""
    from datetime import date as _date, datetime as _dt, timedelta as _td
    c = get_case(cid)
    if not c.get("date_of_death"):
        return {"resources": [], "events": [], "timeRanges": []}
    resources, events = [], []
    today = _dt.now().date()
    for f in c["findings"]:
        if f["kind"] not in ("SUBSCRIPTION", "INVOICE"):
            continue
        rid = f["id"]
        resources.append({"id": rid, "name": f["merchant_name"], "kind": f["kind"].title(),
                          "cadence": f["cadence"] or "", "status": f["status"]})
        for i, h in enumerate(f.get("history") or []):
            kind = "invoice" if h.get("invoice") else "after" if h.get("after_death") else "before"
            events.append({"id": f"{rid}-h{i}", "resourceId": rid, "startDate": h["date"], "duration": 0, "kind": kind,
                           "name": f"${h['amount']:.2f}" + (" invoice, unpaid" if kind == "invoice" else " after the death" if kind == "after" else ""),
                           "eventColor": {"before": "gray", "after": "red", "invoice": "orange"}[kind]})
        n = f.get("notice") or {}
        if n.get("received_at"):
            events.append({"id": f"{rid}-told", "resourceId": rid, "startDate": n["received_at"], "duration": 0, "kind": "told",
                           "name": "Told by Afterword", "eventColor": "violet"})
        if n.get("closed_at"):
            refunded = f.get("refunded") or 0
            events.append({"id": f"{rid}-closed", "resourceId": rid, "startDate": n["closed_at"], "duration": 0, "kind": "closed",
                           "name": "Closed" + (f", ${refunded:.2f} refunded" if refunded else ""), "eventColor": "green"})
        if f["kind"] == "SUBSCRIPTION" and f.get("next_charge") and f.get("period_days"):
            stopped = f["status"] in ("CLOSED", "PAUSED")
            d, k = _date.fromisoformat(f["next_charge"]), 0
            while d <= today + _td(days=365) and k < 24:
                events.append({"id": f"{rid}-n{k}", "resourceId": rid, "startDate": d.isoformat(), "duration": 0, "kind": "future",
                               "stopped": stopped, "name": f"${f['typical_amount']:.2f} " + ("won't be taken" if stopped else "will be taken"),
                               "eventColor": "teal" if stopped else "amber"})
                d += _td(days=round(f["period_days"]))
                k += 1
    ranges = [{"id": "death", "name": "", "startDate": c["date_of_death"], "cls": "tr-death"},
              {"id": "today", "name": "", "startDate": today.isoformat(), "cls": "tr-today"}]
    if c.get("objection_deadline") and c["status"] != "OBJECTED":
        start = min((f["notice"]["received_at"] for f in c["findings"] if (f.get("notice") or {}).get("received_at")), default=None)
        if start:
            ranges.append({"id": "window", "name": "", "startDate": start, "endDate": c["objection_deadline"], "cls": "tr-window"})
    hist = [e["startDate"] for e in events]
    return {"resources": resources, "events": events, "timeRanges": ranges,
            "start": (_date.fromisoformat(c["date_of_death"][:10]) - _td(days=100)).isoformat(), "end": (today + _td(days=370)).isoformat()}


# ------------------------------------------------------------------ proof of life ("I'm alive")
def _by_token(token: str) -> dict:
    rows = db.query("SELECT * FROM cases WHERE objection_token=?", (token,))
    if not rows or not token:
        _404("link")
    return rows[0]


@app.get("/api/alive/{token}")
def alive_info(token: str):
    c = _by_token(token)
    return {"name": c["deceased_name"], "reported_by": c["claimant_name"], "relationship": c["relationship"],
            "reported_at": c["created_at"], "deadline": c["objection_deadline"], "objected_at": c["objected_at"],
            "companies": [n["name"] for n in db.query(
                "SELECT m.name FROM notices x JOIN merchants m ON m.id=x.merchant_id WHERE x.case_id=?", (c["id"],))]}


class Objection(BaseModel):
    note: str = ""


@app.post("/api/alive/{token}")
def alive_object(token: str, body: Objection | None = None):
    c = _by_token(token)
    if c["objected_at"]:
        return alive_info(token)
    note = (body.note if body else "")[:500]
    db.update("cases", c["id"], {"objected_at": db.now(), "objection_note": note, "status": "OBJECTED"})
    db.audit("account_holder", "OBJECTED: says they are alive", c["id"], {"note": note})
    for n in db.query("SELECT id, merchant_id FROM notices WHERE case_id=?", (c["id"],)):
        withdrawal = {"protocol": notices.PROTOCOL + "/withdrawal", "notice_id": n["id"], "reason": "account holder objected", "at": db.now()}
        db.audit("hub", "SIGNED withdrawal", n["id"], {"signature": notices.sign(withdrawal)[:24] + "..."})
        merchant.withdraw(n["id"], "The account holder answered the proof-of-life check and said they are alive.")
    return alive_info(token)


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
    if not pp.HAS_KEYS or not webhook_id:
        raise HTTPException(400, "Webhook verification needs PayPal sandbox credentials and PAYPAL_WEBHOOK_ID")
    res = pp.live()._rest("POST", "/v1/notifications/verify-webhook-signature", {
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
def merchant_stats(mid: str, request: Request):
    from datetime import datetime
    rows = db.query("""SELECT n.received_at, n.closed_at, n.outcome, n.status FROM notices n LEFT JOIN cases c ON c.id = n.case_id
                       WHERE n.merchant_id=? AND COALESCE(c.session, '')=?""", (mid, _session(request)))
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
def merchant_notices(request: Request, merchant_id: str | None = None):
    sql = """SELECT n.id, n.status, n.created_at, n.received_at, n.closed_at, n.review, n.outcome, n.payload,
                    m.name AS merchant_name, m.id AS merchant_id, m.auto_close
             FROM notices n JOIN merchants m ON m.id = n.merchant_id LEFT JOIN cases c ON c.id = n.case_id
             WHERE COALESCE(c.session, '') = ?"""
    args: tuple = (_session(request),)
    rows = db.query(sql + (" AND m.id=?" if merchant_id else "") + " ORDER BY n.created_at DESC", args + ((merchant_id,) if merchant_id else ()))
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
    who = "human (merchant support)"
    try:
        a = pp.approve(aid, who)
    except pp.WindowOpen as w:  # irreversible: record the approval now, it runs when the window passes
        a = db.get("pending_actions", aid)
        if any(not c["passed"] for c in a["policy_checks"]):
            raise HTTPException(409, "Checks failed") from w
        db.update("pending_actions", aid, {"preapproved_by": who})
        db.audit(who, f"PRE-APPROVED {a['tool']}", a["notice_id"], {"action_id": aid, "runs_after": w.until})
        return {**db.get("pending_actions", aid), "scheduled": True}
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


# =============================================================================== PayPal proof
@app.get("/api/paypal/evidence")
def paypal_evidence():
    return paypal_proof.evidence()


@app.post("/api/paypal/live-check")
def paypal_live_check():
    return paypal_proof.live_check()


# =============================================================================== AG Studio
@app.get("/api/insights")
def insights_rows():
    return insights.rows()


@app.get("/api/llm/ping")
async def llm_ping():
    """Which model this server can reach right now, and how fast (never shows the key)."""
    return await asyncio.to_thread(llm.probe)


@app.post("/api/ai/responses")
async def ai_responses(request: Request):
    """OpenAI Responses-API shim over the configured model, for AG Studio's AI assistant."""
    if not llm.available():
        raise HTTPException(503, "The AI assistant needs GEMINI_API_KEY (or OPENAI_API_KEY) on the server.")
    body = await request.json()
    return await asyncio.to_thread(studio_ai.respond, body)


# =============================================================================== try to break it
@app.get("/api/lab")
def lab_list():
    return {"paypal_mode": pp.MODE, "scenarios": lab.SCENARIOS}


@app.post("/api/lab/{sid}")
def lab_run(sid: str, request: Request):
    try:
        return lab.run(sid, ingest=_ingest, session=_session(request))
    except KeyError:
        _404("scenario")


@app.get("/api/demo/specimen.png")
def specimen():
    return Response(seed.ensure_specimen(), media_type="image/png")


@app.get("/api/demo/sample-activity.csv")
def sample_csv(request: Request):
    return PlainTextResponse(seed.sample_csv(_session(request)), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="paypal-activity-sample.csv"'})


@app.post("/api/demo/reset")
def demo_reset(request: Request):
    """With a session (the hosted UI always sends one): reset only this visitor's demo world.
    Without one (tests, local scripts): reset everything."""
    s = _session(request)
    if s:
        seed.reset_session(s)
        return {"ok": True, "scope": "your demo only"}
    db.reset()
    pp._transport = None
    seed.seed_demo()
    return {"ok": True, "scope": "everything"}


# =============================================================================== UI
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")

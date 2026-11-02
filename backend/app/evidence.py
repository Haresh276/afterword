"""Evidence pack: a PDF a freelancer can attach to a PayPal dispute or send to a slow payer."""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import db

INK = colors.HexColor("#0f172a")
ACCENT = colors.HexColor("#0b5fff")
VERDICT = {"PASS": colors.HexColor("#15803d"), "PARTIAL": colors.HexColor("#b45309"), "FAIL": colors.HexColor("#b91c1c")}


def _p(text: str, style) -> Paragraph:
    safe = (str(text or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(safe, style)


def build(milestone_id: str) -> bytes:
    ms = db.get("milestones", milestone_id)
    if not ms:
        raise KeyError(milestone_id)
    c = db.get("contracts", ms["contract_id"])
    report = ms.get("proof_report") or {}
    log = db.query("SELECT * FROM audit_log WHERE milestone_id=? ORDER BY id", (milestone_id,))

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=INK, fontSize=20, alignment=0, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=ACCENT, fontSize=12, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12, textColor=INK)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=10, textColor=colors.HexColor("#475569"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"ProofPay evidence pack - {ms['name']}")
    el = [
        _p("ProofPay - Proof-of-Work Evidence Pack", h1),
        _p(f"Generated {db.now()} UTC. Milestone {ms['seq']} of '{c['title']}'.", small),
        _p("Engagement", h2),
    ]
    facts = [
        ["Freelancer", f"{c['freelancer_name']} <{c['freelancer_email']}>"],
        ["Client", f"{c['client_name']} <{c['client_email']}>"],
        ["Milestone", f"{ms['seq']}. {ms['name']}"],
        ["Amount", f"{float(ms['amount']):,.2f} {c['currency']}"],
        ["Due date", ms.get("due_date") or "-"],
        ["PayPal invoice", f"{ms.get('invoice_id') or 'not yet issued'} ({ms.get('invoice_status') or '-'})"],
        ["Verification score", f"{int((ms.get('proof_score') or 0) * 100)}%"],
    ]
    t = Table([[_p(a, body), _p(b, body)] for a, b in facts], colWidths=[38 * mm, 140 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el += [t, _p("Acceptance criteria vs delivered work", h2)]
    rows = [[_p("Criterion", body), _p("Verdict", body), _p("Evidence", body)]]
    styles = [("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
              ("VALIGN", (0, 0), (-1, -1), "TOP")]
    for i, cr in enumerate(report.get("criteria", []), start=1):
        rows.append([_p(cr["criterion"], body), _p(cr["verdict"], body), _p(cr.get("evidence", ""), small)])
        styles.append(("TEXTCOLOR", (1, i), (1, i), VERDICT.get(cr["verdict"], INK)))
    vt = Table(rows, colWidths=[70 * mm, 20 * mm, 88 * mm])
    vt.setStyle(TableStyle(styles))
    el += [vt, Spacer(1, 4), _p(f"Reviewer summary: {report.get('summary', '-')}", body)]
    dl = ms.get("deliverable") or {}
    el += [_p("Deliverable submitted", h2), _p(f"{dl.get('kind', '-')}: {dl.get('label') or dl.get('value', '')}", body),
           _p((dl.get("excerpt") or "")[:1500], small)]
    el += [_p("Audit trail (every agent and human action)", h2)]
    arows = [[_p("Time (UTC)", body), _p("Actor", body), _p("Action", body)]]
    for a in log:
        arows.append([_p(a["ts"], small), _p(a["actor"], small), _p(a["action"], small)])
    at = Table(arows, colWidths=[44 * mm, 34 * mm, 100 * mm])
    at.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0"))]))
    el += [at, Spacer(1, 6), _p("AI verification is advisory. Every PayPal action in this trail was approved by a human before execution.", small)]
    doc.build(el)
    return buf.getvalue()

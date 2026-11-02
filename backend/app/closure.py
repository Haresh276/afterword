"""Closure record: one PDF the family keeps (and an executor can file) showing what was
stopped, what was refunded, who confirmed it, and the audit-chain fingerprint."""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import db

INK = colors.HexColor("#1f2a2b")
TEAL = colors.HexColor("#2f5d62")
LINE = colors.HexColor("#d5dcda")


def _p(t, s):
    return Paragraph(str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), s)


def build(case: dict) -> bytes:
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontName="Times-Roman", textColor=INK, fontSize=24, alignment=0, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=TEAL, fontSize=12, spaceBefore=12, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=13, textColor=INK)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10.5, textColor=colors.HexColor("#5d6969"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Afterword record: {case.get('deceased_name')}")
    t = case["totals"]
    el = [_p(f"In memory of {case.get('deceased_name')}", h1),
          _p(f"Afterword closure record · prepared for {case.get('claimant_name')} ({case.get('relationship')}) · {db.now()[:10]}", small),
          Spacer(1, 6),
          _p(f"{t['closed']} of {t['notified']} companies have closed the account. ${t['refunded']:,.2f} taken after "
             f"{case.get('date_of_death')} has been refunded; ${t['monthly_stopped']:,.2f} a month will no longer be charged.", body)]

    el.append(_p("Certificate", h2))
    v = case.get("verification") or {}
    facts = [["Name", case.get("deceased_name")], ["Date of death", case.get("date_of_death")],
             ["Registration", f"{case.get('registration_number')} · {case.get('issuing_authority')}"],
             ["Verification", f"{int((v.get('score') or 0) * 100)}% of checks passed"],
             ["Certificate image", f"not stored (deleted {(case.get('certificate_deleted_at') or '')[:19]} UTC); sha256 {case.get('certificate_sha256')}"]]
    tb = Table([[_p(a, body), _p(b, small if a == 'Certificate image' else body)] for a, b in facts], colWidths=[38 * mm, 136 * mm])
    tb.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el.append(tb)

    el.append(_p("Companies", h2))
    rows = [[_p(x, small) for x in ("Company", "What it was", "Outcome", "Refunded", "Confirmed")]]
    for f in case["findings"]:
        if f["kind"] not in ("SUBSCRIPTION", "INVOICE") or not f["selected"]:
            continue
        n = f.get("notice") or {}
        outcome = {"CLOSED": "Closed", "NOT_ON_NETWORK": "Email prepared for family"}.get(f["status"], f["status"].title())
        rows.append([_p(f["merchant_name"], body), _p(f"{f['kind'].title()}, {f['cadence']}", small), _p(outcome, body),
                     _p(f"${f['refunded']:,.2f}" if f.get("refunded") else "-", body), _p((n.get("closed_at") or "")[:16], small)])
    ct = Table(rows, colWidths=[44 * mm, 36 * mm, 40 * mm, 22 * mm, 32 * mm])
    ct.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el.append(ct)

    chain = db.verify_audit_chain()
    el += [_p("Integrity", h2),
           _p(f"Every step (certificate read, notices signed with Ed25519, each merchant's checks, approvals and PayPal calls) "
              f"is in a hash-chained audit log: {chain['rows_checked']} entries, chain {'intact' if chain['ok'] else 'BROKEN'}, "
              f"head sha256 {chain['head']}.", small)]
    doc.build(el)
    return buf.getvalue()


def paypal_packet(case: dict) -> bytes:
    """The family's own PayPal account still has to be closed through PayPal's deceased-account
    process (executor or administrator only). This pre-fills the cover sheet PayPal asks for and
    lists what to attach. Content follows PayPal's UK help article; other countries differ."""
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontName="Times-Roman", textColor=INK, fontSize=20, alignment=0, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=TEAL, fontSize=12, spaceBefore=12, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=14, textColor=INK)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10.5, textColor=colors.HexColor("#5d6969"))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title="PayPal deceased account: cover sheet")
    blank = "______________________________"
    rows = [["Account holder (deceased)", case.get("deceased_name")], ["PayPal email", case.get("deceased_email")],
            ["Date of death", case.get("date_of_death")],
            ["Death certificate", f"{case.get('registration_number')} · {case.get('issuing_authority')}"],
            ["Representative's name", blank], ["Representative's email", case.get("claimant_email") or blank],
            ["Representative's phone", blank], ["Role", "Executor / Administrator of the estate (delete one)"]]
    t = Table([[_p(a, body), _p(b, body)] for a, b in rows], colWidths=[55 * mm, 115 * mm])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    checklist = [
        "This cover sheet, stating that the account holder has died.",
        "A copy of the death certificate.",
        "If money is left in the PayPal balance: a certified copy of the will naming the executor, or, with no will, a statement "
        "of the administrator's interest in the estate and relationship to the deceased; written permission from anyone else entitled "
        "to a share; government photo ID; a signed Standard Indemnity Form; and how the money should be paid out.",
        "UK: balances above £5,000 need a Grant of Probate or Letters of Administration instead of the indemnity form.",
    ]
    el = [_p("PayPal account of a person who has died: cover sheet", h1),
          _p(f"Prepared by Afterword on {db.now()[:10]}. Afterword has already told the companies that charged this account; "
             "this sheet is for closing the PayPal account itself.", small),
          _p("Cover sheet", h2), t, _p("Attach", h2)]
    el += [_p(f"☐  {x}", body) for x in checklist]
    el += [_p("Send", h2),
           _p("By email to the address in PayPal's help article \"How do I close the PayPal account of a deceased relative?\", "
              "or by post (UK) to PayPal, 76 Upper Ground, London, SE1 9PZ, United Kingdom.", body),
           Spacer(1, 8),
           _p("Only the executor or administrator of the estate can ask PayPal to close the account. Requirements differ by country: "
              "check PayPal's help article for yours before sending. Afterword does not send this for you.", small)]
    doc.build(el)
    return buf.getvalue()

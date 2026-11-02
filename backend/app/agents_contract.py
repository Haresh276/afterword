"""Contract Agent: contract text -> validated milestones + risk flags."""
from __future__ import annotations

import re
from datetime import date, timedelta

from pydantic import BaseModel, Field, field_validator

from . import llm


class Milestone(BaseModel):
    name: str
    amount: float = Field(ge=0)
    due_date: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)

    @field_validator("due_date")
    @classmethod
    def iso_date(cls, v: str | None) -> str | None:
        if not v:
            return None
        try:
            return date.fromisoformat(v[:10]).isoformat()
        except ValueError:
            return None


class ContractExtraction(BaseModel):
    title: str = "Untitled engagement"
    freelancer_name: str = ""
    freelancer_email: str = ""
    client_name: str = ""
    client_email: str = ""
    currency: str = "USD"
    total_amount: float = 0
    summary: str = ""
    milestones: list[Milestone] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    sac_code: str = ""

    @field_validator("currency")
    @classmethod
    def cur(cls, v: str) -> str:
        v = (v or "USD").upper().strip()[:3]
        return v if re.fullmatch(r"[A-Z]{3}", v) else "USD"


SYSTEM = """You are ProofPay's Contract Agent. You read freelance contracts / statements of work and
return STRICT JSON with this shape:
{"title": str, "freelancer_name": str, "freelancer_email": str, "client_name": str, "client_email": str,
 "currency": "ISO 4217 code", "total_amount": number, "summary": "2 sentences, plain English",
 "milestones": [{"name": str, "amount": number, "due_date": "YYYY-MM-DD or null",
                 "acceptance_criteria": ["objective, checkable statements"]}],
 "risk_flags": ["short warnings a freelancer should know before signing/billing"]}
Rules:
- Never invent amounts. If a milestone amount is a percentage of the total, compute it.
- Acceptance criteria must be concrete and verifiable (files, features, counts, formats, deadlines).
  If the contract is vague, write the most reasonable objective criteria and add a risk flag saying so.
- Risk flags to look for: no late-payment clause, unlimited revisions, payment only on 'satisfaction',
  IP transfer before payment, milestone sum != total, missing client email, net-60+ terms, vague scope.
- Dates relative to signing: assume signing date is TODAY given in the user message.
- "sac_code": the Indian GST Services Accounting Code that best fits the work, e.g. 998314 (IT design and
  development), 998313 (IT consulting), 998391 (specialty design), 998392 (design originals),
  998395 (translation). Empty string if unsure.
Return the "sac_code" key too."""


def extract(text: str) -> tuple[ContractExtraction, str]:
    """Returns (extraction, engine_used)."""
    if llm.available():
        try:
            data = llm.json_call(SYSTEM, f"TODAY: {date.today().isoformat()}\n\nCONTRACT:\n{text[:24000]}")
            result = ContractExtraction(**data)
            _post_checks(result)
            return result, llm.model_name()
        except Exception as exc:  # noqa: BLE001  - fall back rather than break the demo
            result = heuristic(text)
            result.risk_flags.append(f"AI extraction failed ({type(exc).__name__}); used offline parser - review carefully.")
            return result, "offline-heuristic"
    return heuristic(text), "offline-heuristic"


def _post_checks(r: ContractExtraction) -> None:
    s = round(sum(m.amount for m in r.milestones), 2)
    if not r.total_amount:
        r.total_amount = s
    elif r.milestones and abs(s - r.total_amount) > 0.01:
        r.risk_flags.append(f"Milestones add up to {s:.2f} {r.currency} but contract total is {r.total_amount:.2f}.")
    if not r.client_email:
        r.risk_flags.append("No client email found - PayPal needs one to send the invoice.")
    for m in r.milestones:
        if not m.acceptance_criteria:
            r.risk_flags.append(f"Milestone '{m.name}' has no acceptance criteria - disputes become your word vs theirs.")


# ----------------------------------------------------------------------------- offline parser
_AMT = r"(?:USD|US\$|\$|EUR|€|GBP|£)\s?([\d,]+(?:\.\d{1,2})?)|([\d,]+(?:\.\d{1,2})?)\s?(?:USD|EUR|GBP)"
_EMAIL = r"[\w.+-]+@[\w-]+\.[\w.-]+"


def _amount(s: str) -> float | None:
    m = re.search(_AMT, s)
    if not m:
        return None
    return float((m.group(1) or m.group(2)).replace(",", ""))


def heuristic(text: str) -> ContractExtraction:
    lines = [l.rstrip() for l in text.splitlines()]
    r = ContractExtraction()
    title = next((l.strip("# ").strip() for l in lines if l.strip()), "Untitled engagement")
    if " - " in title:
        title = title.split(" - ", 1)[1]
    r.title = title[:120]
    cur = re.search(r"\b(USD|EUR|GBP)\b", text)
    r.currency = cur.group(1) if cur else ("EUR" if "€" in text else "GBP" if "£" in text else "USD")

    def party(label: str) -> tuple[str, str]:
        for i, l in enumerate(lines):
            if re.match(rf"\s*{label}\b", l, re.I):
                block = " ".join(lines[i : i + 3])
                em = re.search(_EMAIL, block)
                name = re.sub(rf"^\s*{label}\s*[:\-]?\s*", "", l, flags=re.I)
                name = re.split(r"[(<,]|" + _EMAIL, name)[0].strip(" :-")
                return name, em.group(0) if em else ""
        return "", ""

    r.freelancer_name, r.freelancer_email = party("(?:Freelancer|Contractor|Service Provider|Consultant)")
    r.client_name, r.client_email = party("Client")

    tot = re.search(r"(?:total|contract value|fee)[^\n]*", text, re.I)
    if tot and _amount(tot.group(0)):
        r.total_amount = _amount(tot.group(0)) or 0

    current: Milestone | None = None
    in_criteria = False
    for l in lines:
        mh = re.match(r"\s*#*\s*(?:Milestone|Phase|Deliverable)\s*(\d+)\s*[:.\-–—]\s*(.+)", l, re.I)
        if mh:
            if current:
                r.milestones.append(current)
            rest = mh.group(2)
            name = re.split(r"\s[—–-]\s|\(|\|", rest)[0].strip()
            amt = _amount(rest) or 0.0
            pct = re.search(r"(\d{1,3})\s?%", rest)
            if not amt and pct and r.total_amount:
                amt = round(r.total_amount * int(pct.group(1)) / 100, 2)
            d = re.search(r"(\d{4}-\d{2}-\d{2})", rest)
            days = re.search(r"(?:within|in)\s+(\d+)\s+days", rest, re.I)
            due = d.group(1) if d else ((date.today() + timedelta(days=int(days.group(1)))).isoformat() if days else None)
            current = Milestone(name=name, amount=amt, due_date=due)
            in_criteria = False
            continue
        if current is None:
            continue
        if re.search(r"acceptance|criteria|definition of done|deliverables?:", l, re.I):
            in_criteria = True
            continue
        if not current.amount and _amount(l):
            current.amount = _amount(l) or 0
        if not current.due_date:
            d = re.search(r"(\d{4}-\d{2}-\d{2})", l)
            if d:
                current.due_date = d.group(1)
        b = re.match(r"\s*(?:[-*•]|\d+[.)])\s+(.+)", l)
        if b and in_criteria:
            current.acceptance_criteria.append(b.group(1).strip())
        elif not l.strip() and current.acceptance_criteria:
            in_criteria = False
    if current:
        r.milestones.append(current)

    low_all = text.lower()
    r.sac_code = ("998395" if "translat" in low_all else "998314" if re.search(r"\b(api|software|web app|code|developer|development agreement)\b", low_all)
                  else "998391" if re.search(r"\b(design|logo|brand)\b", low_all) else "")
    r.summary = (
        f"{r.client_name or 'The client'} engages {r.freelancer_name or 'the freelancer'} for '{r.title}', "
        f"{len(r.milestones)} milestones totalling {r.total_amount or sum(m.amount for m in r.milestones):,.2f} {r.currency}."
    )
    low = text.lower()
    if "late" not in low and "interest" not in low:
        r.risk_flags.append("No late-payment clause - nothing compels the client to pay on time.")
    if "unlimited revision" in low:
        r.risk_flags.append("Unlimited revisions - scope can grow without extra pay.")
    if re.search(r"net[\s-]?(60|90)", low):
        r.risk_flags.append("Net-60/90 payment terms - long wait for cash.")
    if "satisf" in low:
        r.risk_flags.append("Payment tied to 'satisfaction' - subjective; rely on the acceptance criteria instead.")
    _post_checks(r)
    return r

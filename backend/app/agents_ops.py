"""Collections Agent (reminders) and the ProofPay Copilot (chat over your receivables)."""
from __future__ import annotations

import json
from datetime import date, datetime

from . import db, llm
from . import paypal_gateway as pp

# ----------------------------------------------------------------------------- reminders
REMINDER_SYSTEM = """You are ProofPay's Collections Agent writing a payment reminder on behalf of a freelancer
to a client abroad. Return STRICT JSON {"subject": str (<= 80 chars), "note": str (<= 600 chars)}.
Tone by tier: 1 = warm and friendly; 2 = polite but firm, restate that the work was verified against the
agreed acceptance criteria; 3 = final notice, professional, mention the verification report is available.
Never threaten, never invent fees that are not in the contract. Plain text, no markdown."""


def days_since(iso: str | None) -> int:
    if not iso:
        return 0
    return (date.today() - datetime.fromisoformat(iso).date()).days


def tier_for(ms: dict) -> int:
    sent = days_since(ms.get("sent_at"))
    overdue = (date.today() - date.fromisoformat(ms["due_date"])).days if ms.get("due_date") else 0
    n = ms.get("reminders_sent") or 0
    if n >= 2 or overdue > 14:
        return 3
    if n >= 1 or overdue > 3 or sent > 10:
        return 2
    return 1


def draft_reminder(ms: dict) -> tuple[dict, int, str]:
    c = db.get("contracts", ms["contract_id"])
    tier = tier_for(ms)
    report = ms.get("proof_report") or {}
    ctx = (f"Tier {tier}. Freelancer: {c['freelancer_name']}. Client: {c['client_name']}. Project: {c['title']}. "
           f"Milestone {ms['seq']}: {ms['name']}, {ms['amount']:.2f} {c['currency']}, due {ms.get('due_date')}. "
           f"Invoice sent {days_since(ms.get('sent_at'))} days ago. Reminders so far: {ms.get('reminders_sent') or 0}. "
           f"Verification score: {int((ms.get('proof_score') or 0) * 100)}%. Verification summary: {report.get('summary', '')}")
    if llm.available():
        try:
            out = llm.json_call(REMINDER_SYSTEM, ctx, temperature=0.5)
            return {"subject": out["subject"][:80], "note": out["note"][:600]}, tier, llm.model_name()
        except Exception:  # noqa: BLE001
            pass
    name = (c["client_name"] or "there").split(" ")[0]
    amt = f"{ms['amount']:,.2f} {c['currency']}"
    templates = {
        1: ("Friendly reminder: " + ms["name"],
            f"Hi {name}, just a friendly nudge on the invoice for '{ms['name']}' ({amt}). You can pay securely through PayPal using the link in the invoice. Thanks so much! - {c['freelancer_name']}"),
        2: ("Payment pending: " + ms["name"],
            f"Hi {name}, the invoice for '{ms['name']}' ({amt}) is still open. The delivered work was checked against every acceptance criterion in our agreement ({int((ms.get('proof_score') or 0)*100)}% verified). Could you settle it this week? - {c['freelancer_name']}"),
        3: ("Final notice: invoice for " + ms["name"],
            f"Hi {name}, this is a final reminder for '{ms['name']}' ({amt}). The verification report mapping each deliverable to our agreed criteria is available on request. Please complete payment via PayPal or let me know if anything is blocking it. - {c['freelancer_name']}"),
    }
    s, n = templates[tier]
    return {"subject": s[:80], "note": n[:600]}, tier, "template"


def propose_reminder(ms: dict, proposed_by: str = "collections_agent") -> dict:
    msg, tier, engine = draft_reminder(ms)
    params = {"invoice_id": ms["invoice_id"], **msg}
    checks = [
        {"check": "Invoice is open (not paid/cancelled)", "passed": ms["status"] == "INVOICED", "detail": ms.get("invoice_status")},
        {"check": "Reminder cap (max 3)", "passed": (ms.get("reminders_sent") or 0) < 3, "detail": f"{ms.get('reminders_sent') or 0} sent"},
    ]
    return pp.propose(ms["id"], "send_invoice_reminder", params, f"Tier {tier} reminder drafted by {engine}", proposed_by, checks)


# ----------------------------------------------------------------------------- copilot
COPILOT_SYSTEM = """You are ProofPay Copilot, the assistant inside a freelancer's receivables dashboard.
You can look up milestones and PayPal invoices with your tools. You can PROPOSE a payment reminder, but you
can never send money-moving actions yourself: proposals go to the human approval queue. Be concise, use
numbers, and mention invoice IDs when relevant. Today is {today}."""


def _snapshot() -> str:
    rows = db.query("""SELECT m.id, m.seq, m.name, m.amount, m.status, m.due_date, m.invoice_id, m.invoice_status,
                              m.proof_score, m.reminders_sent, m.sent_at, c.title, c.client_name, c.currency
                       FROM milestones m JOIN contracts c ON c.id = m.contract_id ORDER BY c.created_at, m.seq""")
    return json.dumps(rows, default=str)


def copilot(message: str) -> dict:
    if not llm.available():
        return {"reply": _offline_answer(message), "engine": "offline", "tools": []}
    try:
        return _agents_sdk_answer(message)
    except Exception as exc:  # noqa: BLE001
        db.audit("copilot", "agents-sdk error, falling back", None, {"error": str(exc)})
        reply = llm.text_call(COPILOT_SYSTEM.format(today=date.today()) + "\nDATA:\n" + _snapshot(), message)
        return {"reply": reply, "engine": llm.model_name(), "tools": []}


def _agents_sdk_answer(message: str) -> dict:
    from agents import Agent, OpenAIChatCompletionsModel, Runner, function_tool, set_tracing_disabled

    set_tracing_disabled(True)
    used: list[str] = []

    @function_tool
    def list_milestones() -> str:
        """List every milestone with contract, amount, status, invoice id, proof score and reminders sent."""
        used.append("list_milestones")
        return _snapshot()

    @function_tool
    def propose_payment_reminder(milestone_id: str) -> str:
        """Draft a reminder for an unpaid invoiced milestone and put it in the human approval queue."""
        used.append("propose_payment_reminder")
        ms = db.get("milestones", milestone_id)
        if not ms or ms["status"] != "INVOICED":
            return "Milestone not found or not awaiting payment."
        a = propose_reminder(ms, proposed_by="copilot")
        return f"Queued for approval as {a['id']}: {a['params']['subject']}"

    tools = [list_milestones, propose_payment_reminder]
    if pp.MODE == "sandbox":
        # Read-only PayPal Agent Toolkit tools, straight from the official package.
        from paypal_agent_toolkit.openai.toolkit import PayPalToolkit
        from paypal_agent_toolkit.shared.configuration import Configuration, Context

        tk = PayPalToolkit(pp.CLIENT_ID, pp.CLIENT_SECRET, Configuration(
            actions={"invoices": {"list": True, "get": True}}, context=Context(sandbox=True)))
        tools += tk.get_tools()
    else:
        @function_tool
        def get_invoice(invoice_id: str) -> str:
            """Get a PayPal invoice by id (status, amount, recipient)."""
            used.append("get_invoice")
            return json.dumps(pp.call("get_invoice", {"invoice_id": invoice_id}, actor="copilot"), default=str)[:6000]

        tools.append(get_invoice)

    model = OpenAIChatCompletionsModel(model=llm.model_name(), openai_client=_async_client())
    agent = Agent(name="ProofPay Copilot", instructions=COPILOT_SYSTEM.format(today=date.today()), tools=tools, model=model)
    result = Runner.run_sync(agent, message, max_turns=6)
    for item in result.new_items:
        name = getattr(getattr(item, "raw_item", None), "name", None)
        if name and name not in used:
            used.append(name)
    db.audit("copilot", "answered", None, {"q": message, "tools": used})
    return {"reply": str(result.final_output), "engine": llm.model_name() + " + OpenAI Agents SDK", "tools": used}


def _async_client():
    from openai import AsyncOpenAI

    key, base, _ = llm._config()
    return AsyncOpenAI(api_key=key, base_url=base) if base else AsyncOpenAI(api_key=key)


def _offline_answer(q: str) -> str:
    rows = json.loads(_snapshot())
    unpaid = [r for r in rows if r["status"] == "INVOICED"]
    paid = [r for r in rows if r["status"] == "PAID"]
    owed = sum(r["amount"] for r in unpaid)
    got = sum(r["amount"] for r in paid)
    lines = [f"Outstanding: {owed:,.2f} across {len(unpaid)} invoices. Collected: {got:,.2f} across {len(paid)}."]
    for r in sorted(unpaid, key=lambda r: r.get("sent_at") or "")[:5]:
        lines.append(f"- {r['client_name']} / {r['name']}: {r['amount']:,.2f} {r['currency']}, invoice {r['invoice_id']}, sent {days_since(r['sent_at'])}d ago")
    lines.append("(Offline mode - add OPENAI_API_KEY or GEMINI_API_KEY for full Copilot answers.)")
    return "\n".join(lines)

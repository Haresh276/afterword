# ProofPay

**Get paid for proven work.** ProofPay is an AI agent for freelancers who bill clients abroad. It reads your contract, turns it into milestones, checks each deliverable against the agreed acceptance criteria, and sends a PayPal invoice only after you approve it. Then it follows up until the money lands, and gives you an evidence pack if the client disputes.

> AI checks the work. A human approves. PayPal settles.

Built for the PayPal AI Hackathon 2026 with the **PayPal Agent Toolkit (Python)**, the **OpenAI Agents SDK**, and **AG Grid**.

---

## Why this exists

India has millions of freelancers selling design, code and translation to clients in the US, UK, EU and Japan. Since April 2021 PayPal in India has focused entirely on exactly this: getting Indian sellers paid from abroad. The hard part isn't the payment button. It's the two weeks before it:

- Contracts say "pay on satisfaction", so the client's mood decides when you get paid.
- Nobody writes down what "done" means, so every late invoice becomes your word against theirs.
- Chasing payment across time zones is awkward, so freelancers wait instead of asking.

ProofPay turns the contract into an objective checklist, proves the work against it, and makes asking for money a one-click, evidence-backed action.

## What it does

| Step | Who | What happens |
|---|---|---|
| 1. Read contract | Contract Agent (LLM) | Paste or upload a PDF. Extracts parties, currency, each milestone's amount, due date and **acceptance criteria**, and flags clauses that delay payment (no late fee, "unlimited revisions", net-60, pay-on-satisfaction, milestone sum ≠ total). |
| 2. Confirm | Freelancer | Edit anything, confirm the milestones. |
| 3. Prove the work | Proof Agent (LLM) | Submit a delivery note, a link, a **GitHub repo** (reads the file tree + README) or a file. Each criterion gets a PASS / PARTIAL / FAIL verdict with the evidence location. Prompt-injection inside deliverables is ignored and flagged. |
| 4. Draft invoice | PayPal Agent | Builds the PayPal Invoicing v2 payload and runs **7 deterministic safety checks**: amount equals the agreed milestone, per-invoice ceiling, payee is the contract's client, currency, contract total not exceeded, no duplicate, proof score ≥ 70% (or a written human override). |
| 5. Approve | Freelancer | One click. Only now does ProofPay call `create_invoice` through the PayPal Agent Toolkit. The client gets a real PayPal invoice. |
| 6. Get paid | PayPal + poller | The ledger polls `get_invoice` and flips to **Paid** the moment the client pays. |
| 7. Chase | Collections Agent (LLM) | Drafts a reminder whose tone escalates (friendly → firm → final) based on days outstanding; you approve; `send_invoice_reminder` sends it. |
| 8. Defend | Evidence pack | One PDF: contract terms, criterion-by-criterion verdicts, the deliverable, and the full audit trail. Ready for a PayPal dispute. |
| Anytime | Copilot (OpenAI Agents SDK) | "Who owes me the most?", "Chase my oldest invoice". Uses the toolkit's read-only invoice tools; can only *propose* reminders. |

## How we use PayPal

| PayPal capability | Via | Used for |
|---|---|---|
| `create_invoice` (Invoicing v2, create + send) | PayPal Agent Toolkit `PayPalAPI.run` | Billing a verified milestone, after human approval |
| `get_invoice` | Toolkit | Payment-status poller, recipient view URL for the client |
| `list_invoices`, `get_invoice` as agent tools | Toolkit `PayPalToolkit(...).get_tools()` for the OpenAI Agents SDK | Copilot answers about receivables |
| `send_invoice_reminder` | Toolkit | Approved, AI-drafted payment reminders |
| `cancel_sent_invoice` | Toolkit | Reserved for the gate (not exposed to agents) |
| Sandbox Business + Personal accounts | developer.paypal.com | End-to-end demo: freelancer bills, client pays |

## How we use AI

| Component | Model | Job |
|---|---|---|
| Contract Agent | OpenAI `gpt-4.1-mini` or Gemini `gemini-2.5-flash` (JSON mode, Pydantic-validated) | Contract → milestones, acceptance criteria, risk flags |
| Proof Agent | same | Deliverable vs. criteria, per-criterion verdict + evidence, injection-resistant prompt |
| Collections Agent | same | Tiered reminder drafts that cite the verification |
| Copilot | OpenAI Agents SDK + PayPal Agent Toolkit tools | Conversational receivables, tool-calling |

Every AI output is advisory. Nothing an LLM writes can move money without passing deterministic checks and a human click.

## The trust layer (why the agent can't go rogue)

The PayPal Agent Toolkit gives an LLM direct access to money-moving endpoints, with no approval hook, amount ceiling or payee validation in the package. In v1.11.0 `create_invoice` also **sends** the invoice immediately. ProofPay wraps the toolkit in a single choke point (`backend/app/paypal_gateway.py`):

1. Money-moving tools (`create_invoice`, `send_invoice`, `send_invoice_reminder`, `cancel_sent_invoice`) are never given to an LLM. Calling them without an approved action id raises `PermissionError` and is logged as `BLOCKED`.
2. Agents can only **propose**. Proposals land in an approval queue with the exact payload and policy checks shown to the human.
3. Approval re-runs nothing the LLM controls: amounts and payee come from the confirmed contract, not from model output.
4. Every call, proposal, approval, rejection and block is written to the audit trail, which ships in the evidence pack.

Tests in `backend/tests/test_flow.py` prove each guarantee (tampered amount blocked, unverified work blocked, unapproved money call refused, injection flagged).

## Architecture

```
React + Vite + AG Grid  ──REST──►  FastAPI (Python)
                                     ├─ Contract Agent ─┐
                                     ├─ Proof Agent ────┼─ LLM (OpenAI / Gemini, OpenAI-compatible)
                                     ├─ Collections Agent┘
                                     ├─ Copilot ── OpenAI Agents SDK + PayPal Agent Toolkit read tools
                                     ├─ Approval gate + policy checks ── PayPal Agent Toolkit ── PayPal Sandbox
                                     ├─ Status poller (get_invoice every 15 s)
                                     ├─ Evidence pack (ReportLab PDF)
                                     └─ SQLite: contracts, milestones, pending_actions, audit_log
```

## Run it

**Hosted demo:** `https://<your-render-url>` (see Testing below)

**Locally (2 terminals):**

```bash
# 1. API (Python 3.11+)
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env        # fill in keys, or leave blank for offline mode
uvicorn app.main:app --reload --port 8000

# 2. UI
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

Or one container: `docker build -t proofpay . && docker run -p 8000:8000 --env-file .env proofpay` → http://localhost:8000

**No keys? It still runs.** Without PayPal credentials ProofPay uses a built-in simulator of the Invoicing v2 endpoints; without an LLM key it falls back to an offline parser. The sidebar shows which mode you're in.

### Connect the PayPal sandbox

1. Log in at https://developer.paypal.com → *Apps & Credentials* → Sandbox → *Create App* (merchant). Copy the Client ID and Secret into `.env`.
2. *Testing Tools → Sandbox Accounts*: note the email of the **Personal** account. Put it in `DEMO_CLIENT_EMAIL` so demo invoices go to it.
3. Restart. The sidebar now says *PayPal sandbox (live API)*.
4. To pay as the client: open the invoice from the milestone drawer (*Open the client's PayPal invoice*) and log in with the sandbox Personal account.

### Tests

```bash
cd backend && python -m pytest -q
```

## Testing instructions for judges

1. Open the hosted URL. Demo data loads automatically: three real-looking contracts (Berlin coffee brand, Bristol physio clinic, Osaka robotics manual).
2. **Contracts** → *Try a sample* → *Read contract*. See milestones and risk flags appear.
3. **Receivables** → click *Booking backend API* (Verified, 88%) → read the criterion stamps, note the prompt-injection line was caught → *Draft PayPal invoice* → *Approve and send invoice*.
4. Watch the row become *Invoiced, unpaid* with a real sandbox invoice id. Pay it as the sandbox buyer (credentials below), or in offline mode press *Simulate client payment*. The row flips to **Paid**.
5. *Download evidence pack (PDF)*. Open **Audit trail** to see every agent and human step.
6. *Ask Copilot*: "Which invoice should I chase first?"

Sandbox buyer for paying invoices: `<sandbox personal email>` / `<password>` (sandbox only, no real money).

## What's next

- Recurring retainers via PayPal subscriptions.
- Client-side agents: an AP2-style signed mandate so a client's purchasing agent can accept a ProofPay verification and pay automatically.
- WhatsApp sharing of invoice QR codes for clients who live on chat.
- Milestone escrow-style holds once PayPal exposes them to agents.

## Team

- **Haresh G.** (SRM University AP): product, AI agents, pitch
- **<Senior's name>** (SRM University AP, CSE): frontend, integration, deployment

MIT licensed. Sample contracts and people are fictional.

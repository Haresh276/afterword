# Afterword

**When someone dies, their subscriptions don't.**

Afterword lets a grieving family tell every company once. It reads the death certificate in any language, finds everything still charging the person's PayPal account, and sends each company one signed, verified notice. Each company's agent checks the notice against its own PayPal records and proposes the fix: cancel the subscription, refund what was taken after the date of death, cancel open invoices. A person approves, or the company's own written auto-close rule does. The family gets one letter in their own language, plus a record they can keep.

Built for the PayPal AI Hackathon 2026 with the **PayPal Agent Toolkit (Python)**, **Gemini or OpenAI**, **AG Grid** and **Render**.

---

## Why

- Bereaved families in the UK spend about **7 million hours a year** cancelling a dead person's subscriptions. The research values that time at **£171m**. The average person has **11.47** services and bills in their name, and each has to be contacted separately. (Life Ledger research, via [Funeral Service Times](https://funeralservicetimes.co.uk/news/people/2021/01/14/bereaved-families-lose-171m-a-year-cancelling-subscriptions))
- **22 of the 49** largest UK savings providers had no way to report a death online. ([Fairer Finance, 2022](https://www.fairerfinance.com/insights/press-releases/financial-services-firms-continuing-to-charge-customers-after-they-die))
- Recurring PayPal payments, both subscriptions and saved billing agreements, keep running until someone stops each one.

For merchants it's a moment of trust. A bereavement handled in minutes, with an automatic refund, is the opposite of a chargeback.

## How it works

| # | Who | What happens |
|---|---|---|
| 1 | Family | Uploads a photo of the death certificate. A vision model reads it in any language: English, Hindi, Tamil, Telugu and more. **The image is never stored.** Only the extracted fields and a SHA-256 fingerprint are kept, so the same certificate can't be reused for someone else. |
| 2 | Code | Eight checks, four of them blocking. A notice can't be sent if the certificate is reused, the date is impossible, the document isn't a death certificate, or **the account made payments by itself after the date of death**, which is the strongest sign of a false claim. |
| 3 | Family | Uploads the person's PayPal activity export (CSV). Deterministic rules find subscriptions, billing agreements and open invoices, and leave one-off purchases and payments to family alone, each with a stated reason. Day/month order is detected per file, because Indian exports use DD/MM/YYYY. |
| 4 | Hub | Signs one notice per company with **Ed25519** (`afterword/notice@1`). The notice carries only the name, PayPal email, date of death, the payment references and the verification result. The certificate itself never leaves. Merchants verify the signature with the hub key at `/.well-known/afterword-hub.json`. |
| 5 | Merchant agent | Verifies the signature, then checks PayPal's own record. `show_subscription_details` must match the subscriber's name and email. Transactions after the date of death are listed, then it proposes `cancel_subscription`, `refund_capture` for each post-death charge, and `cancel_sent_invoice`. |
| 6 | Person or rule | Approves. Auto-close only applies when every check passes **and** refunds are under the merchant's own cap. Everything else waits for support staff in the AG Grid inbox. |
| 7 | Family | Watches each company close live. Gets one letter in their language, ready-to-send emails for companies not on the network, and a PDF record. |

## How we use PayPal

| Capability | Via | Used for |
|---|---|---|
| `show_subscription_details` | PayPal Agent Toolkit | Identity match against PayPal's own subscriber record |
| `cancel_subscription` | Toolkit | Stop future charges, after approval |
| `cancel_sent_invoice`, `get_invoice`, `send_invoice` | Toolkit | Open invoices addressed to the deceased |
| `create_product`, `create_subscription_plan`, `create_subscription` | Toolkit | Building the sandbox demo (`scripts/sandbox_setup.py`) |
| `GET /v1/billing/subscriptions/{id}/transactions` | Toolkit's authenticated client (no Python tool exists) | Find captures after the date of death |
| `POST /v2/payments/captures/{id}/refund` with `PayPal-Request-Id` | Toolkit's authenticated client (no refund tool exists in Python) | Refund post-death charges exactly once |

## How we use AI

| Where | What the model does | What code decides |
|---|---|---|
| Certificate | Reads any language and format, transliterates names, notes visible alterations | Eight checks; four blocking |
| Letters | Writes short, specific, kind letters in the family's language | Facts and amounts are passed in; the model can't change them |
| Merchant agent | Drafts the review | Every money-moving call goes through the approval gate |

## Techniques under the hood

| Problem | Technique | Where |
|---|---|---|
| A forger re-saves or edits a real certificate, so its SHA-256 changes | **Perceptual hash** (64-bit DCT pHash), Hamming distance ≤ 10 means the same document | `forensics.py` |
| A name or date typed onto a photo of a real certificate | **JPEG-ghost error-level analysis** (after Farid, 2009). The page is re-compressed across a sweep of qualities, then text blocks are compared with text blocks using robust z-scores (median / MAD). Only a 16×12 grid of block scores is kept, so the text can't be rebuilt from it | `forensics.py` |
| One fake death used at several companies | **Fraud graph across all cases**. Union-find over certificate hashes, registration numbers, person and claimant identities. It blocks reuse for a different person, and blocks a claimant who reports 3 or more deaths in 30 days. When siblings report the same parent, the cases are linked instead of blocked | `fraud.py` |
| Telling recurring charges from one-offs, despite price rises | **Robust period estimation**: median inter-arrival gap snapped to canonical billing periods, with a confidence score from MAD. Amounts may vary up to 25% when the timing is periodic | `activity.py` |
| What happens if nobody acts | **Next-charge prediction** for each subscription, including annual renewals, giving the cost per year | `activity.py` |
| "Trust me, we cancelled it" | **Two-way signatures and independent checks**. The hub signs notices (Ed25519). Each merchant signs a closure receipt with its own key. The hub then re-reads PayPal: the subscription must show `CANCELLED`, the capture `REFUNDED` and the invoice `CANCELLED` | `notices.py`, `merchant.py` |
| Merchants already run AI support agents | **MCP server** at `/mcp` (streamable HTTP). It has read-and-verify tools only: `list_notices`, `get_notice`, `verify_notice`. No money tools | `mcp_server.py` |
| PayPal confirming changes directly | **Webhook receiver** for `BILLING.SUBSCRIPTION.CANCELLED` and `PAYMENT.CAPTURE.REFUNDED`. Every event is checked with PayPal's `verify-webhook-signature` API, and unverified events are rejected | `main.py` |
| "Your payment failed" emails sent to the dead | When a case closes, the merchant agent turns off all emails to that customer | `merchant.py` |
| The PayPal account itself | A pre-filled cover sheet and checklist for PayPal's own deceased-account process (executor only), following PayPal's UK help article | `closure.py` |

**Known limits, stated plainly.**
- Error-level analysis misses edits on very high-quality originals (above about JPEG 90). It can't analyse lossless images.
- It is one signal, and blocks only at strong evidence (peak z > 15).
- In the demo, all merchants share one database and one PayPal sandbox account. In production, each keeps its own keys and records.

## Where Afterword sits

| Existing service | What it covers | Gap Afterword fills |
|---|---|---|
| UK **Death Notification Service** | Participating banks | Not subscriptions or retailers ([Which?](https://www.which.co.uk/news/2018/06/new-service-notifies-banks-of-a-loved-ones-death-with-a-single-click/)) |
| UK **Tell Us Once** | Government bodies | Not private companies |
| PayPal's deceased-account process | Closing the PayPal account (executor, by email or post) | The help article doesn't address the recurring payments still running to merchants |

Afterword is "Tell Us Once" for the subscription economy, running on PayPal's rails.

## The trust layer

The PayPal Agent Toolkit gives a model direct access to money-moving endpoints, with no approval hook, amount cap or payee check. Afterword wraps it in one choke point, `backend/app/paypal_gateway.py`:

- **No model can move money.** `cancel_subscription`, `refund_capture` and `cancel_sent_invoice` raise `PermissionError` without an approved action id.
- **Approvals are bound to the payload.** Each proposal stores a SHA-256 of its exact parameters and expires after 24 hours. Any change after proposal blocks it.
- **Exactly once.** Every money call carries `PayPal-Request-Id`, so a retry replays the same refund.
- **Tamper-evident.** The audit log is append-only (SQLite triggers) and hash-chained, and there's a *Verify the chain* button.

`backend/tests/test_afterword.py` covers the full flow and each guarantee:
- a forged notice fails signature verification;
- a manual payment after death blocks sending;
- a mismatched subscriber can't auto-close;
- a tampered payload is refused;
- a replayed refund is idempotent;
- an over-refund is refused.

## Run it

**Hosted:** see the Devpost page for the link. Demo data loads automatically.

**Locally:**

```bash
cd backend && pip install -r requirements.txt
cp ../.env.example ../.env        # optional: keys; leave blank for offline mode
uvicorn app.main:app --port 8000  # API + built UI at http://localhost:8000 after `npm run build`
cd ../frontend && npm install && npm run dev   # UI with hot reload on :5173
```

Or one container: `docker build -t afterword . && docker run -p 8000:8000 --env-file .env afterword`

With no keys, it runs on a built-in emulator of the PayPal endpoints and an offline reader for the bundled specimen certificate. The header shows which mode is active.

### Real PayPal sandbox

1. In developer.paypal.com, go to Testing Tools → Sandbox Accounts and **create a Personal account named Lakshmi Narayanan**. The merchant agent checks this name.
2. Put `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and `DEMO_CLIENT_EMAIL` (that account's email) in `.env`.
3. Run `python backend/scripts/sandbox_setup.py create`, open each printed link, and subscribe as the sandbox buyer.
4. Run `python backend/scripts/sandbox_setup.py finish`, restart, and press *Start the demo again*.

### Tests

```bash
cd backend && python -m pytest -q
```

## Testing instructions for judges

1. Open the site. In **For families**, press *Fill in the demo family*, then *Continue*.
2. Press *Use the demo specimen*. It's a fictional certificate stamped SPECIMEN. Every check passes, and the image is deleted. Open *How we checked the image*.
3. Press *Use the demo export*. Afterword finds 8 things still charging or billing the account (one company isn't on Afterword yet). That's $73.92 already taken after the death, plus an annual antivirus renewal due in 5 days. Soonest charge first. It leaves a one-off purchase and a payment to the person's son alone.
4. Press *Tell 7 companies*. Six companies with auto-close rules close within seconds, each showing *Receipt signed* and *Confirmed on PayPal*. Each one cancels in PayPal, refunds the post-death charge and writes to the family.
5. Switch to **For businesses → The Daily Ledger**. This company reviews every case by hand. Open the notice, see PayPal's subscriber record match the certificate, and press *Approve all and close*.
6. Back in **For families**, read the letter in English, हिन्दी, தமிழ் or తెలుగు, and download the PDF record.
7. **For businesses → Audit trail → Verify the chain.**
8. **Fraud demo.** Press *Start the demo again*, enter any name, and choose *Try an edited copy (fraud demo)*. This is Lakshmi's certificate with the name changed. It is blocked twice: the heat map lights up the edited line, and the network check finds the original. To see the network flag, run steps 1–2 first, then start a new case.

## What's next

- Removing saved payment methods (Vault payment tokens) in the same notice.
- PayPal's own deceased-account process as an Afterword node.
- An executor role across multiple estates.
- Notices for banks and utilities, not just PayPal merchants.

## Team

- **Haresh G.** (SRM University AP): product, AI agents, pitch
- **\<Senior's name\>** (SRM University AP, CSE): frontend, deployment

MIT licensed. Every person, company and document in the demo is fictional.

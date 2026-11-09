# Afterword

[![tests](https://github.com/Haresh276/afterword/actions/workflows/ci.yml/badge.svg)](https://github.com/Haresh276/afterword/actions/workflows/ci.yml)
![license](https://img.shields.io/badge/license-MIT-2f5d52)
![PayPal](https://img.shields.io/badge/PayPal-Agent%20Toolkit%201.11-003087)
![AI](https://img.shields.io/badge/AI-Gemini%203.x%20Flash-5a4e6e)

**When someone dies, their subscriptions don't.**

Afterword lets a grieving family tell every company once.
1. It reads the death certificate.
2. It finds everything still charging the person's PayPal account.
3. It sends each company one signed, verified notice.
4. Each company's own agent checks that notice against its PayPal records, then:
   - **pauses billing at once** (reversible);
   - cancels;
   - refunds what was taken after the death.
5. Where a person reviews, a **Gemini reviewer agent** investigates first: it decides what to look up on PayPal, reads the results and hands the person a recommendation with its evidence. It has no tool that can move money.

**AI proposes, policy decides, a person approves.** Before anything irreversible happens, Afterword asks the account holder's own inbox: *"Was this a mistake?"*. One click from a living person, before the window ends, resumes billing and stops every pending step.

Services like Life Ledger already *tell* companies about a death. The charges keep coming for weeks while each one processes it. Afterword is the missing half: the company's own PayPal billing is paused within seconds, only what was taken after the death is refunded, and a living person can stop it before anything irreversible runs.

Built solo for the **PayPal AI Hackathon 2026** with the **PayPal Agent Toolkit**, **Gemini**, **AG Studio and AG Grid**, **Bryntum Scheduler** and **Render**.

> **Live demo:** https://afterword-6xki.onrender.com (Render free tier: the first visit can take about a minute to wake up). **2-minute video:** linked at the top of the Devpost page.
> **Run it yourself:** `python scripts/install.py && python scripts/start_demo.py` (Python 3.11+, any OS, no keys, no Node).

<img src="docs/screenshots/5-timeline-after.jpg" width="49%" alt="Bryntum timeline: charges before the death, after it, closed, and a year of charges that won't be taken"> <img src="docs/screenshots/3-paused-window.jpg" width="49%" alt="Billing paused at every company while the objection window runs">

---

## Try it in 3 minutes

| Time | Where | Do this | You'll see |
|---|---|---|---|
| 0:00 | **For families** | *Fill in the demo family* → *Continue* → *Use the demo specimen*. Open *How we checked the image*. | Eleven checks on a fictional certificate stamped SPECIMEN, then the image is deleted |
| 0:30 | Step 3 | *Use the demo export* | 8 things still charging Eleanor: what's been taken since she died, and what a year of doing nothing would cost (about $950). The **Bryntum timeline** shows every charge before and after the death. |
| 1:00 | Step 4 | *Tell 7 companies*, then *See the email* | Billing **paused** everywhere within seconds. A 30-second objection window runs (72 hours in production), and the email her own inbox received. |
| 1:30 | Step 4 | Wait | Six companies cancel and refund, each signs a receipt, and Afterword re-reads PayPal to confirm. The timeline turns green. |
| 1:45 | **For businesses → The Daily Ledger** | Open the notice → read the **AI reviewer** → *Approve all* | Gemini's recommendation and every PayPal lookup it made; PayPal's subscriber record matched to the certificate; each step labelled reversible or irreversible |
| 2:10 | **For businesses → Network insights** | Switch pages; press *Edit this report* | The **AG Studio** report across the network |
| 2:25 | **For businesses → PayPal sandbox proof** | *Run live check* | Real sandbox calls made right now, plus the recorded full run on real sandbox subscriptions |
| 2:40 | **Try to break it** | *Run every attack* | **10 of 10 blocked** (against the built-in PayPal copy), including talking the AI reviewer into approving |
| Bonus | Step 4 again | *Start the demo again*, send, open the email link, press *I'm alive. Stop this.* | Every pause undone, no money moved, the reporter flagged |

The shared demo runs on a built-in copy of the PayPal endpoints, and **every visitor gets their own demo world** (their own cases, inbox, merchant rules, audit view and simulated PayPal records). Every route checks it, so judges using the site at the same time can't see, approve, reset or block each other's runs. **PayPal sandbox proof** shows the same code on the real sandbox.

## Why it matters

- **3,072,666** people died in the US in 2024 ([CDC NCHS Data Brief 548](https://www.cdc.gov/nchs/products/databriefs/db548.htm)).
- Families take an average of **15 months** and spend **$12,616** settling a loved one's affairs ([Empathy, Cost of Dying 2024](https://www.prnewswire.com/news-releases/empathy-releases-annual-cost-of-dying-report-for-2024-302036982.html)).
- PayPal's own deceased-account process is still email and paper (certificate, executor ID, cover sheet), and recurring billing keeps charging while it runs ([PayPal Help](https://www.paypal.com/us/cshelp/article/how-do-i-close-the-paypal-account-of-a-deceased-relative-help220)).
- In the UK, closing a dead person's accounts and subscriptions takes families **7 million hours a year** ([Life Ledger](https://funeralservicetimes.co.uk/news/people/2021/01/14/bereaved-families-lose-171m-a-year-cancelling-subscriptions)).
- Banks have death-notification services. The subscription economy has nothing, and recurring payments keep running until someone stops each one.
- The flip side: about **12,000 living Americans are wrongly declared dead each year** ([Smithsonian, citing SSA](https://www.smithsonianmag.com/smart-news/about-12200-people-are-erroneously-declared-dead-every-year-us-government-180955549/)). Any "report a death" tool can be used to grief a living person. Afterword is built around that risk.

## How it works

```mermaid
flowchart LR
  F[Family] -->|certificate photo| C[Certificate agent<br/>Gemini vision + 11 checks<br/>pHash, JPEG-ghost ELA]
  F -->|PayPal activity CSV| A[Activity agent<br/>billing-period estimation]
  C --> G{Fraud graph<br/>across all cases}
  A --> H[Afterword hub]
  G --> H
  H -->|Ed25519-signed notice| M1[Merchant agent<br/>auto-close rule]
  H -->|signed notice| M2[Merchant agent<br/>human review]
  R[Gemini reviewer agent<br/>read-only PayPal tools] -.->|recommendation + evidence| M2
  H -->|"Was this a mistake?"| X[Account holder's own inbox]
  X -.->|"I'm alive"| H
  M1 --> P[(PayPal<br/>Agent Toolkit + REST)]
  M2 --> P
  P -->|re-read: CANCELLED / REFUNDED| H
  M1 -->|signed closure receipt| H
```

1. **Read.** Gemini reads the certificate (English, Spanish or any other language). **The image is never stored**: only the fields, a SHA-256 and a perceptual hash are kept.
2. **Check.** Eleven checks run. Five always block, and error-level analysis blocks when its evidence is strong:
   - document type;
   - impossible dates;
   - case details that differ from the certificate (so nobody can move the date of death to claim more refunds);
   - the fraud network (the same certificate or registration number used for someone else);
   - payments the person made themselves after the date of death;
   - an edited image (JPEG-ghost error-level analysis), when the anomaly is strong.
3. **Find.** The PayPal activity export is parsed into subscriptions, annual renewals, saved billing agreements and open invoices. One-off purchases and payments to family are left alone, with the reason shown.
4. **Notify.** One Ed25519-signed notice goes to each company (`afterword/notice@1`). It carries only the name, PayPal email, date of death, payment references and the objection deadline, **never the certificate**.
5. **Proof of life.** The account holder's own address is asked whether this is a mistake.
6. **Verify.** Each company's agent checks the signature, matches PayPal's subscriber record (`show_subscription_details`) to the certificate, and lists transactions after the death. It proposes:
   - **pause now** (reversible);
   - then **cancel**, **refund each post-death capture** and **cancel open invoices** (irreversible, held until the window ends).
7. **Approve.** Where a person reviews, the Gemini reviewer has already investigated and left its recommendation. A person approves, or the company's auto-close rule does. The rule only fires when every check passes and refunds are under the company's cap, and never consults a model.
8. **Close.** The company signs a closure receipt with its own key and stops all emails to the deceased.
9. **Confirm.** Afterword **re-reads PayPal** to confirm `CANCELLED` and `REFUNDED`.
10. **For the family.** A letter in their language, ready-to-send emails for companies not on Afterword, a PDF record, and a filled-in cover sheet for PayPal's own deceased-account process.

## How it meets each judging criterion

| Criterion | What to look at |
|---|---|
| **Technological implementation** | PayPal Agent Toolkit 1.11 plus REST through the toolkit's own authenticated client. One choke point for every money call that verifies the approval itself. A gateway-enforced objection window, payload-hash-bound atomic approvals, `PayPal-Request-Id` idempotency, and a hash-chained audit log. A Gemini agent with read-only PayPal tools. 38 tests, CI on Linux, Windows and macOS (Python 3.11–3.13), and a scripted real-sandbox run. |
| **Design** | Six calm steps for a grieving family; a Bryntum timeline of the money; countdown and plain wording; letters in English, Spanish and more. A merchant console with AG Grid and AG Studio. |
| **Potential impact** | Three million US deaths a year, each with its own set of subscriptions. The design covers both directions: families, and businesses (fewer chargebacks, no dunning emails to the dead, auto-close rules). |
| **Innovation** | Reversible-first two-phase close with a proof-of-life veto. An AI reviewer that investigates on PayPal but structurally cannot move money. Signed notices, signed receipts and independent re-reads of PayPal. A fraud graph that a forger can't use to lock out the real family. A live red-team page. |
| **Presentation** | A 2-minute video, a 3-minute judge path above, a hosted demo, and screenshots below. |

## The trust layer

The AI reads documents, writes letters and investigates notices. **No language model sits in the money path.** Amounts and targets come from code reading PayPal, and every money-moving call goes through `backend/app/paypal_gateway.py`.

| Guarantee | How |
|---|---|
| Nothing irreversible during the objection window | Each action carries `not_before`, and the gateway itself refuses earlier calls (`WindowOpen`) |
| No money moves without approval | Cancel, pause, resume, refund and cancel-invoice are refused unless they name an action that exists, is being executed right now by `approve()`, is for this exact tool and carries this exact payload |
| An approval can't be reused for something else | It's bound to the SHA-256 of the exact payload, is claimed atomically (two approvers can't both send it), and is valid for 24 hours after it first becomes runnable |
| Exactly once | Refunds, pauses and resumes send `PayPal-Request-Id: afterword-<action id>`; an executed approval can't be replayed through the gateway at all |
| The AI can't spend | The reviewer agent is offered only read-only tools, may only look up ids the signed notice names, and its PayPal calls go through the same choke point |
| Refunds can't be redirected | They go back to the original capture only; nothing is ever paid to the reporter |
| A living person can stop it | A signed withdrawal resumes paused billing, rejects pending steps, and flags the reporter and the person |
| A forger can't lock out the real family | A reuse flag is critical only if the other case *passed*; a blocked forgery becomes a note on the genuine case |
| Tamper-evident | An append-only, SHA-256 hash-chained audit log, with a *Verify the chain* button |

<img src="docs/screenshots/8-try-to-break-it.jpg" width="49%" alt="Try to break it: 10 of 10 attacks blocked"> <img src="docs/screenshots/6-merchant-review.jpg" width="49%" alt="Merchant review: PayPal matches and reversible / irreversible steps">

## PayPal

| Capability | Via | Used for |
|---|---|---|
| `create_product`, `create_subscription_plan`, `create_subscription` | Agent Toolkit | Live check; building the real sandbox run |
| `show_subscription_details` | Agent Toolkit | Match the subscriber's name and email to the certificate |
| `cancel_subscription` | Agent Toolkit | Stop future charges, after the window |
| `get_invoice`, `send_invoice`, `cancel_sent_invoice` | Agent Toolkit | Unpaid invoices addressed to the deceased |
| `/v1/billing/subscriptions/{id}/suspend` and `/activate` | Toolkit's authenticated client | Reversible pause, and resume if the person objects |
| `/v1/billing/subscriptions/{id}/transactions` | Toolkit's authenticated client | Captures after the date of death |
| `/v2/payments/captures/{id}/refund` (falls back to `/v1/payments/sale/{id}/refund`) | Toolkit's authenticated client | Refund each post-death charge exactly once |
| `/v1/notifications/verify-webhook-signature` | REST | Webhooks are logged only when PayPal vouches for them (completion is confirmed by re-reading PayPal, not by webhooks) |

**Proof it's real.**
- `scripts/sandbox_run.py` subscribes a sandbox buyer named Eleanor Hayes to five companies, then runs Afterword on those real subscriptions. It writes `evidence/sandbox_run.json` and [`evidence/SANDBOX_RUN.md`](evidence/), which record PayPal's ids, statuses and debug ids.
- The hosted app's *Run live check* button makes real sandbox calls on demand.

## AI

| Where | Model | Code around it |
|---|---|---|
| Certificate | Gemini 3.x Flash (vision, native API) reads any language and layout, and notes visible alterations | Eleven deterministic checks decide; the model's reading never overrides them |
| Reviewer agent | Gemini plans its own investigation: reads the notice, looks up each subscription, transaction list and invoice on PayPal, checks earlier reports, then recommends approve / needs a person / reject with concerns | Tools are read-only and limited to ids in the signed notice; data in tool results is treated as untrusted; a person still approves |
| Letters | Kind, specific letters in the family's language | Facts and amounts are passed in; templates for English, Spanish and Hindi when no key is set |
| AG Studio assistant | AG Studio's own AI agent explains and builds charts | `/api/ai/responses`: an OpenAI Responses-API shim over Gemini, so AG's adapter runs unchanged |
| Companies' own agents | **MCP** at `/mcp`: `list_notices`, `get_notice`, `verify_notice`, `check_customer_before_collecting` | Read-only, and each call needs that business's own key, so an agent only ever sees its own customers (and never the family's contact details). The last tool lets a billing or collections agent check before chasing a failed payment, so no one dunns a dead customer. |

The server picks the newest Flash model the key can use from Google's live model list, with thinking set to `minimal`, so a reply takes about a second. If the model is slow or down, a circuit breaker switches every AI step to its offline path at once, so a visitor never waits on a timeout.

## Sponsor tools

<img src="docs/screenshots/7-ag-studio.jpg" width="60%" alt="AG Studio network report">

- **AG Studio.** *For businesses → Network insights* is a three-page report (network, PayPal steps, safety) over live hub data plus a seeded 90-day simulation. Every row is labelled `Live` or `Simulated`. It has page tabs, in-place edit mode and the AI assistant. **AG Grid** runs the merchant inbox.
- **Bryntum Scheduler.** The family's timeline: one row per company showing charges before and after the death, *told* and *closed* markers, the objection window, and the next year of charges that won't be taken.
- **Render.** A one-click Blueprint (`render.yaml`, Docker, free plan, health check `/api/status`).

## Run it yourself

```bash
git clone https://github.com/Haresh276/afterword && cd afterword
python scripts/install.py        # Python 3.11+, any OS
python scripts/start_demo.py     # http://localhost:8000, offline, no keys
cd backend && python -m pytest -q
```

The UI is prebuilt in `frontend/dist`. To rebuild it: `cd frontend && npm ci && npm run build`.

The PayPal toolkit is installed with `--no-deps`. Its core needs only `httpx`, `requests` and `pydantic`, while its pinned langchain/crewai extras (unused here) don't install on Python 3.13+.

Optional `.env` in the repo root:

| Variable | Effect |
|---|---|
| `GEMINI_API_KEY` | Live certificate reading, letters in any language, AG Studio assistant |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | *Run live check*, verified webhooks, `scripts/sandbox_run.py` |
| `SANDBOX_BUYER_EMAIL` | The Personal sandbox account named Eleanor Hayes, for `scripts/sandbox_run.py` |
| `AFTERWORD_OBJECTION_WINDOW` | Seconds before irreversible steps may run (demo 30, production 259200) |
| `AFTERWORD_PAYPAL_MODE` | `sandbox` runs the whole demo on the real sandbox (single user); default `mock` |

## Limits, stated plainly

- Error-level analysis misses edits on very high-quality originals (above about JPEG 90) and can't analyse lossless images. It's one signal among several.
- In the demo, all seven companies share one database and one sandbox account. In production, each keeps its own keys and records.
- The AG Studio report mixes live rows with a labelled, seeded simulation, so it has shape on a fresh install.
- The demo has no sign-in: anyone with the link can act as any business. Per-visitor worlds keep judges apart; in production each business sits behind its own login and keys.
- "Verification" checks the document and the PayPal records against each other. It doesn't query a vital-records office; the proof-of-life email and the objection window cover that gap.
- Use a paid Gemini key in production. On the free tier Google may use submitted content to improve its products, and a death certificate shouldn't go there.

## What's next

- Removing Vault payment tokens in the same notice.
- PayPal's deceased-account team as an Afterword node.
- An executor role across estates.
- Banks and utilities on the same signed protocol.

Built solo by **Haresh G.** · MIT licensed · every person, company and document in the demo is fictional.

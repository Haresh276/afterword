# Afterword: Devpost submission text

**Project name:** Afterword

**Tagline (under 200 characters):** When someone dies, their subscriptions don't. Tell every company once: their PayPal agent verifies, pauses, cancels and refunds. A living person can veto it.

## Inspiration
3,072,666 people died in the US in 2024 (CDC). Each left behind a set of subscriptions still charging their account: streaming, cloud storage, news, apps, an annual antivirus renewal nobody remembers. Settling a loved one's affairs takes families an average of 15 months and $12,616 (Empathy, Cost of Dying 2024). Part of that is the second job grief hands you: calling every company, one at a time, death certificate in hand.

Notification services like Life Ledger can *tell* companies. But the charges keep coming while each company processes the letter, and PayPal's own deceased-account process is still email and paper. Nobody pauses the billing.

The first question we asked was the dangerous one: what stops someone reporting a *living* person dead? About 12,000 living Americans are wrongly declared dead every year. So Afterword is built around that question as much as around the family.

## What it does
- **The family tells us once.** Gemini reads the death certificate (English, Spanish or any other language), eleven checks run, and the image is deleted. Only the fields and two fingerprints are kept.
- **It finds what's still charging.** From the PayPal activity export, Afterword finds every subscription, annual renewal and unpaid invoice, and what was taken after the death. It predicts every charge in the next year, and a **Bryntum** timeline shows the whole picture.
- **One signed notice per company.** Each company's own agent:
  - verifies the Ed25519 signature;
  - matches PayPal's subscriber record to the certificate;
  - **pauses billing immediately**. That step is reversible.
- **An AI reviewer investigates.** Where a person reviews, a Gemini agent goes first: it decides what to look up, reads the notice, checks each subscription, transaction and invoice on PayPal, looks for earlier reports, and hands the person a recommendation with every lookup it made. It is offered no tool that can move money and can only look up ids the signed notice names. **AI proposes, policy decides, a person approves.**
- **Proof of life.** The account holder's own inbox is asked *"Was this a mistake?"*. Irreversible steps (cancel, refund, cancel invoice) wait out an objection window that the PayPal gateway itself enforces. One click from a living person withdraws every notice, resumes billing, moves no money, and flags the reporter.
- **Then it closes for real.** Agents cancel and refund through the **PayPal Agent Toolkit**, sign a closure receipt, and stop all emails to the deceased. Afterword re-reads PayPal to confirm.
- **For businesses:**
  - an **AG Grid** inbox with auto-close rules and human review;
  - an **AG Studio** network report whose AI agent runs on Gemini;
  - an MCP server, so a company's own billing or collections agents can verify notices and avoid chasing a dead customer for a failed payment.
- **Try to break it.** Ten live attacks against the real code, from a forged certificate to talking the AI reviewer into approving a refund. All ten are blocked on the hosted demo.

## How we built it
- **Backend.** FastAPI and SQLite.
  - The PayPal Agent Toolkit 1.11 (Python) handles products, plans, subscriptions and invoices.
  - The toolkit's own authenticated client handles the endpoints it has no tool for: suspend/activate, transactions, refunds with `PayPal-Request-Id`, and webhook signature verification.
- **AI.** Gemini 3.x Flash through its native API (thinking set to minimal, about a second a call) reads certificates, writes letters and runs the reviewer agent with function calling. **No model sits in the money path.** Amounts and targets come from code reading PayPal. A circuit breaker sends every AI step to its offline path if the model is slow, so a visitor never waits on a timeout.
- **Forensics and detection.**
  - A DCT perceptual hash and JPEG-ghost error-level analysis.
  - A union-find fraud graph across all cases.
  - Robust billing-period estimation (median inter-arrival gap, MAD confidence, price-rise tolerance).
- **Trust layer.**
  - One choke point for every money call. It checks the approval itself: the action must exist, be executing now, and match the tool and the exact payload.
  - Approvals bound to a SHA-256 of the payload, claimed atomically, valid for 24 hours once runnable.
  - A `not_before` time that enforces the objection window.
  - An append-only, hash-chained audit log.
- **Frontend.**
  - React and Vite.
  - **AG Grid** and **AG Studio**. A small Responses-API shim over Gemini lets AG's documented AI adapter run unchanged.
  - **Bryntum Scheduler** for the family timeline.
- **Deployment and proof.**
  - Docker on **Render** (Blueprint).
  - 37 tests, with CI on Linux, Windows and macOS (Python 3.11–3.13).
  - `scripts/sandbox_run.py` runs the whole flow on real PayPal sandbox subscriptions and records the evidence.

## Challenges we ran into
- **Griefing.** Any "report a death" product can be weaponised. We made every irreversible step wait, put the wait in the gateway rather than the UI, and gave the subject a one-click veto.
- **A fraudster could lock out the real family.** If someone submits an edited copy first, the genuine family's case would match it. A reuse flag is now critical only if the other case *passed*.
- **A hosted demo on real money objects gets used up.** The first judge would cancel the sandbox subscriptions for everyone else. So the shared demo runs on a faithful copy of the PayPal endpoints, every visitor gets a private demo world, and a live-check button and a recorded full sandbox run prove the same code on real PayPal.
- **Gemini on a free key.** Through the OpenAI-compatibility endpoint, Gemini 3.x calls hung for 20+ seconds from Render. We moved to the native API with minimal thinking (under a second), pick the model from Google's live model list, and added a circuit breaker.
- **Toolkit gaps and packaging.** The Python toolkit has no refund, suspend or transactions tool, so we call those endpoints through its authenticated client, behind the same approval gate. Its pinned extras don't install on Python 3.13+, so we install its core alone.

## Accomplishments that we're proud of
- **A reversible-first, two-phase close** with a proof-of-life veto, enforced in the payment gateway rather than the UI.
- **An agent that investigates but structurally can't spend.**
- **A red-team page** judges can attack themselves.
- **Letters that are kind and specific**, in the family's language.

## What we learned
Trust is the product. The model is the easy part. What makes an agent safe near money is the machinery around it: approvals bound to payloads, idempotency keys, reversible-first ordering, and independently re-reading the ledger.

## What's next
- Removing Vault payment tokens in the same notice.
- PayPal's deceased-account team as an Afterword node.
- An executor role across estates.
- Banks and utilities on the same signed protocol.

## Built with
python, fastapi, sqlite, paypal-agent-toolkit, paypal-rest-api, paypal-subscriptions, paypal-invoicing, gemini, react, vite, ag-grid, ag-studio, bryntum-scheduler, mcp, ed25519, numpy, pillow, reportlab, docker, render, github-actions

## Try it out (links)
- Live demo: https://afterword-6xki.onrender.com
- Code: https://github.com/Haresh276/afterword
- Video: `<YouTube link>`

## Testing instructions (paste into the "testing instructions" or description field)
- **Hosted demo.** Open the link; the first load can take about a minute on Render's free tier. No login needed.
- **Path.** *For families → Fill in the demo family → Use the demo specimen → Use the demo export → Tell 7 companies*. Then *For businesses → The Daily Ledger* (open the notice: the AI reviewer's findings), *Network insights* and *PayPal sandbox proof*, then *Try to break it*.
- **Run locally.** `python scripts/install.py && python scripts/start_demo.py` (Python 3.11+, no keys).

All people, companies and documents in the demo are fictional.

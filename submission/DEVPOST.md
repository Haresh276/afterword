# Afterword: Devpost submission text

**Project name:** Afterword

**Tagline (under 200 characters):** When someone dies, their subscriptions don't. Tell every company once: a signed notice their own PayPal agent verifies, pauses, cancels and refunds, with an "I'm alive" veto.

## Inspiration
3,072,039 people died in the US in 2024. Each left behind a set of subscriptions still charging their account: streaming, cloud storage, news, apps, an annual antivirus renewal nobody remembers. Settling a loved one's affairs takes families an average of 13 months and nearly $13,000. Part of that is the second job grief hands you: calling every company, one at a time, death certificate in hand.

Banks have death-notification services. The subscription economy has nothing.

The first question we asked was the dangerous one: what stops someone reporting a *living* person dead? About 12,000 living Americans are wrongly declared dead every year. So Afterword is built around that question as much as around the family.

## What it does
- **The family tells us once.** Gemini reads the death certificate (English, Spanish or any other language), ten checks run, and the image is deleted. Only the fields and two fingerprints are kept.
- **It finds what's still charging.** From the PayPal activity export, Afterword finds every subscription, annual renewal and unpaid invoice, and what was taken after the death. It predicts every charge in the next year, and a **Bryntum** timeline shows the whole picture.
- **One signed notice per company.** Each company's own agent:
  - verifies the Ed25519 signature;
  - matches PayPal's subscriber record to the certificate;
  - **pauses billing immediately**. That step is reversible.
- **Proof of life.** The account holder's own inbox is asked *"Was this a mistake?"*. Irreversible steps (cancel, refund, cancel invoice) wait out an objection window that the PayPal gateway itself enforces. One click from a living person withdraws every notice, resumes billing, moves no money, and flags the reporter.
- **Then it closes for real.** Agents cancel and refund through the **PayPal Agent Toolkit**, sign a closure receipt, and stop all emails to the deceased. Afterword re-reads PayPal to confirm.
- **For businesses:**
  - an **AG Grid** inbox with auto-close rules and human review;
  - an **AG Studio** network report whose AI agent runs on Gemini;
  - an MCP server, so a company's own billing or collections agents can verify notices and avoid chasing a dead customer for a failed payment.
- **Try to break it.** Nine live attacks against the real code, from a forged certificate to prompt injection. All nine are blocked.

## How we built it
- **Backend.** FastAPI and SQLite.
  - The PayPal Agent Toolkit 1.11 (Python) handles products, plans, subscriptions and invoices.
  - The toolkit's own authenticated client handles the endpoints it has no tool for: suspend/activate, transactions, refunds with `PayPal-Request-Id`, and webhook signature verification.
- **AI.** Gemini 2.5 Flash reads certificates (vision) and writes letters. **No model sits in the money path.** Amounts and targets come from code reading PayPal.
- **Forensics and detection.**
  - A DCT perceptual hash and JPEG-ghost error-level analysis.
  - A union-find fraud graph across all cases.
  - Robust billing-period estimation (median inter-arrival gap, MAD confidence, price-rise tolerance).
- **Trust layer.**
  - One choke point for every money call.
  - Approvals bound to a SHA-256 of the payload, with a 24-hour expiry.
  - A `not_before` time that enforces the objection window.
  - An append-only, hash-chained audit log.
- **Frontend.**
  - React and Vite.
  - **AG Grid** and **AG Studio**. A small Responses-API shim over Gemini lets AG's documented AI adapter run unchanged.
  - **Bryntum Scheduler** for the family timeline.
- **Deployment and proof.**
  - Docker on **Render** (Blueprint).
  - 25 tests, with CI on Python 3.11–3.13.
  - `scripts/sandbox_run.py` runs the whole flow on real PayPal sandbox subscriptions and records the evidence.

## Challenges we ran into
- **Griefing.** Any "report a death" product can be weaponised. We made every irreversible step wait, put the wait in the gateway rather than the UI, and gave the subject a one-click veto.
- **A fraudster could lock out the real family.** If someone submits an edited copy first, the genuine family's case would match it. A reuse flag is now critical only if the other case *passed*.
- **A hosted demo on real money objects gets used up.** The first judge would cancel the sandbox subscriptions for everyone else. So the shared demo runs on a faithful copy of the PayPal endpoints, while a live-check button and a recorded full sandbox run prove the same code on real PayPal.
- **Toolkit gaps and packaging.** The Python toolkit has no refund, suspend or transactions tool, so we call those endpoints through its authenticated client, behind the same approval gate. Its pinned extras don't install on Python 3.13+, so we install its core alone.

## Accomplishments that we're proud of
- **A reversible-first, two-phase close** with a proof-of-life veto. We haven't seen it in any other entry.
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
- Live demo: `https://<your-service>.onrender.com`
- Code: https://github.com/Haresh276/afterword
- Video: `<YouTube link>`

## Testing instructions (paste into the "testing instructions" or description field)
- **Hosted demo.** Open the link; the first load can take about a minute on Render's free tier. No login needed.
- **Path.** *For families → Fill in the demo family → Use the demo specimen → Use the demo export → Tell 7 companies*. Then *For businesses → The Daily Ledger*, *Network insights* and *PayPal sandbox proof*, then *Try to break it*.
- **Run locally.** `python scripts/install.py && python scripts/start_demo.py` (Python 3.11+, no keys).

All people, companies and documents in the demo are fictional.

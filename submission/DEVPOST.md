# Afterword: Devpost submission text

**Tagline (under 200 characters):** When someone dies, their subscriptions don't. Afterword tells every company once, with a signed notice their own PayPal agent verifies, pauses, cancels and refunds.

## Inspiration
When a parent dies, the family inherits a second job: finding every subscription still charging the account and phoning each company, one at a time, with a death certificate. In the UK alone that's about 7 million hours a year, valued at £171m, and the average person has 11.47 accounts and services to close. Banks have the Death Notification Service and governments have Tell Us Once. The subscription economy has nothing, and recurring PayPal payments keep running until someone stops each one.

The first design question we asked was the dangerous one: what stops someone reporting a *living* person dead? About 12,000 living Americans are wrongly declared dead every year. Afterword is built around that question.

## What it does
- **The family tells us once.** A vision model reads the death certificate in any language, ten checks run, and the image is deleted. Only the fields and two fingerprints are kept.
- **It finds what's still charging.** From the PayPal activity export it finds every subscription, annual renewal and open invoice, and what was taken after the death. It predicts every charge in the next year, and a Bryntum timeline shows it all at once.
- **One signed notice per company.** Each company's agent verifies the Ed25519 signature, matches PayPal's own subscriber record to the certificate, and **pauses billing immediately**. That step is reversible.
- **Proof of life.** The account holder's own address is asked *"Was this a mistake?"*. Irreversible steps (cancel, refund, cancel invoice) wait out an objection window that the PayPal gateway itself enforces. One click from a living person withdraws every notice, resumes billing, moves no money and flags the reporter.
- **Then it closes for real.** Agents cancel and refund through the PayPal Agent Toolkit, sign a closure receipt, and stop all emails to the deceased. Afterword re-reads PayPal to confirm.
- **For businesses.** An AG Grid inbox with auto-close rules and human review. An AG Studio network report whose AI agent runs on Gemini. A read-only MCP server so their own support agents can verify notices.
- **Try to break it.** Nine live attacks against the real code. All nine are blocked.

## How we built it
- **Backend.** FastAPI and SQLite. The PayPal Agent Toolkit (Python) handles subscriptions and invoices. The toolkit's authenticated client handles REST calls it has no tool for: suspend/activate, transactions, refunds with `PayPal-Request-Id`, and webhook signature verification.
- **AI.** Gemini through an OpenAI-compatible client, for certificate reading and letters in English, Hindi, Tamil and Telugu. No model sits in the money path.
- **Forensics.** A DCT perceptual hash and JPEG-ghost error-level analysis. A union-find fraud graph across cases. Robust billing-period estimation using the median inter-arrival gap and MAD confidence.
- **Trust layer.** One choke point for every money call. Approvals are bound to a SHA-256 of the payload and expire after 24 hours. A `not_before` time enforces the objection window. The audit log is append-only and hash-chained.
- **Frontend.** React and Vite. **AG Grid** for the merchant inbox and **AG Studio** for network insights. We wrote a small Responses-API shim over Gemini so AG's documented AI adapter runs unchanged. **Bryntum Scheduler** draws the family timeline.
- **Deployment.** A Docker image and a **Render** Blueprint. 24 automated tests.

## Challenges we ran into
- **Griefing.** Any "report a death" product can be weaponised. We made every irreversible step wait, put the wait in the gateway rather than the UI, and gave the subject a one-click veto.
- **A fraudster could lock out the real family.** If someone submits an edited copy of a certificate first, the genuine family's later case would match it. A reuse flag is now critical only if the other case *passed*.
- **Gaps in the Python toolkit.** It has no refund, suspend or transactions tool. We call those endpoints through the toolkit's own authenticated client, behind the same approval gate.
- **Error-level analysis false positives.** Coloured disclaimer text and page borders triggered it. Luminance-only JPEG ghosts with an ink mask and robust z-scores fixed that. We state its limits plainly.

## Accomplishments that we're proud of
- **A two-phase close** (pause now, cancel later, undo on objection) that we haven't seen in any other entry.
- **The red-team page.** Judges can run the attacks themselves.
- **Kind, specific letters** in four Indian and English languages.

## What we learned
Trust is the product. The model is the easy part. What makes an agent safe to let near money is the boring machinery around it: approvals bound to payloads, idempotency keys, reversible-first ordering, and independently re-reading the ledger.

## What's next
- Removing Vault payment tokens in the same notice.
- PayPal's own deceased-account team as an Afterword node.
- An executor role across estates.
- Banks and utilities on the same signed protocol.

## Built with
python, fastapi, sqlite, paypal-agent-toolkit, paypal-rest-api, gemini, openai-compatible-api, react, vite, ag-grid, ag-studio, bryntum-scheduler, mcp, ed25519, numpy, pillow, reportlab, docker, render

## AI tools used (for the form)
- **Gemini 2.5 Flash.** Reads certificates (vision), writes letters, and powers the AG Studio assistant through our Responses-API shim.
- **The PayPal Agent Toolkit** is the agents' PayPal tool layer.
- **Claude (Anthropic)** was used as a coding assistant during development.

## Try it
- Repo: https://github.com/Haresh276/afterword
- Run: `python scripts/start_demo.py` (no keys needed).
- Judge path: see the README's 3-minute table.

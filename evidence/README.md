# Evidence

`sandbox_run.json` and `SANDBOX_RUN.md` are written by `python scripts/sandbox_run.py`. That script runs Afterword against
**real PayPal sandbox subscriptions**: it subscribes a sandbox buyer named Eleanor Hayes to five companies, pauses, cancels and
refunds them through the PayPal Agent Toolkit, then re-reads PayPal to confirm. The app shows these files under
*For businesses → PayPal sandbox proof*.

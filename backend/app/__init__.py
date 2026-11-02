"""ProofPay backend. Loads ../.env (repo root) or ./.env for local runs."""
from pathlib import Path

try:
    from dotenv import load_dotenv

    for p in (Path(__file__).resolve().parents[2] / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if p.exists():
            load_dotenv(p)
except ImportError:
    pass

"""Installs everything Afterword needs into the current Python (3.11 or newer), on any OS.

    python scripts/install.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.version_info < (3, 11):
    sys.exit(f"Python 3.11 or newer, please (this is {sys.version.split()[0]}). Get it from python.org.")
pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
subprocess.check_call(pip + ["-r", str(ROOT / "backend" / "requirements.txt")])
# The toolkit's core needs only httpx/requests/pydantic; its pinned langchain/crewai extras aren't used and
# don't install on Python 3.13+, so it is installed without them.
subprocess.check_call(pip + ["--no-deps", "paypal-agent-toolkit==1.11.0"])
import importlib  # noqa: E402
importlib.invalidate_caches()
from paypal_agent_toolkit.shared.api import PayPalAPI  # noqa: E402,F401
print("\nInstalled. Next:  python scripts/start_demo.py")

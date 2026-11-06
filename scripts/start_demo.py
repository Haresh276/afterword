"""One command for judges: install what's missing, build the UI if needed, start Afterword offline.

    python scripts/start_demo.py            # http://localhost:8000
    python scripts/start_demo.py --port 9000

No keys needed. Put GEMINI_API_KEY / PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET in .env to go live.
"""
import argparse
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND, FRONTEND = ROOT / "backend", ROOT / "frontend"


def run(cmd, cwd):
    print("$", " ".join(cmd))
    subprocess.check_call(cmd, cwd=cwd, shell=os.name == "nt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8000")
    ap.add_argument("--window", default="30", help="objection window in seconds (production: 259200)")
    a = ap.parse_args()
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or newer, please.")
    try:
        import fastapi, uvicorn, reportlab, cryptography, paypal_agent_toolkit  # noqa: F401,E401
    except ImportError:
        run([sys.executable, str(ROOT / "scripts" / "install.py")], ROOT)
    if not (FRONTEND / "dist" / "index.html").exists():
        if not shutil.which("npm"):
            sys.exit("The UI isn't built and npm isn't installed. Install Node.js 20+ and run again.")
        run(["npm", "install"], FRONTEND)
        run(["npm", "run", "build"], FRONTEND)
    env = {**os.environ, "AFTERWORD_OBJECTION_WINDOW": os.getenv("AFTERWORD_OBJECTION_WINDOW", a.window)}
    url = f"http://localhost:{a.port}"
    print(f"\nAfterword is starting at {url}  (Ctrl+C to stop)\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--port", a.port], cwd=BACKEND, env=env)


if __name__ == "__main__":
    main()

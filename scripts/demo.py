"""One command to run the demo: python scripts/demo.py

1. creates .venv and installs requirements if needed (then re-runs itself inside it)
2. ingests data (data/raw/*.csv if present, else the SYNTHETIC sample)
3. seeds the demo NGO (idempotent)
4. installs frontend packages if needed
5. starts the API (port 8000) and the web UI (port 5173)
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
API_PORT = os.getenv("API_PORT", "8000")
WEB_PORT = os.getenv("WEB_PORT", "5173")


def sh(cmd: list[str], **kw) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def wait_for(url: str, seconds: int = 60) -> None:
    import urllib.request

    for _ in range(seconds * 2):
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.5)
    print(f"warning: {url} did not respond within {seconds}s")


def has_active_dataset() -> bool:
    """True if a CSR dataset version has been built and activated (data/processed/datasets/active.json)."""
    return (ROOT / "data/processed/datasets/active.json").exists()


def read_env() -> dict:
    p = ROOT / ".env"
    if not p.exists():
        return {}
    return dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", p.read_text(encoding="utf-8"), re.M))


def ensure_session_secret() -> None:
    """Sessions are signed with SESSION_SECRET; generate one locally so logins survive restarts."""
    p = ROOT / ".env"
    if read_env().get("SESSION_SECRET", "").strip():
        return
    if not p.exists():
        shutil.copy(ROOT / ".env.example", p)
    text = p.read_text(encoding="utf-8")
    secret = secrets.token_urlsafe(48)
    if re.search(r"^SESSION_SECRET=.*$", text, re.M):
        text = re.sub(r"^SESSION_SECRET=.*$", f"SESSION_SECRET={secret}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\nSESSION_SECRET={secret}\n"
    p.write_text(text, encoding="utf-8")
    print("Generated SESSION_SECRET in .env")


def ensure_venv() -> None:
    if Path(sys.executable).resolve() == VENV_PY.resolve():
        return
    if not VENV_PY.exists():
        sh([sys.executable, "-m", "venv", str(VENV)])
        sh([str(VENV_PY), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
    # Re-run this script with the venv's interpreter.
    sys.exit(subprocess.call([str(VENV_PY), __file__, *sys.argv[1:]]))


def main() -> None:
    ensure_venv()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "API_PORT": API_PORT, "WEB_PORT": WEB_PORT}
    if not (ROOT / "data/processed/geo/geo.sqlite").exists():  # LGD geography first: ingest maps rows to it
        if not list((ROOT / "data/raw/geo").glob("*district*.csv")):
            sh([sys.executable, str(ROOT / "scripts/geo.py"), "fetch"], env=env)
        sh([sys.executable, str(ROOT / "scripts/geo.py"), "load"], env=env)
    if not has_active_dataset():
        sh([sys.executable, str(ROOT / "scripts/ingest.py")], env=env)
    ensure_session_secret()
    env = {**env, **{k: v for k, v in read_env().items() if k == "SESSION_SECRET"}}
    sh([sys.executable, str(ROOT / "scripts/seed_demo.py")], env=env)
    sh([sys.executable, str(ROOT / "scripts/seed_platform.py")], env=env)

    npm = shutil.which("npm")
    if not npm:
        sys.exit("npm not found: install Node.js 18+ to run the web UI")
    web = ROOT / "frontend"
    if not (web / "node_modules").exists():
        sh([npm, "install", "--no-audit", "--no-fund"], cwd=web)

    procs = [subprocess.Popen([sys.executable, "-m", "uvicorn", "fundbridge.api:app", "--port", API_PORT], cwd=ROOT, env=env)]
    wait_for(f"http://127.0.0.1:{API_PORT}/api/status")
    procs.append(subprocess.Popen([npm, "run", "dev", "--", "--port", WEB_PORT, "--strictPort"], cwd=web, env=env))
    url = f"http://localhost:{WEB_PORT}"
    wait_for(url)
    print(f"\nFundBridge running: UI {url}  ·  API http://localhost:{API_PORT}/docs  (Ctrl+C to stop)\n")
    webbrowser.open(url)
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()

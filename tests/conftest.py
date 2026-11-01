import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Tests always run against the SYNTHETIC sample in their own folder, so loading
# real filings into data/processed/ for the app never changes test results.
TEST_PROCESSED = ROOT / "data" / "processed_test"
os.environ["FUNDBRIDGE_PROCESSED_DIR"] = str(TEST_PROCESSED)
os.environ["GEMINI_MODEL_BACKUPS"] = ""  # routing tests assume no backups unless a test sets them
# Geography first: ingest maps every row to LGD codes.
if not (TEST_PROCESSED / "geo" / "geo.sqlite").exists():
    subprocess.run([sys.executable, str(ROOT / "scripts" / "geo.py"), "load"], check=True,
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"}, stdout=subprocess.DEVNULL)
if not (TEST_PROCESSED / "datasets" / "active.json").exists():
    subprocess.run([sys.executable, str(ROOT / "scripts" / "ingest.py"), "--sample"], check=True,
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"}, stdout=subprocess.DEVNULL)

"""Geography (LGD) commands.

  python scripts/geo.py fetch      download the LGD state + district CSVs from India Data Portal into data/raw/geo/
                                   and record their source URL and download time in data/raw/geo/SOURCES.json
  python scripts/geo.py load       load data/raw/geo/*.csv as a new geo_version (data/processed/geo/geo.sqlite)
  python scripts/geo.py info       show the loaded version, its source files and counts
  python scripts/geo.py unmapped   list place names that could not be matched (the admin review queue)

Source: India Data Portal dataset "LGD Codes" (Open Data Commons Attribution licence), which republishes the
Local Government Directory (lgdirectory.gov.in). The LGD site itself needs a captcha, so it is never automated;
a file downloaded from it by hand can be dropped into data/raw/geo/ instead.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fundbridge import geo  # noqa: E402

DATASET = "https://ckandev.indiadataportal.com/dataset/lgd-codes"
FILES = {
    "state-lgd-codes.csv": "https://ckandev.indiadataportal.com/dataset/a7419751-ac37-46ad-b938-638cda7b7b60/resource/"
                           "9ac39fa8-29b4-41e4-acdb-c81190a8c56f/download/state-lgd-codes.csv",
    "district-lgd-codes.csv": "https://ckandev.indiadataportal.com/dataset/a7419751-ac37-46ad-b938-638cda7b7b60/resource/"
                              "19df978a-675e-4ff5-8015-d0e1de447319/download/district-lgd-codes.csv",
}


def record_sources(downloaded_at: str | None = None) -> None:
    now = downloaded_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    src = {"_source": f"India Data Portal, 'LGD Codes' ({DATASET}), ODC-BY; republishes lgdirectory.gov.in"}
    src.update({name: {"url": url, "downloaded_at": now} for name, url in FILES.items()})
    (geo.GEO_RAW_DIR / "SOURCES.json").write_text(json.dumps(src, indent=2), encoding="utf-8")


def fetch() -> None:
    geo.GEO_RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 - fixed https URLs above
            data = r.read()
        if not data.lstrip().startswith(b'"id"') and b"," not in data[:200]:
            sys.exit(f"{name}: the download does not look like a CSV; nothing was saved.")
        (geo.GEO_RAW_DIR / name).write_bytes(data)
        print(f"Saved {name} ({len(data)} bytes)")
    record_sources()


def info() -> None:
    g = geo.Geo()
    i = g.info()
    print(f"geo_version {i['id']} loaded {i['created_at']} | source: {i['source']}")
    for f in i["files"]:
        print(f"  {f['name']}  sha256 {f['sha256'][:12]}  downloaded {f.get('downloaded_at', 'unknown')}  {f.get('url', '')}")
    print(f"  {len(g.states())} active states/UTs ({sum(s['kind'] == 'ut' for s in g.states())} UTs), "
          f"{len(g.districts())} districts; inactive: {[s['name'] for s in g.states(True) if s['status'] != 'active']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "info"
    if cmd == "fetch":
        fetch()
    elif cmd == "load":
        print(json.dumps(geo.load(), indent=2))
    elif cmd == "unmapped":
        for u in geo.Geo().unmapped():
            print(f"{u['id']:>5}  {u['kind']:<8} {u['raw']!r:<40} state {u['state_lgd_code'] or '-':<4} x{u['count']}  {u['context']}")
    else:
        info()

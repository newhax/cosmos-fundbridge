"""Ingest CSR filings as a new dataset version and activate it.

- Uses every *.csv in data/raw/ (REAL data) if present.
- Otherwise uses data/sample/ (SYNTHETIC), generating it first if needed.

Outputs: data/processed/datasets/v<N>/ (csr.sqlite, manifest.json, source/), the active pointer
data/processed/datasets/active.json, and data/processed/data_quality_report.json.
Earlier versions are kept so old decision traces can still be replayed.

Usage: python scripts/ingest.py               (REAL if data/raw/*.csv exist)
       python scripts/ingest.py --sample      (force the SYNTHETIC sample; used by the test suite)
       python scripts/ingest.py --list        (show all versions and which one is active)
       python scripts/ingest.py --activate N  (switch to version N; restored if it cannot be read)
       python scripts/ingest.py --rollback    (switch back to the previously active version)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from fundbridge import datasets  # noqa: E402
from fundbridge.config import DATASETS_DIR, QUALITY_REPORT_PATH, RAW_DIR, SAMPLE_DIR  # noqa: E402


def main(force_sample: bool = False) -> dict:
    files = [] if force_sample else sorted(RAW_DIR.glob("*.csv"))
    data_source = "REAL"
    if not files:
        files = sorted(SAMPLE_DIR.glob("*.csv"))
        data_source = "SAMPLE"
        if not files:
            from generate_sample import main as generate  # type: ignore

            generate()
            files = sorted(SAMPLE_DIR.glob("*.csv"))
    print(f"Ingesting {data_source} data from: {[f.name for f in files]}")

    m = datasets.build_version(files, data_source, created_by="scripts/ingest.py")
    datasets.activate(m["version"])
    from fundbridge.stores.csr_dataset import DatasetCSRStore

    report = DatasetCSRStore().data_quality_report()
    QUALITY_REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Loaded {m['rows_loaded']} rows, {m['companies']} companies -> {m['dataset_id']} v{m['version']} "
          f"(sha256 {m['sha256'][:12]}), now active")
    for k in ["rows_read", "exact_duplicates_removed", "rows_missing_state", "rows_multiple_states",
              "name_variants_merged", "rows_sector_other_or_unmapped", "rows_missing_cin"]:
        print(f"  {k:32s} {report.get(k, 0)}")
    return report


def list_versions(root: Path = DATASETS_DIR) -> None:
    ptr = datasets.active_pointer(root)
    versions = datasets.list_versions(root)
    if not versions:
        print("No dataset versions yet. Run: python scripts/ingest.py")
    for m in versions:
        mark = "* active" if ptr and ptr["version"] == m["version"] else ""
        fy = " to ".join(m["fy_range"]) if m["fy_range"] else "unknown"
        print(f"v{m['version']:<3} {m['data_source']:<6} FY {fy:<20} {m['rows_loaded']:>7} rows  "
              f"sha256 {m['sha256'][:12]}  {m['created_at']}  {', '.join(m['source_files'])}  {mark}")


def _health_check() -> None:
    from fundbridge.stores.csr_dataset import DatasetCSRStore

    DatasetCSRStore().health_check()


def switch(version: int | None, root: Path = DATASETS_DIR, health_check=None) -> None:
    """Activate a version (or roll back when version is None). Exits with a message on failure."""
    health_check = health_check or _health_check
    ptr = datasets.active_pointer(root)
    if version is not None and ptr and ptr["version"] == version:
        print(f"v{version} is already active.")
        return
    try:
        m = (datasets.activate(version, root, health_check) if version is not None
             else datasets.rollback(root, health_check))
    except (datasets.DatasetInvalid, datasets.DatasetUnavailable) as e:
        sys.exit(f"Not switched: {e}")
    print(f"Active: {m['dataset_id']} v{m['version']} ({m['data_source']}, {m['rows_loaded']} rows). "
          "The running app reads it from its next request.")


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_versions()
    elif "--rollback" in sys.argv:
        switch(None)
    elif "--activate" in sys.argv:
        try:
            switch(int(sys.argv[sys.argv.index("--activate") + 1]))
        except (IndexError, ValueError):
            sys.exit("Usage: python scripts/ingest.py --activate N")
    else:
        main(force_sample="--sample" in sys.argv)

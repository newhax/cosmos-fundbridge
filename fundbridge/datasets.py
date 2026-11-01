"""Versioned CSR datasets: every load builds a new, immutable version; one pointer names the active one.

  <root>/v<N>/csr.sqlite        store tables (clean_*, gold_*, fb_meta)
  <root>/v<N>/manifest.json     dataset_id, version, sha256 of the source files, data source, FY range, row count
  <root>/v<N>/source/*.csv      the source files exactly as loaded
  <root>/active.json            {"version": N, "previous": M}; replaced atomically (os.replace)

A version is built in a staging folder, checked (fundbridge.ingest.validate_database) and only then
renamed into place, so a failed load never leaves a half-written version. Old versions are never
deleted on activation: a decision trace records (dataset_id, version, sha256) and replays on that file.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import DATASETS_DIR
from .ingest import build_database, clean_rows, merge_reports, validate_database

DATASET_ID = "csr-filings"
DATASET_NAME = "CSR filings"
DB_FILE = "csr.sqlite"
_LOCK = threading.Lock()  # one build or activation at a time


class DatasetInvalid(ValueError):
    """The uploaded data failed the checks; nothing was written or activated."""

    def __init__(self, message: str, failures: list[dict] | None = None):
        super().__init__(message)
        self.failures = failures or []


class DatasetUnavailable(RuntimeError):
    """The dataset version a trace was computed on cannot be read."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def files_sha256(files: list[Path]) -> str:
    """sha256 over the source files (name + bytes, in name order): identifies exactly what was loaded."""
    h = hashlib.sha256()
    for f in sorted(files, key=lambda p: p.name):
        h.update(f.name.encode() + b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def version_dir(version: int, root: Path = DATASETS_DIR) -> Path:
    return Path(root) / f"v{int(version)}"


def db_path(version: int, root: Path = DATASETS_DIR) -> Path:
    return version_dir(version, root) / DB_FILE


def manifest(version: int, root: Path = DATASETS_DIR) -> dict:
    p = version_dir(version, root) / "manifest.json"
    if not p.exists():
        raise DatasetUnavailable(f"{DATASET_ID} v{version} is not on disk")
    return json.loads(p.read_text(encoding="utf-8"))


def list_versions(root: Path = DATASETS_DIR) -> list[dict]:
    root = Path(root)
    if not root.exists():
        return []
    out = []
    for d in root.glob("v*"):
        if d.is_dir() and d.name[1:].isdigit() and (d / "manifest.json").exists():
            out.append(json.loads((d / "manifest.json").read_text(encoding="utf-8")))
    return sorted(out, key=lambda m: m["version"])


def _pointer(root: Path) -> Path:
    return Path(root) / "active.json"


def active_pointer(root: Path = DATASETS_DIR) -> dict | None:
    p = _pointer(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def active(root: Path = DATASETS_DIR) -> dict | None:
    """Manifest of the active version, or None if nothing was loaded yet."""
    ptr = active_pointer(root)
    return manifest(ptr["version"], root) if ptr else None


def ref(m: dict) -> dict:
    """What a trace records about the data it read."""
    return {"dataset_id": m["dataset_id"], "version": m["version"], "sha256": m["sha256"]}


def _write_pointer(root: Path, data: dict) -> None:
    tmp = Path(root) / f".active.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _pointer(root))  # atomic on the same filesystem


def check_version(version: int, root: Path = DATASETS_DIR) -> None:
    """Open the version's database read-only and run the data checks; raises if it is unusable."""
    path = db_path(version, root)
    if not path.exists():
        raise DatasetUnavailable(f"{DATASET_ID} v{version}: {DB_FILE} missing")
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        failures = validate_database(con)
    finally:
        con.close()
    if failures:
        raise DatasetInvalid(f"{DATASET_ID} v{version} failed {len(failures)} data checks", failures)


def build_version(files: list[Path], data_source: str, root: Path = DATASETS_DIR, created_by: str = "ingest",
                  source_names: list[str] | None = None) -> dict:
    """Clean, load and check `files` into a new version (not activated). Returns its manifest.

    On any failure the staging folder is removed and no version is created.
    source_names: names to record for the files (an upload is staged under a temporary name).
    """
    if not files:
        raise DatasetInvalid("no CSV files given")
    names = source_names or [f.name for f in files]
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".staging-{uuid.uuid4().hex}"
    try:
        src = staging / "source"
        src.mkdir(parents=True)
        copies = []
        for f, name in zip(files, names):
            dst = src / Path(name).name
            shutil.copyfile(f, dst)
            copies.append(dst)

        all_rows, reports = [], []
        for f in copies:
            try:
                rows, rep = clean_rows(f)
            except ValueError as e:  # missing columns, unreadable header
                raise DatasetInvalid(f"{f.name}: {e}") from e
            all_rows.extend(rows)
            reports.append(rep)
        if not all_rows:
            raise DatasetInvalid("no usable rows: every row was dropped (check the amount and year columns)")
        for i, r in enumerate(all_rows, start=1):
            r["row_id"] = i
        report = build_database(all_rows, merge_reports(reports), staging / DB_FILE, data_source,
                                [c.name for c in copies])
        con = sqlite3.connect(staging / DB_FILE)
        try:
            failures = validate_database(con)
        finally:
            con.close()
        if failures:
            raise DatasetInvalid(f"the data failed {len(failures)} checks", failures)

        with _LOCK:
            version = max([m["version"] for m in list_versions(root)] or [0]) + 1
            m = {"dataset_id": DATASET_ID, "name": DATASET_NAME, "version": version, "sha256": files_sha256(copies),
                 "data_source": data_source, "source_files": [c.name for c in copies],
                 "rows_loaded": report.get("rows_loaded", 0), "companies": report.get("companies", 0),
                 "fy_range": report.get("fy_range", []), "created_at": _now(), "created_by": created_by,
                 "checks_passed": True}
            (staging / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
            os.replace(staging, version_dir(version, root))
        return m
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def activate(version: int, root: Path = DATASETS_DIR, health_check=None) -> dict:
    """Make `version` the active dataset. The pointer switch is atomic; if `health_check()` (run after the
    switch) raises, the previous pointer is restored and the error re-raised."""
    root = Path(root)
    with _LOCK:
        check_version(version, root)
        m = manifest(version, root)
        old = active_pointer(root)
        _write_pointer(root, {"version": version, "previous": old["version"] if old else None,
                              "activated_at": _now()})
        if health_check is not None:
            try:
                health_check()
            except Exception:
                if old is not None:
                    _write_pointer(root, old)
                else:
                    _pointer(root).unlink(missing_ok=True)
                raise
    return m


def rollback(root: Path = DATASETS_DIR, health_check=None) -> dict:
    """Re-activate the version that was active before the current one."""
    ptr = active_pointer(root)
    if not ptr or not ptr.get("previous"):
        raise DatasetUnavailable("no previous version to roll back to")
    return activate(ptr["previous"], root, health_check)

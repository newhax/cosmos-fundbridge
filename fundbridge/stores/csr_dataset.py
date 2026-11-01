"""DatasetCSRStore: the CSR store, reading the active dataset version (fundbridge/datasets.py).

Each version is its own SQLite file and is never modified after it is built, so a trace that recorded
(dataset_id, version, sha256) can be replayed on exactly the data it used, even after newer versions
were activated.
"""
from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path

from .. import datasets
from ..config import DATASETS_DIR
from ..datasets import DatasetUnavailable
from .csr_base import SQLCSRStore


CACHE_ENTRIES = 512


class DatasetCSRStore(SQLCSRStore):
    def __init__(self, root: Path = DATASETS_DIR):
        self.root = Path(root)
        if datasets.active_pointer(self.root) is None:
            raise FileNotFoundError(f"No active CSR dataset in {self.root}. Run: python scripts/ingest.py")
        self._active_cache: tuple[float, dict] | None = None
        self._manifests: dict[int, dict] = {}  # versions are immutable, so their manifests can be cached
        self._cache: OrderedDict = OrderedDict()  # hot queries per dataset version (LRU)
        self._cache_lock = threading.Lock()

    def active_manifest(self) -> dict:
        """Manifest of the active version (re-read whenever the pointer file is replaced)."""
        mtime = (self.root / "active.json").stat().st_mtime_ns
        if self._active_cache is None or self._active_cache[0] != mtime:
            self._active_cache = (mtime, datasets.active(self.root))
        return self._active_cache[1]

    def current_dataset(self) -> dict:
        return datasets.ref(self.active_manifest())

    def _query(self, sql: str, params: dict | None, dataset: dict) -> list[dict]:
        if dataset.get("dataset_id") != datasets.DATASET_ID:
            raise DatasetUnavailable(f"unknown dataset {dataset.get('dataset_id')!r}")
        v = int(dataset["version"])
        if v not in self._manifests:
            self._manifests[v] = datasets.manifest(v, self.root)  # raises DatasetUnavailable if missing
        m = self._manifests[v]
        if dataset.get("sha256") and m["sha256"] != dataset["sha256"]:
            raise DatasetUnavailable(f"{m['dataset_id']} v{m['version']} on disk has a different sha256 "
                                     f"({m['sha256'][:12]}, recorded {dataset['sha256'][:12]})")
        path = datasets.db_path(v, self.root)
        if not path.exists():
            raise DatasetUnavailable(f"{m['dataset_id']} v{v}: database file missing")
        st = path.stat()  # part of the key: a file changed on disk is never served from the cache
        key = (v, st.st_mtime_ns, st.st_size, sql, tuple(sorted((params or {}).items())))
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return [dict(r) for r in self._cache[key]]
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)  # versions are immutable
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(sql, params or {})]
        finally:
            con.close()
        with self._cache_lock:
            self._cache[key] = rows
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
        return [dict(r) for r in rows]

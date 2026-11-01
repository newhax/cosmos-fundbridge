"""App state (NGO profiles, pipeline runs, outreach drafts) in local SQLite.

This is not agent memory: it is what the UI needs to re-render after a
restart. Agent memory (events, traces) goes through MemoryStore.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import MEMORY_DIR


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AppState:
    def __init__(self, db_path: Path = MEMORY_DIR / "app.sqlite"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS ngos (ngo_id TEXT PRIMARY KEY, body TEXT, updated_at TEXT);
                CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, ngo_id TEXT, created_at TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS outreach (
                    ngo_id TEXT, company_id TEXT, updated_at TEXT, body TEXT, PRIMARY KEY (ngo_id, company_id));
                """
            )

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def save_ngo(self, ngo: dict) -> None:
        with self._con() as con:
            con.execute("INSERT OR REPLACE INTO ngos VALUES (?,?,?)", (ngo["ngo_id"], json.dumps(ngo), now_iso()))

    def get_ngo(self, ngo_id: str) -> dict | None:
        with self._con() as con:
            r = con.execute("SELECT body FROM ngos WHERE ngo_id = ?", (ngo_id,)).fetchone()
        return json.loads(r["body"]) if r else None

    def list_ngos(self) -> list[dict]:
        with self._con() as con:
            return [json.loads(r["body"]) for r in con.execute("SELECT body FROM ngos ORDER BY updated_at DESC")]

    def save_run(self, run: dict) -> None:
        with self._con() as con:
            con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?)",
                        (run["run_id"], run["ngo_id"], run["created_at"], json.dumps(run)))

    def get_run(self, run_id: str, ngo_id: str) -> dict | None:
        """A run, only if it belongs to this NGO."""
        with self._con() as con:
            r = con.execute("SELECT body FROM runs WHERE run_id = ? AND ngo_id = ?", (run_id, ngo_id)).fetchone()
        return json.loads(r["body"]) if r else None

    def latest_run(self, ngo_id: str) -> dict | None:
        with self._con() as con:
            r = con.execute("SELECT body FROM runs WHERE ngo_id = ? ORDER BY created_at DESC LIMIT 1", (ngo_id,)).fetchone()
        return json.loads(r["body"]) if r else None

    def save_outreach(self, ngo_id: str, company_id: str, body: dict) -> None:
        with self._con() as con:
            con.execute("INSERT OR REPLACE INTO outreach VALUES (?,?,?,?)", (ngo_id, company_id, now_iso(), json.dumps(body)))

    def get_outreach(self, ngo_id: str, company_id: str) -> dict | None:
        with self._con() as con:
            r = con.execute("SELECT body FROM outreach WHERE ngo_id = ? AND company_id = ?", (ngo_id, company_id)).fetchone()
        return json.loads(r["body"]) if r else None

    def list_outreach(self, ngo_id: str) -> list[dict]:
        with self._con() as con:
            return [json.loads(r["body"]) for r in con.execute(
                "SELECT body FROM outreach WHERE ngo_id = ? ORDER BY updated_at DESC", (ngo_id,))]

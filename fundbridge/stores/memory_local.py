"""LocalMemoryStore: SQLite file under data/memory/ (survives server restarts)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..config import MEMORY_DB_PATH
from ..models import DecisionTrace, FollowUpEvent
from .memory_base import MemoryStore


class LocalMemoryStore(MemoryStore):
    backend_name = "local"

    def __init__(self, db_path: Path = MEMORY_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, ngo_id TEXT, company_id TEXT, type TEXT, at TEXT, body TEXT);
                CREATE INDEX IF NOT EXISTS ix_events_key ON events(ngo_id, company_id);
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY, run_id TEXT, ngo_id TEXT, created_at TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS thread_messages (
                    scope TEXT, key TEXT, title TEXT, at TEXT, body TEXT);
                CREATE INDEX IF NOT EXISTS ix_thread ON thread_messages(scope, key, at);
                CREATE TABLE IF NOT EXISTS meko_conversations (scope TEXT, key TEXT, conversation_id TEXT,
                    PRIMARY KEY (scope, key));
                """
            )

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def remember(self, event: FollowUpEvent) -> None:
        with self._con() as con:
            con.execute(
                "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)",
                (event.event_id, event.ngo_id, event.company_id, event.type, event.at, event.model_dump_json()),
            )

    def recall(self, ngo_id: str, company_id: str | None = None) -> list[FollowUpEvent]:
        sql, params = "SELECT body FROM events WHERE ngo_id = ?", [ngo_id]
        if company_id:
            sql += " AND company_id = ?"
            params.append(company_id)
        with self._con() as con:
            rows = con.execute(sql + " ORDER BY at, event_id", params).fetchall()
        return [FollowUpEvent.model_validate_json(r["body"]) for r in rows]

    def search(self, query: str, ngo_id: str | None = None, limit: int = 10) -> list[dict]:
        like = f"%{query}%"
        out = []
        with self._con() as con:
            q = "SELECT body FROM events WHERE body LIKE ?" + (" AND ngo_id = ?" if ngo_id else "") + " ORDER BY at DESC LIMIT ?"
            for r in con.execute(q, [like, ngo_id, limit] if ngo_id else [like, limit]):
                out.append({"kind": "event", **json.loads(r["body"])})
            q = "SELECT trace_id, run_id, ngo_id, created_at FROM traces WHERE body LIKE ?" + (" AND ngo_id = ?" if ngo_id else "") + " ORDER BY created_at DESC LIMIT ?"
            for r in con.execute(q, [like, ngo_id, limit] if ngo_id else [like, limit]):
                out.append({"kind": "trace", **dict(r)})
        return out[:limit]

    def log_trace(self, trace: DecisionTrace) -> None:
        with self._con() as con:
            con.execute(
                "INSERT OR REPLACE INTO traces VALUES (?,?,?,?,?)",
                (trace.trace_id, trace.run_id, trace.ngo_id, trace.created_at, trace.model_dump_json()),
            )

    def log_thread(self, scope: str, key: str, title: str, message: dict) -> None:
        with self._con() as con:
            con.execute("INSERT INTO thread_messages VALUES (?,?,?,?,?)",
                        (scope, key, title, message.get("at", ""), json.dumps(message)))

    def read_thread(self, scope: str, key: str) -> list[dict]:
        with self._con() as con:
            rows = con.execute("SELECT body FROM thread_messages WHERE scope = ? AND key = ? ORDER BY at, rowid",
                               (scope, key)).fetchall()
        return [json.loads(r["body"]) for r in rows]

    def conversation_id(self, scope: str, key: str) -> str | None:
        with self._con() as con:
            r = con.execute("SELECT conversation_id FROM meko_conversations WHERE scope = ? AND key = ?", (scope, key)).fetchone()
        return r["conversation_id"] if r else None

    def save_conversation_id(self, scope: str, key: str, conversation_id: str) -> None:
        with self._con() as con:
            con.execute("INSERT OR REPLACE INTO meko_conversations VALUES (?,?,?)", (scope, key, conversation_id))

    def get_trace(self, trace_id: str) -> DecisionTrace | None:
        with self._con() as con:
            r = con.execute("SELECT body FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
        return DecisionTrace.model_validate_json(r["body"]) if r else None

"""Scoped admins for the regional-operations jobs: location and sector review queues, the sector map,
state CSR portal links, feedback, and the coverage page.

Admins are created only with scripts/admin.py (there is no admin signup). Each admin has zero or more scope
rows: a state LGD code and/or a language. No rows = all states and languages. A state-scoped admin sees
only queue items for its states (items with no state are national and visible to unscoped admins only).
Admins do NOT approve companies or verify NGOs: those steps were removed from the product.
"""
from __future__ import annotations

from fastapi import HTTPException

from ..geo import code
from .db import now_iso


class AdminService:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS admin_scopes (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        state_lgd TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (user_id, state_lgd, language));
    CREATE TABLE IF NOT EXISTS admin_log (
        at TEXT NOT NULL, user_id TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
    """

    def __init__(self, platform):
        self.p, self.db = platform, platform.db

    def create(self, email: str, password: str, states: list[str] = (), languages: list[str] = (), is_demo: bool = False) -> dict:
        user = self.p.auth.create_user(email, password, "admin", verified=True, is_demo=is_demo, internal=True)
        self.set_scope(user["id"], states, languages)
        return user

    def set_scope(self, user_id: str, states, languages) -> None:
        for s in states or []:
            if not self.p.geo.state(s):
                raise ValueError(f"Unknown state LGD code {s!r}")
        with self.db.tx() as con:
            con.execute("DELETE FROM admin_scopes WHERE user_id = ?", (user_id,))
            rows = [(user_id, code(s), "") for s in states or []] + [(user_id, "", lang) for lang in languages or []]
            con.executemany("INSERT OR IGNORE INTO admin_scopes VALUES (?,?,?)", rows)

    def scope(self, user: dict) -> dict:
        rows = self.db.all("SELECT state_lgd, language FROM admin_scopes WHERE user_id = ?", (user["id"],))
        states = sorted({r["state_lgd"] for r in rows if r["state_lgd"]})
        langs = sorted({r["language"] for r in rows if r["language"]})
        return {"states": states or None, "languages": langs or None,
                "state_names": [self.p.geo.state(s)["name"] for s in states if self.p.geo.state(s)]}

    def allows_state(self, user: dict, state_lgd: str | None) -> bool:
        sc = self.scope(user)["states"]
        return sc is None or (bool(state_lgd) and code(state_lgd) in sc)

    def allows_language(self, user: dict, lang: str | None) -> bool:
        sc = self.scope(user)["languages"]
        return sc is None or (lang or "en") in sc

    def require_state(self, user: dict, state_lgd: str | None) -> None:
        if not self.allows_state(user, state_lgd):
            raise HTTPException(403, "This item is outside the states you administer.")

    def log(self, user: dict, action: str, detail: str = "") -> None:
        self.db.run("INSERT INTO admin_log VALUES (?,?,?,?)", (now_iso(), user["id"], action, detail[:500]))

    # -- queues ---------------------------------------------------------------------------------------------
    def unmapped_locations(self, user: dict, status: str = "open", state: str = "") -> list[dict]:
        sc = self.scope(user)["states"]
        wanted = [code(state)] if state else sc
        if sc is not None and state and code(state) not in sc:
            raise HTTPException(403, "This state is outside your scope.")
        items = self.p.geo.unmapped(status, wanted)
        for it in items:
            s = self.p.geo.state(it["state_lgd_code"]) if it["state_lgd_code"] else None
            it["state_name"] = s["name"] if s else ("national" if not it["state_lgd_code"] else "unknown")
        return items

    def resolve_location(self, user: dict, item_id: int, target_code: str | None) -> dict:
        it = next((x for x in self.p.geo.unmapped("open") if x["id"] == item_id), None)
        if not it:
            raise HTTPException(404, "Queue item not found or already handled.")
        self.require_state(user, it["state_lgd_code"] or None)
        try:
            out = self.p.geo.resolve_unmapped(item_id, target_code, user["email"])
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        self.log(user, "geo.resolve", f"{it['raw']} -> {target_code or 'ignored'}")
        return out

    def feedback(self, user: dict, status: str = "", state: str = "", language: str = "") -> list[dict]:
        sc = self.scope(user)
        q, args = "SELECT * FROM feedback WHERE 1 = 1", []
        if status:
            q += " AND status = ?"
            args.append(status)
        if state:
            self.require_state(user, state)
            q += " AND state_lgd = ?"
            args.append(code(state))
        elif sc["states"] is not None:
            q += f" AND state_lgd IN ({','.join('?' * len(sc['states']))})"
            args += sc["states"]
        if language:
            if not self.allows_language(user, language):
                raise HTTPException(403, "This language is outside your scope.")
            q += " AND language = ?"
            args.append(language)
        elif sc["languages"] is not None:
            q += f" AND language IN ({','.join('?' * len(sc['languages']))})"
            args += sc["languages"]
        return self.db.all(q + " ORDER BY created_at DESC LIMIT 200", args)

    def set_feedback_status(self, user: dict, fid: str, status: str) -> None:
        f = self.db.one("SELECT * FROM feedback WHERE id = ?", (fid,))
        if not f:
            raise HTTPException(404, "Feedback not found.")
        self.require_state(user, f["state_lgd"] or None) if self.scope(user)["states"] is not None else None
        if status not in ("new", "read", "done"):
            raise HTTPException(422, "Status must be new, read or done.")
        self.db.run("UPDATE feedback SET status = ? WHERE id = ?", (status, fid))

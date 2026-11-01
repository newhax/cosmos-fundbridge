"""MekoMemoryStore: shared persistent agent memory via the Meko MCP server.

Uses Meko's documented MCP tools (https://docs.mekodata.ai/reference/mcp-server/):
  memory_add      store each follow-up event / trace summary (metadata carries the full record)
  memory_search   recall events for an NGO + company
  context_search  free-text search across memory, knowledge base and conversations
  artifact_put    store each full decision trace as a JSON artifact
Auth: "Authorization: Bearer mko_tkn_..." plus a User-Agent header (both required by Meko).

Every write is mirrored to a LocalMemoryStore. recall() merges Meko results with
the mirror by event_id, so the follow-up timeline stays exact even if Meko's
search ranking omits an item, and the UI keeps working if Meko is briefly down.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import base64
import json
import logging
from typing import Any

from ..config import Settings
from ..models import DecisionTrace, FollowUpEvent
from .memory_base import MemoryStore
from .memory_local import LocalMemoryStore

log = logging.getLogger("fundbridge.meko")
AGENT_ID = "fundbridge"
REQUIRED_TOOLS = {"memory_add", "memory_search", "context_search", "artifact_put", "conversation_create",
                  "conversation_add_message"}


class MekoError(RuntimeError):
    pass


def _parse_result(result: Any) -> Any:
    if getattr(result, "isError", False):
        texts = [getattr(c, "text", "") for c in getattr(result, "content", [])]
        raise MekoError(" ".join(texts) or "tool error")
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    texts = [getattr(c, "text", "") for c in getattr(result, "content", []) if getattr(c, "text", None)]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, TypeError):
        return joined


def _provenance_meta(prov: dict) -> dict:
    """Compact data provenance for Meko metadata: dataset_id, version, sha256, each query's SQL and row count."""
    if not prov:
        return {}
    return {"dataset_id": prov.get("dataset_id"), "version": prov.get("version"), "sha256": prov.get("sha256"),
            "queries": [{"sql": q["sql"][:2000], "tables": q["tables"], "row_count": q["row_count"],
                         "result_hash": q["result_hash"]} for q in prov.get("queries", [])],
            "data_quality_caveats": prov.get("data_quality_caveats", [])}


def _iter_dicts(obj: Any):
    """Yield every dict nested anywhere in a tool result (result shapes vary by tool)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_dicts(v)


class MekoMemoryStore(MemoryStore):
    backend_name = "meko"

    def __init__(self, url: str, api_key: str, datapack_id: str = "", mirror: LocalMemoryStore | None = None):
        self.url = url
        self.api_key = api_key
        self.datapack_id = datapack_id
        self.mirror = mirror or LocalMemoryStore()
        self.last_error: str | None = None

    @classmethod
    def from_settings(cls, s: Settings) -> "MekoMemoryStore":
        if not s.meko_api_key:
            raise RuntimeError("MEKO_API_KEY not set")
        return cls(s.meko_url, s.meko_api_key, s.meko_datapack_id)

    # -- MCP plumbing ------------------------------------------------------------
    async def _acall(self, calls: list[tuple[str, dict]] | None, list_tools: bool = False) -> list[Any]:
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {self.api_key}", "User-Agent": "fundbridge/0.1 (mcp-client)"}
        async with httpx2.AsyncClient(headers=headers, timeout=30.0) as http:
            async with streamable_http_client(self.url, http_client=http) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    if list_tools:
                        tools = await session.list_tools()
                        return [t.name for t in tools.tools]
                    out = []
                    for name, args in calls or []:
                        if self.datapack_id:
                            args = {**args, "datapack_id": self.datapack_id}
                        out.append(_parse_result(await session.call_tool(name, args)))
                    return out

    def _run(self, calls=None, list_tools=False):
        # FastAPI sync endpoints run in worker threads without an event loop, so asyncio.run is safe there. App
        # startup (the health check) runs inside the server's event loop, so the call moves to a helper thread.
        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(self._acall(calls, list_tools))
            with ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, self._acall(calls, list_tools)).result()
        except BaseExceptionGroup as eg:  # anyio task groups wrap transport errors; surface the real cause
            leaf = eg
            while isinstance(leaf, BaseExceptionGroup):
                leaf = leaf.exceptions[0]
            raise MekoError(f"{type(leaf).__name__}: {leaf}") from leaf

    def _call(self, name: str, args: dict) -> Any:
        return self._run([(name, args)])[0]

    def health_check(self) -> None:
        names = set(self._run(list_tools=True))
        missing = REQUIRED_TOOLS - names
        if missing:
            raise MekoError(f"Meko server is missing tools {sorted(missing)}")

    # -- MemoryStore ---------------------------------------------------------------
    def remember(self, event: FollowUpEvent) -> None:
        self.mirror.remember(event)
        text = (f"[fundbridge-event] ngo={event.ngo_id} company={event.company_id} ({event.company_name}) "
                f"type={event.type} at={event.at}: {event.text[:500]}")
        if event.type == "promise_made":
            text += f" | promise: {event.promise_what} by {event.promise_by or 'unknown'}"
        try:
            self._call("memory_add", {
                "text": text, "agent_id": AGENT_ID, "run_id": f"{event.ngo_id}:{event.company_id}",
                "metadata": {"app": "fundbridge", "kind": "followup_event", "ngo_id": event.ngo_id,
                             "company_id": event.company_id, "event": event.model_dump()},
            })
        except Exception as e:  # noqa: BLE001 - keep the app usable; surfaced via last_error
            self.last_error = f"memory_add failed: {e}"
            log.warning(self.last_error)

    def recall(self, ngo_id: str, company_id: str | None = None) -> list[FollowUpEvent]:
        merged: dict[str, FollowUpEvent] = {e.event_id: e for e in self.mirror.recall(ngo_id, company_id)}
        args = {"query": f"fundbridge-event ngo={ngo_id}" + (f" company={company_id}" if company_id else ""),
                "agent_id": AGENT_ID, "limit": 100}
        if company_id:
            args["run_id"] = f"{ngo_id}:{company_id}"
        try:
            for d in _iter_dicts(self._call("memory_search", args)):
                ev = (d.get("metadata") or {}).get("event") if isinstance(d.get("metadata"), dict) else None
                if not ev or ev.get("ngo_id") != ngo_id or (company_id and ev.get("company_id") != company_id):
                    continue
                e = FollowUpEvent(**ev)
                merged.setdefault(e.event_id, e)
                self.mirror.remember(e)  # pull events written by other agents/sessions into the mirror
            self.last_error = None
        except Exception as e:  # noqa: BLE001
            self.last_error = f"memory_search failed: {e}"
            log.warning(self.last_error)
        return sorted(merged.values(), key=lambda e: (e.at, e.event_id))

    def search(self, query: str, ngo_id: str | None = None, limit: int = 10) -> list[dict]:
        try:
            res = self._call("context_search", {"query": query, "agent_id": AGENT_ID, "limit": limit})
            return [{"kind": "meko", "result": res}]
        except Exception as e:  # noqa: BLE001
            self.last_error = f"context_search failed: {e}"
            return self.mirror.search(query, ngo_id, limit)

    def log_trace(self, trace: DecisionTrace) -> None:
        self.mirror.log_trace(trace)
        body = trace.model_dump_json().encode()
        top = ", ".join(f"#{r['rank']} {r['company']} ({r['score']})" for r in trace.ranking[:3])
        try:
            art = self._call("artifact_put", {
                "filename": f"{trace.trace_id}.json", "content_base64": base64.b64encode(body).decode(),
                "content_type": "application/json", "agent_id": AGENT_ID,
            })
            content_hash = next((d.get("content_hash") for d in _iter_dicts(art) if d.get("content_hash")), None)
            self._call("memory_add", {
                "text": f"[fundbridge-trace] Researcher decision trace {trace.trace_id} for NGO {trace.ngo_id}: {top}. "
                        f"Weights {trace.weights}. Full trace artifact {content_hash}.",
                "agent_id": AGENT_ID, "run_id": trace.run_id,
                "metadata": {"app": "fundbridge", "kind": "decision_trace", "trace_id": trace.trace_id,
                             "ngo_id": trace.ngo_id, "artifact_content_hash": content_hash,
                             "llm_calls": [{k: c.get(k) for k in ("role", "provider", "model", "latency_ms",
                                            "input_tokens", "output_tokens", "cached", "fallback_from", "outcome")}
                                           for c in trace.llm_calls],
                             "data": _provenance_meta(trace.data_provenance)},
            })
        except Exception as e:  # noqa: BLE001
            self.last_error = f"trace logging failed: {e}"
            log.warning(self.last_error)

    def get_trace(self, trace_id: str) -> DecisionTrace | None:
        return self.mirror.get_trace(trace_id)

    # -- conversations: one per pitch thread, plus private per-NGO / per-company -------------
    @staticmethod
    def _agent_for(scope: str, key: str) -> str:
        # Separate agent namespaces keep each party's private conversations apart in Meko.
        return {"pitch": f"{AGENT_ID}-thread", "ngo": f"{AGENT_ID}-ngo-{key}", "company": f"{AGENT_ID}-company-{key}",
                "donor": f"{AGENT_ID}-donor-{key}"}[scope]

    def _conversation_id(self, scope: str, key: str, title: str) -> str | None:
        cached = self.mirror.conversation_id(scope, key)
        if cached:
            return cached
        res = self._call("conversation_create", {
            "agent_id": self._agent_for(scope, key), "session_id": f"{scope}:{key}", "title": title,
            "metadata": {"app": "fundbridge", "scope": scope, "key": key},
        })
        cid = next((d.get("conversation_id") or d.get("id") for d in _iter_dicts(res)
                    if d.get("conversation_id") or d.get("id")), None)
        if cid:
            self.mirror.save_conversation_id(scope, key, str(cid))
        return str(cid) if cid else None

    def log_thread(self, scope: str, key: str, title: str, message: dict) -> None:
        self.mirror.log_thread(scope, key, title, message)
        try:
            cid = self._conversation_id(scope, key, title)
            if not cid:
                raise MekoError("conversation_create returned no id")
            self._call("conversation_add_message", {
                "conversation_id": cid, "agent_id": self._agent_for(scope, key),
                "input": f"[{message.get('type')}] by {message.get('actor_role')}: {message.get('summary', '')}"[:2000],
                "output": "recorded",
                "metadata": {"app": "fundbridge", "scope": scope, "key": key, "event": message},
            })
        except Exception as e:  # noqa: BLE001 - SQLite event log stays the source of truth
            self.last_error = f"conversation write failed: {e}"
            log.warning(self.last_error)

    def read_thread(self, scope: str, key: str) -> list[dict]:
        return self.mirror.read_thread(scope, key)

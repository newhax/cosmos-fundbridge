"""Store wiring, the status header data and Meko adapter logic, testable without real credentials."""
from types import SimpleNamespace

import pytest

from fundbridge.agents.followup import new_event
from fundbridge.config import DATASETS_DIR, Settings
from fundbridge.models import DecisionTrace
from fundbridge.stores import build_backends
from fundbridge.stores.memory_local import LocalMemoryStore
from fundbridge.stores.memory_meko import MekoMemoryStore, _parse_result

pytestmark = pytest.mark.skipif(not (DATASETS_DIR / "active.json").exists(), reason="run scripts/ingest.py first")


def test_meko_fallback_warning_and_dataset_status(monkeypatch):
    import fundbridge.stores as stores

    s = Settings(gemini_api_key="", anthropic_api_key="", memory_backend="meko", meko_api_key="")
    monkeypatch.setattr(stores, "settings", s)
    st = build_backends().status()
    assert st["memory_backend"] == "local" and st["requested_memory_backend"] == "meko"
    joined = " ".join(st["warnings"])
    assert "Meko unavailable" in joined and "MEKO_API_KEY" in joined
    d = st["dataset"]  # what the UI header shows: name vN, REAL/SAMPLE, FY range, row count
    assert d["name"] and d["version"] >= 1 and d["data_source"] == "SAMPLE"
    assert len(d["fy_range"]) == 2 and d["rows_loaded"] > 0 and len(d["sha256"]) == 64
    assert set(st) == {"memory_backend", "requested_memory_backend", "data_source", "dataset", "warnings"}


def test_parse_result_variants():
    text = SimpleNamespace(text='{"results": [{"id": 1}]}')
    assert _parse_result(SimpleNamespace(isError=False, structuredContent=None, content=[text])) == {"results": [{"id": 1}]}
    assert _parse_result(SimpleNamespace(isError=False, structuredContent={"a": 1}, content=[])) == {"a": 1}
    with pytest.raises(RuntimeError):
        _parse_result(SimpleNamespace(isError=True, content=[SimpleNamespace(text="denied")]))


class FakeMeko(MekoMemoryStore):
    """Meko adapter with the network replaced by an in-memory fake of memory_add / memory_search."""

    def __init__(self, tmp_path):
        super().__init__("https://example.invalid/mcp", "mko_tkn_fake", mirror=LocalMemoryStore(tmp_path / "mirror.sqlite"))
        self.memories, self.calls = [], []

    def _call(self, name, args):
        self.calls.append(name)
        if name == "memory_add":
            self.memories.append({"id": len(self.memories), "memory": args["text"], "metadata": args["metadata"]})
            return {"id": len(self.memories)}
        if name == "memory_search":
            return {"results": [m for m in self.memories if m["metadata"].get("kind") == "followup_event"]}
        if name == "artifact_put":
            return {"content_hash": "abc123"}
        raise AssertionError(name)


def test_meko_remember_recall_and_cross_session(tmp_path):
    from datetime import datetime, timezone

    m = FakeMeko(tmp_path)
    e1 = new_event("n1", "C1", "sent", datetime(2026, 10, 1, tzinfo=timezone.utc), company_name="Co")
    m.remember(e1)
    assert m.calls == ["memory_add"]
    assert m.memories[0]["metadata"]["event"]["event_id"] == e1.event_id

    # An event written by another agent/session exists only in Meko, not in this mirror.
    e2 = new_event("n1", "C1", "reply_received", datetime(2026, 10, 5, tzinfo=timezone.utc), text="Thanks")
    m.memories.append({"id": 99, "memory": "x", "metadata": {"kind": "followup_event", "event": e2.model_dump()}})
    got = m.recall("n1", "C1")
    assert [e.event_id for e in got] == [e1.event_id, e2.event_id]
    assert m.mirror.recall("n1", "C1")[-1].event_id == e2.event_id  # pulled into the mirror


def test_meko_trace_goes_to_artifact_and_memory(tmp_path):
    m = FakeMeko(tmp_path)
    t = DecisionTrace(trace_id="trace_x", run_id="run_x", ngo_id="n1", created_at="2026-10-31", inputs={}, query={},
                      weights={}, ranking=[{"rank": 1, "company": "Co", "score": 80.0}], pairwise=[], data_quality_caveats=[])
    m.log_trace(t)
    assert m.calls == ["artifact_put", "memory_add"]
    assert m.memories[0]["metadata"]["artifact_content_hash"] == "abc123"
    assert m.get_trace("trace_x") is not None


def test_meko_calls_work_from_inside_a_running_event_loop(monkeypatch):
    """App startup runs the Meko health check inside the server's event loop; asyncio.run must not be used there."""
    import asyncio

    store = MekoMemoryStore.__new__(MekoMemoryStore)

    async def fake_acall(calls=None, list_tools=False):
        return ["ok"]

    monkeypatch.setattr(store, "_acall", fake_acall)

    async def inside_loop():
        return store._run([("x", {})])

    assert asyncio.run(inside_loop()) == ["ok"]
    assert store._run([("x", {})]) == ["ok"]  # and still fine with no loop running

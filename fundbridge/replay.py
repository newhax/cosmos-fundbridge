"""replay(): re-run a stored ranking on the exact data it was computed on.

1. Every recorded CSR query is re-executed on the recorded dataset version (dataset_id, version, sha256;
   each version keeps its own file) and its result hash is compared.
2. The Prospector + Researcher scoring is re-run on that version with no model calls and no memory
   writes (scores are deterministic), and the ranking is compared with the stored one.

Result status: "reproduced" | "differs" | "unavailable" (with a reason).
"""
from __future__ import annotations

from .agents.context import Context
from .agents.prospector import Prospector
from .agents.researcher import Researcher
from .datasets import DatasetUnavailable
from .models import NGOProfile
from .stores.csr_base import rows_hash


class _NoLLM:
    """Scores never use a model; explanations fall back to templates during a replay."""
    enabled = False

    def call(self, *a, **k):
        return None

    def call_log(self) -> list:
        return []


class _NoMemory:
    def log_trace(self, trace) -> None:  # a replay must not write a second trace
        return None


def _ranking(items) -> list[tuple[str, float]]:
    return [(r["company_id"], round(float(r["score"]["total"]), 1)) for r in items]


def replay(run: dict, csr) -> dict:
    trace = run.get("trace") or {}
    prov = trace.get("data_provenance") or {}
    dataset = {k: prov.get(k) for k in ("dataset_id", "version", "sha256")}
    base = {"run_id": run.get("run_id"), **dataset}
    if not prov.get("queries") or dataset["version"] is None:
        return {**base, "status": "unavailable",
                "reason": "This ranking was made before dataset versions were recorded. Run the funder search again."}

    checks = []
    try:
        for i, q in enumerate(prov["queries"], start=1):
            rows = csr.replay_query(q, dataset)
            checks.append({"query": i, "tables": q["tables"], "recorded_rows": q["row_count"], "rows": len(rows),
                           "match": rows_hash(rows) == q["result_hash"]})
        ngo = NGOProfile(**trace["inputs"]["ngo"])
        ctx = Context(ngo=ngo, csr=csr, memory=_NoMemory(), llm=_NoLLM(), run_id=f"{run['run_id']}_replay",
                      lookback_years=trace["inputs"].get("lookback_years", 5))
        with csr.pinned(dataset):
            pr = Prospector().run(ctx, run.get("sectors"))
            ranked, _ = Researcher().run(ctx, pr)
    except DatasetUnavailable as e:
        return {**base, "status": "unavailable", "reason": str(e), "checks": checks}

    stored, now = _ranking(run.get("ranked", [])), _ranking([r.model_dump() for r in ranked])
    moved = [{"rank": i + 1, "stored": s, "replayed": n} for i, (s, n) in enumerate(zip(stored, now)) if s != n]
    same = all(c["match"] for c in checks) and stored == now
    return {**base, "status": "reproduced" if same else "differs",
            "queries_checked": len(checks), "queries_matching": sum(c["match"] for c in checks),
            "ranking_matches": stored == now, "ranking_differences": moved[:10],
            "stored_count": len(stored), "replayed_count": len(now), "checks": checks}

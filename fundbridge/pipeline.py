"""Prospector -> Researcher pipeline, saved as a run the UI can reload.

With background=True the ranking is returned as soon as it is scored (sector mapping + SQL + scoring,
about 2 s) and the model explanations for the top companies are written and fact-checked on a worker
thread, which then updates the same run. Scores never depend on the model, so the ranking does not change.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import nullcontext

from .agents.context import Context
from .agents.prospector import Prospector, ProspectorResult
from .agents.researcher import Researcher
from .appstate import AppState
from .config import settings
from .llm import Router
from .models import NGOProfile
from .stores import Backends

log = logging.getLogger("fundbridge.pipeline")


def _place(ngo: NGOProfile) -> tuple[str | None, str | None, int | None]:
    """The NGO's state and district as LGD codes (None when unknown or geography is not loaded)."""
    from .geo import GEO_DB, get_geo

    if not GEO_DB.exists():
        return None, None, None
    g = get_geo()
    s = g.resolve_state(ngo.state or "")
    if s.level != "state":
        return None, None, g.version
    d = g.resolve_district(ngo.district or "", s.code) if ngo.district else None
    return s.code, (d.code if d and d.level == "district" else None), g.version


def coverage_and_fallback(csr, ngo: NGOProfile, sectors: list[str], lookback_years: int) -> dict:
    """Coverage of the NGO's area in the filings and, when THIN or NONE, separately labelled fallback evidence:
    funders in states with a similar CSR sector mix, and pan-India funders. Never mixed into the local ranking."""
    from .config import FALLBACK_SIMILAR_STATES, FALLBACK_WEIGHTS
    from .geo import get_geo

    state_lgd, district_lgd, gv = _place(ngo)
    out = {"geo_version": gv, "state_lgd": state_lgd, "district_lgd": district_lgd}
    if not state_lgd:
        out["coverage"] = {"level": "NONE", "warning": "Location unknown: coverage cannot be checked.", "sectors": sectors}
        return out
    try:
        cov = csr.coverage(state_lgd, sectors, district_lgd)
    except Exception as e:  # noqa: BLE001 - e.g. a dataset version built before coverage tables existed
        log.warning("coverage unavailable: %s", e)
        out["coverage"] = {"level": "UNKNOWN", "sectors": sectors,
                           "warning": "Coverage is unknown for this dataset version (rebuild it with scripts/ingest.py)."}
        return out
    out["coverage"] = cov
    if cov["level"] == "GOOD":
        return out
    min_fy = csr.latest_fy_start() - lookback_years + 1
    g = get_geo()

    def scored(items, weight, reason):
        top = max((float(i["spend_inr"] or 0) for i in items), default=0) or 1.0
        return [{**i, "weight": weight, "reason": reason,
                 "fallback_score": round(100 * weight * float(i["spend_inr"] or 0) / top, 1)} for i in items]

    sim = csr.similar_states(state_lgd, sectors, FALLBACK_SIMILAR_STATES)
    names = [g.state(x["state_lgd"])["name"] for x in sim if g.state(x["state_lgd"])]
    out["fallback"] = {
        "similar_states": {
            "label": "Nearby evidence: not local matches",
            "states": [{**x, "name": g.state(x["state_lgd"])["name"]} for x in sim if g.state(x["state_lgd"])],
            "basis": ("States with GOOD coverage whose reported CSR sector mix is most similar to this state's" if sim else
                      "None: this state has no reported filings to compare with other states"),
            "funders": scored(csr.funders_in(sectors, min_fy, [x["state_lgd"] for x in sim]) if sim else [],
                              FALLBACK_WEIGHTS["similar_states"],
                              f"Active in {', '.join(sectors)} in {', '.join(names)} (similar states)") if sim else [],
        },
        "pan_india": {
            "label": "Pan-India funders: not local matches",
            "funders": scored(csr.funders_in(sectors, min_fy, all_india=True), FALLBACK_WEIGHTS["pan_india"],
                              f"Filed {', '.join(sectors)} spend as All India"),
        },
    }
    return out


def run_pipeline(ngo: NGOProfile, backends: Backends, router: Router, appstate: AppState,
                 sectors_override: list[str] | None = None, background: bool = False, user_id: str | None = None) -> dict:
    t0 = time.perf_counter()
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    ctx = Context(ngo=ngo, csr=backends.csr, memory=backends.memory, llm=router.session(run_id, user_id), run_id=run_id,
                  lookback_years=settings.lookback_years)
    deferred = background and ctx.llm.enabled
    # Every CSR query of this run is recorded (SQL, tables, row counts, dataset version) into the decision trace.
    with backends.csr.provenance():
        pr = Prospector().run(ctx, sectors_override)
        ranked, trace = Researcher().run(ctx, pr, explain=not deferred, log=not deferred)
        area = coverage_and_fallback(backends.csr, ngo, pr.sectors, ctx.lookback_years)
    run = {
        "run_id": ctx.run_id,
        "ngo_id": ngo.ngo_id,
        "created_at": ctx.now.isoformat(timespec="seconds"),
        "sectors": pr.sectors,
        "sector_rationale": pr.sector_rationale,
        "sector_mode": pr.sector_mode,
        "ranked": _with_relative([r.model_dump() for r in ranked]),
        "coverage": area["coverage"],
        "fallback": area.get("fallback"),
        "geo_version": area["geo_version"],
        "capacity_reached": ctx.llm.capacity_reached,
        "trace": trace.model_dump(),
        "duration_s": round(time.perf_counter() - t0, 2),
        "backends": backends.status(),
        "llm_calls": ctx.llm.call_log(),
        "explanations": "pending" if deferred else "done",
    }
    appstate.save_ngo(ngo.model_dump())
    appstate.save_run(run)
    if deferred:
        threading.Thread(target=_explain_later, args=(dict(run), pr, ctx, backends, appstate, t0),
                         name=f"explain-{run_id}", daemon=True).start()
    return run


def _with_relative(items: list[dict]) -> list[dict]:
    """Score relative to the best candidate in THIS NGO's own set (0-100), shown beside the raw score, so a
    state with more filings does not look better just because it has more data."""
    top = max((float(r["score"]["total"]) for r in items), default=0) or 1.0
    for r in items:
        r["score"]["relative"] = round(100 * float(r["score"]["total"]) / top, 1)
    return items


def _explain_later(run: dict, pr: ProspectorResult, ctx: Context, backends: Backends, appstate: AppState,
                   t0: float) -> None:
    """Write + check the explanations, then replace the run's ranking/trace (same order, same scores)."""
    try:
        prov = run["trace"].get("data_provenance") or {}
        dataset = {k: prov.get(k) for k in ("dataset_id", "version", "sha256")}
        with backends.csr.pinned(dataset) if dataset["version"] is not None else nullcontext():  # same data
            ranked, trace = Researcher().run(ctx, pr, explain=True, log=False)
        trace.trace_id = run["trace"]["trace_id"]
        trace.data_provenance = run["trace"]["data_provenance"]  # the fast pass recorded every data query
        backends.memory.log_trace(trace)
        run.update(ranked=_with_relative([r.model_dump() for r in ranked]), trace=trace.model_dump(), llm_calls=ctx.llm.call_log(),
                   explanations="done", explained_after_s=round(time.perf_counter() - t0, 2))
    except Exception as e:  # noqa: BLE001 - the ranking stays usable with template explanations
        log.warning("background explanations failed for %s: %s", run["run_id"], e)
        run.update(explanations="failed", explanations_error=str(e)[:300])
    appstate.save_run(run)

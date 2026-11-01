"""SCREENER (company side, fast model): summarise a pitch and score fit against the
company's OWN criteria.

The fit score is deterministic and uses only structured facts (sectors, state, ask amount)
compared with the company's focus_sectors / focus_states / ticket range. The pitch's
wording, length, grammar or polish never enter the score; the model only writes the
3-line summary, which is fact-checked against the pitch text.
"""
from __future__ import annotations

import math
import re

from pydantic import BaseModel, Field

from ..formatting import inr
from ..verify import verify_claims

FIT_WEIGHTS = {"sector_fit": 0.45, "state_fit": 0.35, "ticket_fit": 0.20}
NEUTRAL = 0.5  # used when the company has not set a criterion (shown as "unknown", not a penalty)

DOCS = [("crn", "CSR-1 registration number"), ("darpan_id", "NGO Darpan ID"),
        ("has_12a", "12A registration"), ("has_80g", "80G registration")]

SYSTEM = """You summarise a funding pitch from an NGO for a company's CSR team.
Write exactly 3 short, neutral lines: (1) who is asking and where, (2) what the money would do,
(3) the amount asked and any timeline stated. Use only facts in the pitch and NGO profile; copy numbers
exactly. Do NOT judge writing quality, grammar, length or tone, and do not recommend a decision."""


class ScreenSummary(BaseModel):
    lines: list[str] = Field(min_length=1, max_length=3)


def ticket_component(ask: float | None, lo: float | None, hi: float | None) -> tuple[float, str | None]:
    if ask is None or ask <= 0:
        return NEUTRAL, "Ask amount: unknown"
    if lo is None and hi is None:
        return NEUTRAL, "Company ticket range: not set (unknown)"
    lo_v, hi_v = lo or 0.0, hi or math.inf
    if lo_v <= ask <= hi_v:
        return 1.0, None
    bound = lo_v if ask < lo_v else hi_v
    return max(0.0, 1.0 - abs(math.log10(ask / bound)) / 2.0), None


def fit_score(facts: dict, company: dict) -> dict:
    """facts: {sectors, state, ask_inr}. company: {focus_sectors, focus_states, min_ticket, max_ticket}."""
    notes, unknowns = [], []
    sectors = [s.lower() for s in facts.get("sectors") or []]
    focus_sec = [s.lower() for s in company.get("focus_sectors") or []]
    if not focus_sec:
        sector = NEUTRAL
        unknowns.append("Company focus sectors: not set (unknown)")
    elif not sectors:
        sector = NEUTRAL
        unknowns.append("Pitch sectors: unknown")
    else:
        hit = sorted(set(sectors) & set(focus_sec))
        sector = len(hit) / len(set(sectors))
        notes.append(f"Sectors in your focus: {', '.join(hit) or 'none'} (pitch: {', '.join(sectors)})")
    focus_st = company.get("focus_states") or []
    if not focus_st:
        state = NEUTRAL
        unknowns.append("Company focus states: not set (unknown)")
    elif not facts.get("state"):
        state = NEUTRAL
        unknowns.append("Pitch state: unknown")
    else:
        state = 1.0 if facts["state"] in focus_st else 0.0
        notes.append(f"State {facts['state']} {'is' if state else 'is not'} in your focus states")
    ticket, t_unknown = ticket_component(facts.get("ask_inr"), company.get("min_ticket"), company.get("max_ticket"))
    if t_unknown:
        unknowns.append(t_unknown)
    else:
        rng = f"{inr(company.get('min_ticket'))} to {inr(company.get('max_ticket'))}"
        notes.append(f"Ask {inr(facts.get('ask_inr'))} vs your range {rng}")
    comps = {"sector_fit": round(sector, 4), "state_fit": round(state, 4), "ticket_fit": round(ticket, 4)}
    weighted = {k: round(comps[k] * FIT_WEIGHTS[k] * 100, 2) for k in comps}
    return {"components": comps, "weights": FIT_WEIGHTS, "weighted": weighted,
            "total": round(sum(weighted.values()), 1), "notes": notes, "unknowns": unknowns,
            "scored_on": "structured facts only (sectors, state, ask amount); writing quality is not scored"}


def missing_documents(ngo: dict) -> tuple[list[str], list[str]]:
    """Return (missing, present_labels). Present items are labelled self-declared (nobody checks them by hand)."""
    missing, present = [], []
    for field, label in DOCS:
        if ngo.get(field):
            present.append(f"{label} (self-declared)")
        else:
            missing.append(label)
    return missing, present


STANDARD_UNKNOWNS = [
    "Audited financial statements: unknown",
    "Past project outcomes / utilisation certificates: unknown",
    "Credentials are not checked against government registries: unknown",
]


def _template_summary(pitch: dict, ngo: dict) -> list[str]:
    first = re.split(r"(?<=[.!?])\s+", (pitch.get("body") or "").replace("\n", " ").strip())
    first = [s for s in first if s and "csr team" not in s.lower()]
    where = ", ".join(x for x in [ngo.get("district"), ngo.get("state")] if x)
    return [
        f"{ngo.get('name')} ({where or 'location unknown'}) works on {', '.join(pitch.get('sectors') or []) or 'unknown sectors'}.",
        (first[0][:180] if first else pitch.get("subject", "")),
        f"Ask: {inr(pitch.get('ask_inr'))}.",
    ]


def screen(session, pitch: dict, ngo: dict, company: dict) -> dict:
    facts = {"sectors": pitch.get("sectors") or [], "state": pitch.get("state"), "ask_inr": pitch.get("ask_inr")}
    fit = fit_score(facts, company)
    missing, present = missing_documents(ngo)
    evidence = (f"Subject: {pitch.get('subject')}\n{pitch.get('body')}\nNGO: {ngo.get('name')}, {ngo.get('district')}, "
                f"{ngo.get('state')}\nAsk: {inr(pitch.get('ask_inr'))}")
    summary, mode, produced_by, problems = _template_summary(pitch, ngo), "template", None, []
    if session is not None and session.enabled:
        res = session.call("screener", SYSTEM, evidence, ScreenSummary)
        if res is not None:
            check = verify_claims(res.output.lines, evidence, allowed_companies=[ngo.get("name", ""), company.get("name", "")])
            if check.ok:
                summary, mode, produced_by = res.output.lines[:3], "llm", res.meta.produced_by
            else:
                problems = check.problems  # fall back to template; record why
    return {
        "summary": summary, "summary_mode": mode, "produced_by": produced_by, "summary_rejected": problems,
        "fit": fit, "missing_documents": missing, "documents_present": present,
        "unknowns": STANDARD_UNKNOWNS + fit["unknowns"],
        "trace": {"inputs": facts, "criteria": {k: company.get(k) for k in ("focus_sectors", "focus_states", "min_ticket", "max_ticket")},
                  "components": fit["components"], "weights": FIT_WEIGHTS, "weighted": fit["weighted"], "total": fit["total"],
                  "notes": fit["notes"], "llm_calls": session.call_log() if session is not None else [],
                  "policy": "Advisory only. The screener never changes a pitch's status; a person decides."},
    }


# -- NGO recommendations for a company (deterministic, from the company's own profile) ---------------------
REC_WEIGHTS = {"cause_fit": 0.50, "state_fit": 0.35, "readiness": 0.15}
REC_DOCS = ["CSR-1 registration number", "12A registration", "80G registration"]


def ngo_fit(company: dict, listing: dict) -> dict:
    """Score one directory listing against the company's focus sectors and states.

    Uses only fields the NGO chose to list. A field the NGO did not list, or a criterion the company has
    not set, scores NEUTRAL and is reported as unknown (never as a penalty). Wording is never scored."""
    reasons, unknowns = [], []
    focus_sec = {s.lower() for s in company.get("focus_sectors") or []}
    causes = [c.lower() for c in listing.get("causes") or []]
    if not focus_sec:
        cause = NEUTRAL
        unknowns.append("Your focus sectors are not set")
    elif "causes" not in listing:
        cause = NEUTRAL
        unknowns.append("Causes: not listed by the NGO")
    else:
        hit = [c for c in causes if c in focus_sec]
        cause = len(hit) / len(causes) if causes else 0.0
        reasons.append(f"Works on {', '.join(hit)} (your focus)" if hit else "No causes in your focus sectors")

    focus_st = company.get("focus_states") or []
    if not focus_st:
        state = NEUTRAL
        unknowns.append("Your focus states are not set")
    elif not listing.get("state"):
        state = NEUTRAL
        unknowns.append("State: not listed by the NGO")
    else:
        state = 1.0 if listing["state"] in focus_st else 0.0
        reasons.append(f"Based in {listing['state']}" + (" (your focus state)" if state else " (outside your focus states)"))

    if "credentials" not in listing:
        ready = NEUTRAL
        unknowns.append("Registrations: not listed by the NGO")
    else:
        have = [c["label"] for c in listing["credentials"] if c["label"] in REC_DOCS]
        ready = len(have) / len(REC_DOCS)
        if have:
            reasons.append(f"Lists {', '.join(have)} (self-declared)")

    comps = {"cause_fit": round(cause, 4), "state_fit": round(state, 4), "readiness": round(ready, 4)}
    total = round(sum(comps[k] * w for k, w in REC_WEIGHTS.items()) * 100, 1)
    return {"total": total, "components": comps, "weights": REC_WEIGHTS, "reasons": reasons, "unknowns": unknowns}


def recommend_ngos(company: dict, listings: list[dict]) -> list[dict]:
    """Directory listings ranked by fit with the company's profile (ties: name)."""
    out = [{**n, "fit": ngo_fit(company, n)} for n in listings]
    return sorted(out, key=lambda n: (-n["fit"]["total"], n["name"]))

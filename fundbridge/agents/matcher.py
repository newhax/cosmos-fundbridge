"""MATCHER (donor side, Gemini FAST): recommend NGOs to an individual donor.

1. RECALL: read the donor's private memory first (preferences, shortlist, gifts, follows).
2. SCORE: deterministic, from structured fields only:
     cause_overlap 0.50   share of the NGO's causes that the donor cares about
     location      0.30   same state (0.8) + same city/district (0.2)
     giving_fit    0.20   donor giving range vs the NGO's suggested gift (neutral 0.5 if unknown)
   The NGO's summary text, wording or polish never enter the score.
3. EXPLAIN: one batched model call writes a sentence per NGO from the computed facts;
   checked with verify_claims, else an evidence-only template.
4. TRACE: every recommendation carries a decision trace and the memory items it cited.
"""
from __future__ import annotations

from pydantic import BaseModel

from ..evidence import strip_fact_ids
from ..formatting import inr
from ..verify import verify_claims

MATCH_WEIGHTS = {"cause_overlap": 0.50, "location": 0.30, "giving_fit": 0.20}
GIVING_RANGES = {  # label -> (low, high) INR per gift
    "under_1000": (0, 1000), "1000_5000": (1000, 5000), "5000_25000": (5000, 25000),
    "25000_100000": (25000, 100000), "over_100000": (100000, None),
}
FREQUENCIES = ("one_time", "monthly", "yearly")

SYSTEM = """You explain to an individual donor in India why an NGO may match their interests.
Use ONLY the numbered facts for each NGO; copy numbers exactly. One or two plain sentences per NGO.
Do not claim impact, credentials or tax benefits that are not in the facts. Never say "verified".
Never write fact ids like [F1] in the text."""


class Why(BaseModel):
    ngo_id: str
    text: str


class Whys(BaseModel):
    items: list[Why]


def giving_fit(giving_range: str, suggested: float | None) -> tuple[float, str | None]:
    if not suggested:
        return 0.5, "NGO's suggested gift: unknown"
    if giving_range not in GIVING_RANGES:
        return 0.5, "Your giving range: not set"
    lo, hi = GIVING_RANGES[giving_range]
    if suggested >= lo and (hi is None or suggested <= hi):
        return 1.0, None
    bound = lo if suggested < lo else hi  # nearest edge of the donor's range
    return (0.4 if 0.25 <= suggested / bound <= 4 else 0.0), None


def score(donor: dict, ngo: dict) -> dict:
    causes = set(ngo.get("causes") or [])
    wants = set(donor.get("causes") or [])
    overlap = sorted(causes & wants)
    cause = len(overlap) / len(causes) if causes and wants else 0.0
    loc = 0.0
    if donor.get("state") and donor["state"] == ngo.get("state"):
        loc += 0.8
        if donor.get("city") and donor["city"].strip().lower() == (ngo.get("district") or "").strip().lower():
            loc += 0.2
    give, give_unknown = giving_fit(donor.get("giving_range", ""), ngo.get("suggested_gift_inr"))
    comps = {"cause_overlap": round(cause, 4), "location": round(loc, 4), "giving_fit": round(give, 4)}
    weighted = {k: round(v * MATCH_WEIGHTS[k] * 100, 2) for k, v in comps.items()}
    return {"components": comps, "weights": MATCH_WEIGHTS, "weighted": weighted, "total": round(sum(weighted.values()), 1),
            "shared_causes": overlap, "unknowns": [u for u in [give_unknown] if u],
            "scored_on": "causes, location and suggested gift only; profile wording is not scored"}


def facts_for(ngo: dict, s: dict, donor: dict) -> list[str]:
    f = [f"[F1] Shared causes: {', '.join(s['shared_causes']) or 'none'}."]
    where = ", ".join(x for x in [ngo.get("district"), ngo.get("state")] if x) or "location unknown"
    f.append(f"[F2] {ngo['name']} works in {where}; you are in {donor.get('city') or '?'}, {donor.get('state') or '?'}.")
    if ngo.get("suggested_gift_inr"):
        f.append(f"[F3] The NGO suggests gifts of about {inr(ngo['suggested_gift_inr'])}.")
    return f


def recommend(session, donor: dict, ngos: list[dict], memory_items: list[dict], top_n: int = 5) -> list[dict]:
    """ngos: public listings with causes/state/district/suggested_gift_inr/name/ngo_id."""
    by_name = {}
    for m in memory_items:
        if m.get("ngo_id"):
            by_name.setdefault(m["ngo_id"], []).append(m)
    scored = sorted(((n, score(donor, n)) for n in ngos), key=lambda x: (-x[1]["total"], x[0]["name"]))
    recs = []
    for n, s in scored:
        cited = [{"at": m.get("at", "")[:10], "text": m.get("summary", "")} for m in by_name.get(n["ngo_id"], [])]
        prefs = [{"at": m.get("at", "")[:10], "text": m.get("summary", "")} for m in memory_items if m.get("type") == "preferences"][-1:]
        facts = facts_for(n, s, donor)
        recs.append({"ngo": n, "score": s, "facts": facts, "why": " ".join(f.split("] ", 1)[1] for f in facts),
                     "why_mode": "template", "produced_by": None, "memory_used": prefs + cited,
                     "trace": {"inputs": {"donor_causes": donor.get("causes"), "donor_state": donor.get("state"),
                                          "donor_city": donor.get("city"), "giving_range": donor.get("giving_range")},
                               "ngo_fields": {k: n.get(k) for k in ("causes", "state", "district", "suggested_gift_inr")},
                               "components": s["components"], "weights": MATCH_WEIGHTS, "total": s["total"],
                               "memory_cited": len(prefs + cited)}})
    top = recs[:top_n]
    if top and session is not None and session.enabled:
        blocks = "\n\n".join(f"### ngo_id: {r['ngo']['ngo_id']} | {r['ngo']['name']}\n" + "\n".join(r["facts"]) for r in top)
        res = session.call("matcher", SYSTEM, blocks, Whys)
        if res is not None:
            got = {w.ngo_id: w.text for w in res.output.items}
            for r in top:
                text = strip_fact_ids(got.get(r["ngo"]["ngo_id"]) or "") or None
                if text and verify_claims([text], "\n".join(r["facts"]), allowed_companies=[r["ngo"]["name"]]).ok:
                    r.update(why=text, why_mode="llm", produced_by=res.meta.produced_by)
        for r in top:
            r["trace"]["llm_calls"] = session.call_log()
    return recs

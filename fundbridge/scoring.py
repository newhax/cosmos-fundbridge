"""Transparent, deterministic funder scoring. No LLM involved.

Each component is a number in [0, 1] computed only from filing data:

  sector_match           share of the company's CSR spend (in window) that went to the NGO's sectors
  state_match            share of that sector spend that went to the NGO's state (single-state rows only)
  district_overlap       1 if any recorded spend in the NGO's district (any sector), else 0
  consistency_over_years FYs with sector+state spend / FYs in window
  ticket_size_fit        1 - |log10(avg sector ticket / NGO ask)| / 2, floored at 0
                         (same size -> 1.0, 10x apart -> 0.5, 100x apart -> 0)

score = 100 * sum(weight_i * component_i), weights from config.SCORE_WEIGHTS.
"""
from __future__ import annotations

import math

from .config import SCORE_WEIGHTS
from .models import Candidate, PairwiseComparison, ScoreBreakdown

COMPONENT_LABELS = {
    "sector_match": "sector focus",
    "state_match": "state focus",
    "district_overlap": "district overlap",
    "consistency_over_years": "consistency over years",
    "ticket_size_fit": "ticket-size fit",
}


def ticket_fit(avg_ticket: float | None, ask: float) -> float:
    if not avg_ticket or avg_ticket <= 0 or ask <= 0:
        return 0.0
    return max(0.0, 1.0 - abs(math.log10(avg_ticket / ask)) / 2.0)


def components(c: Candidate, funding_ask_inr: float, has_district: bool = True) -> dict[str, float]:
    sector = c.sector_spend_any_state / c.total_spend if c.total_spend > 0 else 0.0
    state = c.sector_state_spend / c.sector_spend_any_state if c.sector_spend_any_state > 0 else 0.0
    return {
        "sector_match": round(min(1.0, sector), 4),
        "state_match": round(min(1.0, state), 4),
        "district_overlap": 1.0 if has_district and c.district_spend > 0 else 0.0,
        "consistency_over_years": round(c.years_funded / c.years_window, 4) if c.years_window else 0.0,
        "ticket_size_fit": round(ticket_fit(c.avg_ticket_sector, funding_ask_inr), 4),
    }


def score(c: Candidate, funding_ask_inr: float, weights: dict[str, float] | None = None, has_district: bool = True) -> ScoreBreakdown:
    w = weights or SCORE_WEIGHTS
    comp = components(c, funding_ask_inr, has_district)
    weighted = {k: round(comp[k] * w[k] * 100, 2) for k in w}
    return ScoreBreakdown(components=comp, weights=dict(w), weighted=weighted, total=round(sum(weighted.values()), 1))


def rank(cands: list[Candidate], funding_ask_inr: float, weights: dict[str, float] | None = None,
         has_district: bool = True) -> list[tuple[Candidate, ScoreBreakdown]]:
    scored = [(c, score(c, funding_ask_inr, weights, has_district)) for c in cands]
    # Ties broken by absolute sector+state spend, then id, so ordering is stable.
    scored.sort(key=lambda cs: (-cs[1].total, -cs[0].sector_state_spend, cs[0].company_id))
    return scored


def pairwise(a: tuple[Candidate, ScoreBreakdown], b: tuple[Candidate, ScoreBreakdown], rank_a: int, rank_b: int) -> PairwiseComparison:
    """Explain why a (higher) outranked b using weighted-point deltas."""
    (ca, sa), (cb, sb) = a, b
    deltas = {k: round(sa.weighted[k] - sb.weighted[k], 2) for k in sa.weighted}
    gap = round(sa.total - sb.total, 1)
    pos = sorted([(v, k) for k, v in deltas.items() if v > 0], reverse=True)
    neg = sorted([(v, k) for k, v in deltas.items() if v < 0])
    parts = [f"{ca.company_name} scores {sa.total} vs {sb.total} for {cb.company_name} (gap {gap} pts)."]
    if pos:
        parts.append("Ahead on " + ", ".join(f"{COMPONENT_LABELS[k]} (+{v})" for v, k in pos[:3]) + ".")
    if neg:
        parts.append("Behind on " + ", ".join(f"{COMPONENT_LABELS[k]} ({v})" for v, k in neg[:3]) + ".")
    if not pos and not neg:
        parts.append("Scores are identical; tie broken by absolute sector+state spend.")
    return PairwiseComparison(
        higher=ca.company_name, lower=cb.company_name, higher_rank=rank_a, lower_rank=rank_b,
        score_gap=gap, component_deltas=deltas, summary=" ".join(parts),
    )

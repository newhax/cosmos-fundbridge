import pytest

from fundbridge.config import SCORE_WEIGHTS
from fundbridge.models import Candidate
from fundbridge.scoring import components, pairwise, rank, score, ticket_fit


def cand(cid="A", **kw):
    base = dict(
        company_id=cid, company_name=f"Co {cid}", sector_state_spend=60.0, sector_spend_any_state=80.0,
        state_spend_any_sector=70.0, total_spend=100.0, district_spend=10.0, multi_state_sector_spend=0.0,
        years_funded=3, years_window=5, fys_funded=[], avg_ticket_sector=500_000.0, n_rows_sector_state=3,
        trend="flat", per_year_sector_state={}, evidence=[], caveats=[],
    )
    base.update(kw)
    return Candidate(**base)


def test_weights_sum_to_one():
    assert sum(SCORE_WEIGHTS.values()) == pytest.approx(1.0)


def test_components_values():
    c = components(cand(), funding_ask_inr=500_000)
    assert c == {"sector_match": 0.8, "state_match": 0.75, "district_overlap": 1.0,
                 "consistency_over_years": 0.6, "ticket_size_fit": 1.0}


def test_total_is_weighted_sum_and_deterministic():
    s1, s2 = score(cand(), 500_000), score(cand(), 500_000)
    expected = 100 * (0.35 * 0.8 + 0.25 * 0.75 + 0.10 * 1 + 0.15 * 0.6 + 0.15 * 1)
    assert s1.total == pytest.approx(round(expected, 1))
    assert s1 == s2


def test_ticket_fit_curve():
    assert ticket_fit(1_000_000, 1_000_000) == 1.0
    assert ticket_fit(10_000_000, 1_000_000) == pytest.approx(0.5)
    assert ticket_fit(100_000, 1_000_000) == pytest.approx(0.5)
    assert ticket_fit(1_000_000_000, 1_000_000) == 0.0
    assert ticket_fit(None, 1_000_000) == 0.0


def test_zero_spend_edge_cases():
    c = components(cand(total_spend=0.0, sector_spend_any_state=0.0, sector_state_spend=0.0), 500_000)
    assert c["sector_match"] == 0 and c["state_match"] == 0


def test_no_district_given_means_no_overlap_points():
    assert components(cand(), 500_000, has_district=False)["district_overlap"] == 0.0


def test_rank_order_and_tiebreak():
    a = cand("A", years_funded=5)
    b = cand("B", years_funded=1)
    tie1 = cand("T1", sector_state_spend=60.0)
    ranked = rank([b, tie1, a], 500_000)
    assert [c.company_id for c, _ in ranked][0] == "A"
    assert [c.company_id for c, _ in ranked][-1] == "B"


def test_pairwise_explains_gap():
    ranked = rank([cand("A", years_funded=5), cand("B", years_funded=2, district_spend=0.0)], 500_000)
    p = pairwise(ranked[0], ranked[1], 1, 2)
    assert p.score_gap == pytest.approx(ranked[0][1].total - ranked[1][1].total, abs=0.11)
    assert p.component_deltas["consistency_over_years"] > 0
    assert p.component_deltas["district_overlap"] == 10.0
    assert "Ahead on" in p.summary

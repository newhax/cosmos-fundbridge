"""Turn a Candidate's numbers into numbered, sourced fact sentences.

These facts are the only material the LLM may use. Each has an id (F1..),
a pre-formatted text and the source_refs of the filing rows behind it.
Absence-of-data facts cite DATASET_QUERY_REF (the SQL in the decision trace).
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from .formatting import inr, pct
from .models import Candidate, NGOProfile

# Fact ids belong in the structured fact_ids field, never in text a person reads:
# "[F1]", "(F2, F5)", "F1, F5", "F1/F3" are all removed.
_FACT_IDS = re.compile(r"\s*[\[(]?\bF\d+\b(?:\s*[,/&]\s*F\d+\b)*[\])]?")


def strip_fact_ids(text: str) -> str:
    out = _FACT_IDS.sub("", text or "")
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = re.sub(r"([.,;:])\1+", r"\1", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()

DATASET_QUERY_REF = "query:dataset"

STANDARD_UNKNOWNS = [
    "CSR decision-maker / contact person: unknown",
    "CSR team email or phone: unknown",
    "Current-year CSR policy and focus areas: unknown",
    "Upcoming CSR budget: unknown",
    "Whether they accept unsolicited proposals: unknown",
]


class Fact(BaseModel):
    fact_id: str
    text: str
    source_refs: list[str]


def sector_label(sectors: list[str]) -> str:
    return " / ".join(sectors)


def build_facts(c: Candidate, ngo: NGOProfile, sectors: list[str]) -> list[Fact]:
    label = sector_label(sectors)
    window = list(c.per_year_sector_state)
    by_kind = lambda *kinds: [e.source_ref for e in c.evidence if e.kind in kinds]  # noqa: E731
    ss_refs, sec_refs = by_kind("sector+state"), by_kind("sector+state", "sector", "multi_state")
    facts: list[Fact] = []

    def add(text: str, refs: list[str]) -> None:
        facts.append(Fact(fact_id=f"F{len(facts) + 1}", text=text, source_refs=refs or [DATASET_QUERY_REF]))

    if c.sector_state_spend > 0:
        add(f"{c.company_name} reported {inr(c.sector_state_spend)} of CSR spend in {label} in {ngo.state} "
            f"across {c.years_funded} of the last {c.years_window} financial years (FY{window[0]} to FY{window[-1]}).", ss_refs)
    else:
        add(f"No single-state {label} spend in {ngo.state} is recorded for {c.company_name} "
            f"between FY{window[0]} and FY{window[-1]} in this dataset.", [])
    if c.total_spend > 0:
        add(f"{c.company_name} reported {inr(c.sector_spend_any_state)} in {label} across all states, "
            f"{pct(c.sector_spend_any_state / c.total_spend)} of its {inr(c.total_spend)} total recorded CSR spend in that period.", sec_refs)
    if ngo.district:
        district = ngo.district.strip().title()
        if c.district_spend > 0:
            add(f"{inr(c.district_spend)} of its recorded spend (any sector) was in {district} district.",
                [e.source_ref for e in c.evidence if (e.district or "").lower() == district.lower()])
        else:
            add(f"No spend in {district} district appears for {c.company_name} in this dataset.", [])
    if c.avg_ticket_sector:
        add(f"Its average reported project amount in {label} is {inr(c.avg_ticket_sector)}, "
            f"compared with your ask of {inr(ngo.funding_ask_inr)}.", sec_refs)
    if c.sector_state_spend > 0:
        yearly = ", ".join(f"FY{fy}: {inr(v) if v else '₹0'}" for fy, v in c.per_year_sector_state.items())
        add(f"Year by year in {ngo.state} ({label}): {yearly}; trend {c.trend}.", ss_refs)
    if c.multi_state_sector_spend > 0:
        add(f"A further {inr(c.multi_state_sector_spend)} in {label} was filed as multi-state or PAN India "
            f"and is not counted towards {ngo.state}.", by_kind("multi_state"))
    if c.state_spend_any_sector > 0:
        add(f"{inr(c.state_spend_any_sector)} recorded in {ngo.state} across all sectors.",
            by_kind("sector+state", "state"))
    return facts


def unknowns_for(c: Candidate) -> list[str]:
    out = list(STANDARD_UNKNOWNS)
    if not c.avg_ticket_sector:
        out.append("Typical grant size in your sector: unknown (no sector rows)")
    if c.trend == "unknown":
        out.append("Spending trend: unknown (fewer than 2 funded years)")
    return out


def facts_text(facts: list[Fact]) -> str:
    return "\n".join(f"[{f.fact_id}] {f.text}" for f in facts)


def refs_for(facts: list[Fact], fact_ids: list[str]) -> list[str]:
    by_id = {f.fact_id: f for f in facts}
    out: list[str] = []
    for fid in fact_ids:
        for r in by_id[fid].source_refs if fid in by_id else []:
            if r not in out:
                out.append(r)
    return out

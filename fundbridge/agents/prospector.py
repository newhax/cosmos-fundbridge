"""PROSPECTOR: map the NGO cause to controlled sectors, then query CSR filings.

The LLM may only choose sectors from the controlled list; all numbers come
from SQL via CSRStore.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..models import Candidate
from ..sectors import SECTORS, clean_causes, keyword_sectors
from .context import Context

SectorName = Literal[tuple(s for s in SECTORS if s != "other")]  # type: ignore[valid-type]

SYSTEM = """You map a small Indian NGO's cause to CSR development sectors.
Choose 1 to 3 sectors ONLY from the allowed list, most relevant first.
Prefer fewer sectors; add a second or third only if the project clearly works in it."""


class SectorChoice(BaseModel):
    sectors: list[SectorName] = Field(min_length=1, max_length=3)
    rationale: str = Field(description="One sentence explaining the mapping, referring only to the NGO's own words.")


TYPED_SYSTEM = """An Indian NGO typed its own CSR sector. Map it to at most 2 sectors from the allowed list,
but ONLY if the typed text clearly describes work in that sector. If it is unclear, unrelated or nonsense,
return an empty list. Never guess."""


class TypedSectorChoice(BaseModel):
    sectors: list[SectorName] = Field(default_factory=list, max_length=2,
                                      description="Empty when no allowed sector clearly fits.")


class ProspectorResult(BaseModel):
    sectors: list[str]
    sector_rationale: str
    sector_mode: str  # user | keywords | "llm (<provider:model>)"
    candidates: list[Candidate]
    query: dict


class Prospector:
    name = "prospector"

    def map_typed(self, ctx: Context, typed: str) -> tuple[list[str], str]:
        """A sector the NGO typed under "Other": the CSR filings only use the controlled list, so map it
        to the closest controlled sectors (keywords first, then the model). ([], "") if nothing fits."""
        kws = keyword_sectors(typed, max_n=2)
        if kws:
            return kws, "keywords"
        res = ctx.llm.call("prospector", TYPED_SYSTEM,
                           f"Allowed sectors: {', '.join(s for s in SECTORS if s != 'other')}\n\nTyped sector: {typed}",
                           TypedSectorChoice)
        if res is not None and res.output.sectors:
            return list(dict.fromkeys(res.output.sectors))[:2], f"model {res.meta.produced_by}"
        return [], ""

    def map_sectors(self, ctx: Context, override: list[str] | None = None) -> tuple[list[str], str, str]:
        if override:
            try:
                chosen = clean_causes(override)
            except ValueError as e:
                raise ValueError(f"Sectors: {e}") from e
            picked = [c for c in chosen if c in SECTORS and c != "other"]
            notes, unmatched = [], []
            for typed in [c for c in chosen if c not in SECTORS]:
                mapped, how = self.map_typed(ctx, typed)
                if mapped:
                    notes.append(f"'{typed}' (typed by the NGO) is searched as {' / '.join(mapped)} ({how})")
                    picked += mapped
                else:
                    unmatched.append(typed)
            if unmatched:
                raise ValueError(f"Could not match {', '.join(repr(u) for u in unmatched)} to a CSR sector in the "
                                 "public filings. Pick the closest sector from the list instead.")
            if picked:
                why = "Sectors chosen by the NGO on the onboarding form." + (" " + "; ".join(notes) + "." if notes else "")
                return list(dict.fromkeys(picked)), why, "user"
        text = f"{ctx.ngo.cause}. {ctx.ngo.project_summary}"
        res = ctx.llm.call(
            "prospector",
            SYSTEM,
            f"Allowed sectors: {', '.join(s for s in SECTORS if s != 'other')}\n\nNGO cause: {ctx.ngo.cause}\n"
            f"Project summary: {ctx.ngo.project_summary}",
            SectorChoice,
        )
        if res is not None:
            return list(dict.fromkeys(res.output.sectors)), res.output.rationale, f"llm ({res.meta.produced_by})"
        kws = keyword_sectors(text)
        if kws:
            return kws, f"Matched keywords in the cause/summary to: {', '.join(kws)}.", "keywords"
        raise ValueError("Could not map the cause to a CSR sector. Please pick sectors on the form.")

    def run(self, ctx: Context, sectors_override: list[str] | None = None, limit: int = 20) -> ProspectorResult:
        sectors, rationale, mode = self.map_sectors(ctx, sectors_override)
        cands, query = ctx.csr.top_funders(sectors, ctx.ngo.state, ctx.ngo.district, ctx.lookback_years, limit)
        query["dataset"] = ctx.csr.dataset_in_use()
        return ProspectorResult(sectors=sectors, sector_rationale=rationale, sector_mode=mode,
                                candidates=cands, query=query)

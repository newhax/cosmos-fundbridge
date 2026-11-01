"""RESEARCHER: deterministic ranking + batched, cross-checked explanations + decision trace.

Free-tier friendly: the top N funders are explained in ONE model call (role "researcher"),
then all their claims are verified in ONE verifier call. Failed items are retried once
together; anything still rejected is marked needs_human_review and shown with the
evidence-only template. Ranks below N always use the template.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from ..config import SCORE_WEIGHTS
from ..crosscheck import model_verify
from ..evidence import Fact, build_facts, facts_text, refs_for, sector_label, strip_fact_ids, unknowns_for
from ..formatting import inr
from ..models import Candidate, DecisionTrace, RankedFunder, ScoreBreakdown, Sentence
from ..scoring import pairwise, rank
from ..stores.csr_base import dataset_caveats
from ..verify import verify_claims
from .context import Context
from .prospector import ProspectorResult

EXPLAIN_TOP_N = 5

SYSTEM = """You explain why companies are plausible CSR funders for a small Indian NGO.
Rules (an automated checker and a second model enforce them; violations are rejected):
- For each company use ONLY its own numbered facts. Copy numbers exactly as written.
- Every sentence lists the fact ids it relies on (ids are per company: F1, F2, ...).
- The first sentence must quote F1's main amount and years exactly as written in F1.
- District amounts are for ANY sector: never describe them as spend in the NGO's sectors.
- Do not state scores, ranks or amounts that are not in the facts.
- Never mention another company inside a company's explanation. No contacts, people, policies or budgets.
- Plain, factual tone. 2 to 4 sentences per company. Return one item per company_id given."""


class ExplSentence(BaseModel):
    text: str
    fact_ids: list[str] = Field(description="Ids like F1, F2 of this company's facts")


class CompanyExplanation(BaseModel):
    company_id: str
    short_why: str = Field(description="Max ~15 words for a table cell, using fact numbers only")
    sentences: list[ExplSentence]


class ExplanationsOut(BaseModel):
    items: list[CompanyExplanation]


class Researcher:
    name = "researcher"

    @staticmethod
    def _template(c: Candidate, facts: list[Fact], ctx: Context, sectors: list[str]) -> tuple[str, list[Sentence]]:
        if c.sector_state_spend > 0:
            why = f"{inr(c.sector_state_spend)} in {ctx.ngo.state} {sector_label(sectors)}, {c.years_funded} of the last {c.years_window} years"
        else:
            why = f"{inr(c.sector_spend_any_state)} in {sector_label(sectors)} across all states"
        keep = [f for f in facts if f.fact_id in {"F1", "F2", "F3", "F4"}]
        return why, [Sentence(text=f.text, source_refs=f.source_refs) for f in keep]

    def _explain_batch(self, ctx: Context, top: list[Candidate], facts: dict[str, list[Fact]],
                       all_names: list[str]) -> tuple[dict[str, dict], list[dict]]:
        """Return ({company_id: result}, verification_log). result has status/output/problems/produced_by."""
        results: dict[str, dict] = {}
        log: list[dict] = []
        if not ctx.llm.enabled:
            return results, [{"note": "offline: templates used"}]
        by_id = {c.company_id: c for c in top}
        pending = list(by_id)
        feedback: dict[str, list[str]] = {}
        last: dict[str, CompanyExplanation] = {}
        meta = None
        for attempt in (1, 2):
            blocks = []
            for cid in pending:
                block = f"### company_id: {cid} | {by_id[cid].company_name}\n{facts_text(facts[cid])}"
                if feedback.get(cid):
                    block += "\nPREVIOUS ANSWER REJECTED:\n- " + "\n- ".join(feedback[cid])
                blocks.append(block)
            res = ctx.llm.call("researcher", SYSTEM,
                               f"NGO: {ctx.ngo.name}, working on {ctx.ngo.cause} in {ctx.ngo.district} {ctx.ngo.state}.\n\n"
                               + "\n\n".join(blocks), ExplanationsOut)
            if res is None:
                log.append({"attempt": attempt, "ok": False, "problems": ["no model output"]})
                break
            meta = res.meta
            items = {i.company_id: i for i in res.output.items if i.company_id in pending}
            for it in items.values():  # fact ids stay in fact_ids, not in the readable text
                it.short_why = strip_fact_ids(it.short_why)
                for x in it.sentences:
                    x.text = strip_fact_ids(x.text)
            problems: dict[str, list[str]] = {}
            for cid in pending:
                item = items.get(cid)
                if item is None:
                    problems[cid] = ["no explanation returned for this company"]
                    continue
                last[cid] = item
                c = by_id[cid]
                r = verify_claims([item.short_why] + [s.text for s in item.sentences], facts_text(facts[cid]),
                                  allowed_companies=[c.company_name], other_companies=all_names,
                                  cited_fact_ids=[s.fact_ids for s in item.sentences],
                                  valid_fact_ids={f.fact_id for f in facts[cid]})
                if r.problems:
                    problems[cid] = r.problems
            # one verifier call for all claims that passed the rules
            claims, owner = [], {}
            for cid in pending:
                if cid in problems:
                    continue
                item = items[cid]
                for j, text in enumerate([item.short_why] + [s.text for s in item.sentences]):
                    claim_id = f"{cid}#{j}"
                    claims.append((claim_id, f"[{by_id[cid].company_name}] {text}"))
                    owner[claim_id] = cid
            evidence = "\n\n".join(f"### {by_id[cid].company_name}\n{facts_text(facts[cid])}" for cid in pending)
            bad = model_verify(ctx.llm, evidence, claims)
            for claim_id, why in (bad or {}).items():
                problems.setdefault(owner[claim_id], []).append(f"verifier: {why}")
            for cid in pending:
                if cid not in problems:
                    results[cid] = {"status": "verified" if bad is not None else "rules_only", "item": items[cid],
                                    "produced_by": meta.produced_by, "fallback_from": meta.fallback_from}
            log.append({"attempt": attempt, "produced_by": meta.produced_by,
                        "ok": not problems, "problems": [f"{by_id[c].company_name}: {p}" for c, ps in problems.items() for p in ps]})
            pending = [cid for cid in pending if cid in problems]
            feedback = problems
            if not pending:
                break
        for cid in pending:
            results[cid] = {"status": "needs_human_review" if cid in last or meta else "unavailable",
                            "problems": feedback.get(cid, []), "rejected": last.get(cid),
                            "produced_by": meta.produced_by if meta else None,
                            "fallback_from": meta.fallback_from if meta else None}
        return results, log

    def run(self, ctx: Context, pr: ProspectorResult, explain: bool = True,
            log: bool = True) -> tuple[list[RankedFunder], DecisionTrace]:
        """Rank (deterministic) and, if explain, write + check model explanations for the top companies.

        explain=False returns the ranking with template explanations in ~0.1 s; the pipeline then
        re-runs with explain=True in the background (same candidates, so the same ranking)."""
        has_district = bool(ctx.ngo.district.strip())
        ranked: list[tuple[Candidate, ScoreBreakdown]] = rank(pr.candidates, ctx.ngo.funding_ask_inr, SCORE_WEIGHTS, has_district)
        names = [c.company_name for c, _ in ranked]
        facts = {c.company_id: build_facts(c, ctx.ngo, pr.sectors) for c, _ in ranked}
        if explain:
            llm_results, vlog = self._explain_batch(ctx, [c for c, _ in ranked[:EXPLAIN_TOP_N]], facts, names)
        else:
            llm_results, vlog = {}, [{"note": "explanations pending (written in the background)"}]

        results: list[RankedFunder] = []
        for i, (c, s) in enumerate(ranked, start=1):
            why, sents = self._template(c, facts[c.company_id], ctx, pr.sectors)
            r = llm_results.get(c.company_id)
            extra: dict = {"explanation_mode": "template", "explanation_status": "template"}
            if r and r["status"] in ("verified", "rules_only"):
                item: CompanyExplanation = r["item"]
                why = item.short_why
                sents = [Sentence(text=x.text, source_refs=refs_for(facts[c.company_id], x.fact_ids)) for x in item.sentences]
                extra = {"explanation_mode": "llm", "explanation_status": r["status"],
                         "produced_by": r["produced_by"], "fallback_from": r["fallback_from"]}
            elif r:
                rej = r.get("rejected")
                extra = {"explanation_mode": "template", "explanation_status": r["status"],
                         "produced_by": r["produced_by"], "fallback_from": r["fallback_from"],
                         "review_problems": r["problems"],
                         "rejected_text": ([rej.short_why] + [x.text for x in rej.sentences]) if rej else []}
            results.append(RankedFunder(
                rank=i, company_id=c.company_id, company_name=c.company_name, score=s, candidate=c, short_why=why,
                explanation=sents, sources=sorted({e.source_ref for e in c.evidence}), unknowns=unknowns_for(c), **extra,
            ))

        top = list(zip(ranked[:5], range(1, 6)))
        pairs = [pairwise(top[i][0], top[i + 1][0], top[i][1], top[i + 1][1]) for i in range(len(top) - 1)]
        trace = DecisionTrace(
            trace_id=f"trace_{uuid.uuid4().hex[:10]}",
            run_id=ctx.run_id,
            ngo_id=ctx.ngo.ngo_id,
            created_at=ctx.now.isoformat(timespec="seconds"),
            inputs={"ngo": ctx.ngo.model_dump(), "sectors": pr.sectors, "sector_rationale": pr.sector_rationale,
                    "sector_mode": pr.sector_mode, "lookback_years": ctx.lookback_years},
            query=pr.query,
            weights=dict(SCORE_WEIGHTS),
            ranking=[{"rank": r.rank, "company_id": r.company_id, "company": r.company_name, "score": r.score.total,
                      "components": r.score.components, "weighted": r.score.weighted, "n_sources": len(r.sources),
                      "n_unknowns": len(r.unknowns), "explanation_status": r.explanation_status,
                      "produced_by": r.produced_by} for r in results],
            pairwise=pairs,
            data_quality_caveats=self._caveats(ctx, results[:5]),
            verification_log=vlog,
            llm_calls=ctx.llm.call_log(),
        )
        if hasattr(ctx.csr, "current_provenance"):
            trace.data_provenance = ctx.csr.current_provenance(dataset_caveats(ctx.csr.data_quality_report())) or {}
        if log:
            ctx.memory.log_trace(trace)
        return results, trace

    @staticmethod
    def _caveats(ctx: Context, top: list[RankedFunder]) -> list[str]:
        rep = ctx.csr.data_quality_report()
        out = []
        if rep.get("data_source") == "SAMPLE":
            out.append("SAMPLE DATA: rankings are computed on a synthetic dataset of fictional companies, not real filings.")
        if rep.get("rows_missing_state"):
            out.append(f"{rep['rows_missing_state']} rows dataset-wide have no state; they are excluded from state totals.")
        if rep.get("rows_multiple_states"):
            out.append(f"{rep['rows_multiple_states']} rows list multiple states or PAN India; their amounts are not attributed to any single state.")
        if rep.get("exact_duplicates_removed"):
            out.append(f"{rep['exact_duplicates_removed']} exact duplicate rows were removed before scoring.")
        if rep.get("name_variants_merged"):
            out.append(f"{rep['name_variants_merged']} company-name spelling variants were merged by normalized name/CIN.")
        for r in top:
            out.extend(f"{r.company_name}: {c}" for c in r.candidate.caveats)
        return out

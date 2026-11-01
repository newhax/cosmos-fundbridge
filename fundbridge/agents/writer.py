"""WRITER: two tone variants of a short outreach email, fact-checked.

Never sends anything. Addresses "CSR Team" because decision-maker contacts
are not in public filings.
"""
from __future__ import annotations


from pydantic import BaseModel, Field

from ..evidence import strip_fact_ids, Fact, build_facts, facts_text, refs_for, sector_label
from ..formatting import inr
from ..models import EmailDraft, NGOProfile, OutreachPackage, RankedFunder, Sentence
from ..crosscheck import Checked, generate_checked, split_sentences
from ..verify import VerificationResult, verify_claims
from .context import Context

MAX_WORDS = 180
CONTACT_NOTE = "Contact details are not in public CSR data. Add the company's CSR email manually before sending."

SYSTEM = f"""You draft short, respectful first-contact emails from a small Indian NGO to a company's CSR team.
Rules (an automated checker enforces them; violations are rejected):
- Address the email to "CSR Team" (e.g. "Dear CSR Team,"). Never invent a person's name, title, email or phone.
- Facts about the company may come ONLY from the numbered facts. Copy numbers exactly as written.
- Facts about the NGO may come only from the NGO profile.
- Use at most 2 company facts; list the fact ids you used.
- Do not mention any other company. No promises of outcomes, no flattery about things not in the facts.
- Body under {MAX_WORDS} words, in short paragraphs separated by blank lines. Sign off with the NGO's name only.
- Never write fact ids like [F1] in the subject or body; list them only in fact_ids.
- Produce two variants: "formal" and "warm" (warm is friendlier, still professional)."""


class EmailOut(BaseModel):
    subject: str
    body: str
    fact_ids: list[str] = Field(description="Fact ids (F1..) used in this email")


class EmailsOut(BaseModel):
    formal: EmailOut
    warm: EmailOut


def ngo_text(ngo: NGOProfile) -> str:
    return (f"NGO name: {ngo.name}\nCause: {ngo.cause}\nLocation: {ngo.district + ', ' if ngo.district else ''}{ngo.state}\n"
            f"Project summary: {ngo.project_summary}\nFunding ask: {inr(ngo.funding_ask_inr)}")


def _short(text: str, words: int) -> str:
    w = text.split()
    return " ".join(w[:words]) + ("..." if len(w) > words else "")


def template_emails(ngo: NGOProfile, r: RankedFunder, facts: list[Fact], sectors: list[str]) -> tuple[list[EmailDraft], list[str]]:
    c = r.candidate
    place = f"{ngo.district + ', ' if ngo.district else ''}{ngo.state}"
    label = sector_label(sectors)
    if c.sector_state_spend > 0:
        fact = (f"your company reported {inr(c.sector_state_spend)} of CSR spend in {label} in {ngo.state} "
                f"across {c.years_funded} of the last {c.years_window} financial years")
        used = ["F1"]
    else:
        fact = f"your company reported {inr(c.sector_spend_any_state)} of CSR spend in {label} across all states"
        used = ["F2"] if any(f.fact_id == "F2" for f in facts) else ["F1"]
    summary = _short(ngo.project_summary, 40)
    formal = (
        f"Dear CSR Team,\n\nI am writing on behalf of {ngo.name}, which works on {ngo.cause} in {place}. {summary}\n\n"
        f"Public CSR filings show that {fact}. Our work is closely aligned with this focus.\n\n"
        f"We are seeking {inr(ngo.funding_ask_inr)} for this project and would welcome the opportunity to share a short "
        f"proposal and budget. Could you let us know the right process or person for proposals?\n\nYours sincerely,\n{ngo.name}"
    )
    warm = (
        f"Dear CSR Team,\n\nNamaste! We are {ngo.name}, a small team working on {ngo.cause} in {place}. {summary}\n\n"
        f"We noticed in public CSR filings that {fact}. It would mean a lot to explore whether our project could be part of that journey.\n\n"
        f"Our ask is {inr(ngo.funding_ask_inr)}. Could we share a one-page proposal, or could you point us to the right person?\n\n"
        f"Warm regards,\n{ngo.name}"
    )
    subject_f = f"CSR partnership proposal: {ngo.cause} in {place}"
    subject_w = f"{ngo.name}: {ngo.cause} in {place}"
    return [EmailDraft(tone="formal", subject=subject_f, body=formal, mode="template"),
            EmailDraft(tone="warm", subject=subject_w, body=warm, mode="template")], used


def check_emails(out: EmailsOut, evidence: str, company: str, other_companies: list[str], valid_ids: set[str]) -> VerificationResult:
    variants = [out.formal, out.warm]
    res = verify_claims(
        [v.subject + "\n" + v.body for v in variants], evidence,
        allowed_companies=[company], other_companies=other_companies,
        cited_fact_ids=[v.fact_ids for v in variants], valid_fact_ids=valid_ids,
    )
    for name, v in (("formal", out.formal), ("warm", out.warm)):
        n = len(v.body.split())
        if n > MAX_WORDS:
            res.problems.append(f"{name} body has {n} words (max {MAX_WORDS})")
        if "csr team" not in v.body.lower():
            res.problems.append(f"{name} body must address the 'CSR Team'")
    res.ok = not res.problems
    return res




def strip_ids(text: str) -> str:
    """Remove fact-id markers a model may leave in email text."""
    return strip_fact_ids(text)


class Writer:
    name = "writer"

    def run(self, ctx: Context, r: RankedFunder, sectors: list[str], other_companies: list[str]) -> OutreachPackage:
        facts = build_facts(r.candidate, ctx.ngo, sectors)
        evidence = facts_text(facts) + "\n\n" + ngo_text(ctx.ngo) + f"\nWord limit: {MAX_WORDS}"
        drafts, used = None, []
        checked = Checked(status="unavailable")
        if ctx.llm.enabled:
            # role "writer": Claude first, Gemini MAIN on any Claude failure; Gemini FAST verifies.
            checked = generate_checked(
                ctx.llm, "writer", SYSTEM,
                f"Company: {r.company_name}\n\nCompany facts:\n{facts_text(facts)}\n\nNGO profile:\n{ngo_text(ctx.ngo)}",
                EmailsOut,
                lambda o: check_emails(o, evidence, r.company_name, other_companies, {f.fact_id for f in facts}),
                lambda o: split_sentences("\n".join([o.formal.subject, o.formal.body, o.warm.subject, o.warm.body])),
                evidence,
            )
            out = checked.output
            if out is not None:
                tag = checked.produced_by
                drafts = [EmailDraft(tone=t, subject=strip_ids(v.subject), body=strip_ids(v.body), mode="llm", produced_by=tag)
                          for t, v in (("formal", out.formal), ("warm", out.warm))]
                used = list(dict.fromkeys(out.formal.fact_ids + out.warm.fact_ids))
        if drafts is None:
            drafts, used = template_emails(ctx.ngo, r, facts, sectors)
        by_id = {f.fact_id: f for f in facts}
        facts_used = [Sentence(text=by_id[i].text, source_refs=refs_for(facts, [i])) for i in used if i in by_id]
        return OutreachPackage(
            ngo_id=ctx.ngo.ngo_id, company_id=r.company_id, company_name=r.company_name, drafts=drafts,
            facts_used=facts_used, contact_note=CONTACT_NOTE,
            status=checked.status if ctx.llm.enabled else "template", produced_by=checked.produced_by,
            fallback_from=checked.fallback_from, review_problems=checked.problems, rejected=checked.rejected,
            llm_calls=ctx.llm.call_log(),
        )

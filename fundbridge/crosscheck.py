"""Cross-model check: one model writes, Gemini FAST verifies each claim against the evidence.

Order for every generated output:
  1. deterministic rules (verify.verify_claims: numbers, other companies, contacts, citations)
  2. model verifier (role "verifier"): every claim must be supported by the evidence
  3. any rejection -> retry once with the problems listed -> still rejected -> "needs_human_review"
Statuses are always surfaced; nothing passes silently:
  verified            rules + model verifier passed
  rules_only          rules passed, model verifier unavailable (rate limit / offline / cache miss)
  needs_human_review  rejected twice; the evidence-only template is shown and the rejected draft kept for review
  unavailable         no model produced output; evidence-only template shown
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from .verify import VerificationResult

T = TypeVar("T", bound=BaseModel)

VERIFIER_SYSTEM = """You are a strict fact checker. You receive EVIDENCE and a list of CLAIMS.
For each claim decide if every factual statement in it (numbers, amounts, years, places, sectors,
company or person names, commitments) is directly supported by the EVIDENCE.
Greetings, sign-offs, polite requests and opinions without facts count as supported.
A claim with any number, name or fact that is not in the EVIDENCE is NOT supported.
Return one verdict per claim id, with a short reason for unsupported claims."""


class ClaimVerdict(BaseModel):
    claim_id: str
    supported: bool
    reason: str = ""


class VerifierOut(BaseModel):
    verdicts: list[ClaimVerdict]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip() for p in parts if len(p.strip()) > 3]


def model_verify(session, evidence: str, claims: list[tuple[str, str]]) -> dict[str, str] | None:
    """Return {claim_id: reason} for unsupported claims, or None if no verifier was available."""
    if not claims:
        return {}
    listing = "\n".join(f"[{cid}] {text}" for cid, text in claims)
    res = session.call("verifier", VERIFIER_SYSTEM, f"EVIDENCE:\n{evidence}\n\nCLAIMS:\n{listing}", VerifierOut,
                       max_tokens=4096)
    if res is None:
        return None
    verdicts = {v.claim_id: v for v in res.output.verdicts}
    out = {}
    for cid, _ in claims:
        v = verdicts.get(cid)
        if v is None:
            out[cid] = "verifier returned no verdict"  # never treat a missing verdict as a pass
        elif not v.supported:
            out[cid] = v.reason or "not supported by evidence"
    return out


@dataclass
class Checked:
    output: Any = None
    status: str = "unavailable"
    problems: list[str] = field(default_factory=list)
    rejected: Any = None
    produced_by: str | None = None
    fallback_from: str | None = None


def generate_checked(
    session,
    role: str,
    system: str,
    user: str,
    schema: type[T],
    rule_check: Callable[[T], VerificationResult],
    claims_of: Callable[[T], list[str]],
    evidence: str,
) -> Checked:
    prompt, last, problems = user, None, []
    for _attempt in (1, 2):
        res = session.call(role, system, prompt, schema)
        if res is None:
            break
        last = res
        problems = list(rule_check(res.output).problems)
        model_ok = None
        if not problems:
            claims = [(f"C{i + 1}", t) for i, t in enumerate(claims_of(res.output))]
            bad = model_verify(session, evidence, claims)
            model_ok = bad is not None
            text = dict(claims)
            problems = [f"verifier: \"{text[c][:120]}\" ({why})" for c, why in (bad or {}).items()]
        if not problems:
            return Checked(res.output, "verified" if model_ok else "rules_only", [],
                           produced_by=res.meta.produced_by, fallback_from=res.meta.fallback_from)
        prompt = (f"{user}\n\nYour previous answer was rejected by the fact check:\n- " + "\n- ".join(problems)
                  + "\nRewrite it using ONLY facts and numbers that appear verbatim in the evidence.")
    if last is None:
        return Checked(status="unavailable")
    return Checked(None, "needs_human_review", problems, rejected=last.output.model_dump(),
                   produced_by=last.meta.produced_by, fallback_from=last.meta.fallback_from)

"""verify_claims(): reject LLM output that states anything not in the evidence.

Checks (all deterministic):
  1. Every number in the output appears verbatim (after comma stripping) in the evidence.
  2. No company other than the allowed ones is named.
  3. No invented contact details: email addresses, phone numbers, honorific + name.
  4. Every sentence cites at least one fact id, and only fact ids that exist.
  5. No banned marketing claims ("first of its kind").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_RE = re.compile(r"(?:\+91[\s-]?)?\b\d{5}[\s-]?\d{5}\b")
_HONORIFIC_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Shri|Smt|Sri)\.?\s+[A-Z][a-z]+")
BANNED_PHRASES = ["first of its kind", "first-of-its-kind", "guaranteed funding", "guarantee"]


def numbers_in(text: str) -> set[str]:
    out = set()
    for tok in _NUM_RE.findall(text or ""):
        t = tok.replace(",", "").rstrip(".")
        try:
            f = float(t)
        except ValueError:
            continue
        out.add(f"{f:g}")
    return out


@dataclass
class VerificationResult:
    ok: bool
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "problems": self.problems}


def verify_claims(
    texts: list[str],
    evidence_text: str,
    *,
    allowed_companies: list[str] = (),
    other_companies: list[str] = (),
    cited_fact_ids: list[list[str]] | None = None,
    valid_fact_ids: set[str] | None = None,
    allowed_contacts: list[str] = (),
) -> VerificationResult:
    problems: list[str] = []
    allowed_nums = numbers_in(evidence_text)
    joined = "\n".join(texts)

    for n in sorted(numbers_in(joined) - allowed_nums):
        problems.append(f"number '{n}' does not appear in the evidence")

    scrub = joined.lower()
    for name in sorted((n for n in allowed_companies if n), key=len, reverse=True):
        scrub = scrub.replace(name.lower(), " ")
    for name in (n for n in other_companies if n):
        if name and name.lower() in scrub and name not in allowed_companies:
            problems.append(f"mentions company '{name}' that is not in this evidence")

    for email in _EMAIL_RE.findall(joined):
        if email not in allowed_contacts:
            problems.append(f"invented contact email '{email}'")
    for phone in _PHONE_RE.findall(joined):
        problems.append(f"invented phone number '{phone}'")
    for hon in _HONORIFIC_RE.findall(joined):
        if hon not in allowed_contacts:
            problems.append(f"invented named person '{hon}'")
    low = joined.lower()
    for phrase in BANNED_PHRASES:
        if phrase in low:
            problems.append(f"banned claim '{phrase}'")

    if cited_fact_ids is not None:
        valid = valid_fact_ids or set()
        for i, ids in enumerate(cited_fact_ids):
            if not ids:
                problems.append(f"sentence {i + 1} cites no fact id")
            for fid in ids:
                if fid not in valid:
                    problems.append(f"sentence {i + 1} cites unknown fact id '{fid}'")

    return VerificationResult(ok=not problems, problems=problems)

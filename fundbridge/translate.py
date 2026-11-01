"""Machine translation of DRAFTS only (emails, pitch text), never of evidence, scores or data.

Protected tokens (names, IDs such as CIN / CSR-1 / Darpan / PAN, amounts and other numbers, dates, URLs,
emails and source_refs) are replaced by numbered placeholders before the model sees the text and put back
afterwards, so they cannot be altered. verify_translation() then checks, without any model, that:
  - every protected token from the English text appears in the translation exactly as often as in English,
  - no number, ID or name appears that is not in the English text (translation must not add claims),
  - the placeholders were all restored.
Every result carries MACHINE_LABEL; a failed check returns status "rejected" and the English draft is kept.
"""
from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, Field

from .config import I18N_LANGUAGES

MACHINE_LABEL = "Machine translated. Review before sending."

_PATTERNS = [
    r"https?://\S+",
    r"[\w.+-]+@[\w-]+\.[\w.-]+",
    r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b",          # CIN
    r"\bSYN\d{13}\b",                                    # synthetic CIN in the SAMPLE data
    r"\bCSR\d{8}\b",                                     # CSR-1 registration number
    r"\b[A-Z]{2}/\d{4}/\d{7}\b",                         # NGO Darpan ID
    r"\b[A-Z]{5}\d{4}[A-Z]\b",                           # PAN
    r"[^\s#|]+#L\d+\|FY\d{4}",                           # source_ref
    r"\b\d{1,2}/\d{1,2}/\d{4}\b|\b\d{4}-\d{2}-\d{2}\b",  # dates
    r"\bFY\s?\d{4}-\d{2}\b|\b\d{4}-\d{2}\b",             # fiscal years
    r"₹\s?[\d,]+(?:\.\d+)?(?:\s?(?:lakh|crore))?",       # amounts
    r"\b\d[\d,]*(?:\.\d+)?%?",                           # any other number
]
_TOKEN_RE = re.compile("|".join(f"(?:{p})" for p in _PATTERNS))
_PH = "⟦{}⟧"
_PH_RE = re.compile(r"⟦(\d+)⟧")


class TranslationOut(BaseModel):
    text: str = Field(description="The translation, keeping every ⟦n⟧ placeholder exactly once and unchanged.")


SYSTEM = """You translate a short draft written by an Indian NGO or company into {lang}.
Rules: keep every placeholder like ⟦3⟧ exactly as it is, once each; they stand for names, IDs, amounts and dates.
Translate faithfully: do not add, remove or soften any statement, and add no facts, numbers or names.
Keep the paragraph breaks. Use a respectful, plain register."""


def protect(text: str, names: list[str]) -> tuple[str, list[str]]:
    """Replace names and pattern tokens with placeholders. Returns (masked text, tokens by placeholder index)."""
    tokens: list[str] = []
    masked = text
    for n in sorted({n.strip() for n in names if n and len(n.strip()) > 2}, key=len, reverse=True):
        if n in masked:
            tokens.append(n)
            masked = masked.replace(n, _PH.format(len(tokens) - 1))

    def sub(m: re.Match) -> str:
        tokens.append(m.group(0))
        return _PH.format(len(tokens) - 1)

    parts = re.split(r"(⟦\d+⟧)", masked)  # never re-mask inside a placeholder
    masked = "".join(p if _PH_RE.fullmatch(p) else _TOKEN_RE.sub(sub, p) for p in parts)
    return masked, tokens


def restore(text: str, tokens: list[str]) -> str:
    return _PH_RE.sub(lambda m: tokens[int(m.group(1))] if int(m.group(1)) < len(tokens) else m.group(0), text)


def verify_translation(english: str, translated: str, names: list[str]) -> dict:
    """Deterministic check that names, IDs, numbers and dates survived and nothing was added."""
    problems = []
    _, src_tokens = protect(english, names)
    _, out_tokens = protect(translated, names)
    want, got = Counter(src_tokens), Counter(out_tokens)
    for t, n in want.items():
        if got[t] != n:
            problems.append(f"'{t}' appears {got[t]} times, expected {n}")
    for t in got:
        if t not in want:
            problems.append(f"'{t}' is not in the English draft (added)")
    if _PH_RE.search(translated):
        problems.append("a placeholder was not restored")
    return {"ok": not problems, "problems": problems[:10], "checked_tokens": sum(want.values())}


def translate_draft(session, text: str, lang: str, names: list[str]) -> dict:
    """Translate one draft. Returns {status, text, label, check}. status: translated | rejected | unavailable."""
    if lang not in I18N_LANGUAGES or lang == "en":
        raise ValueError(f"Unsupported language {lang!r}")
    masked, tokens = protect(text, names)
    res = session.call("translator", SYSTEM.format(lang=I18N_LANGUAGES[lang]), masked, TranslationOut)
    if res is None:
        return {"status": "unavailable", "text": text, "label": "",
                "reason": "AI capacity reached, try later." if getattr(session, "capacity_reached", False)
                else "Translation is unavailable right now (no model answered). The English draft is unchanged."}
    out = restore(res.output.text, tokens)
    check = verify_translation(text, out, names)
    if not check["ok"]:
        return {"status": "rejected", "text": text, "label": "", "check": check,
                "reason": "The translation changed or added names, IDs, numbers or dates, so it was not used."}
    return {"status": "translated", "text": out, "label": MACHINE_LABEL, "check": check, "lang": lang,
            "produced_by": res.meta.produced_by}

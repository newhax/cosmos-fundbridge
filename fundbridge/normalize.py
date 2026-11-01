"""Deterministic cleaning helpers for messy public CSR filings."""
from __future__ import annotations

import re

# Legal-form suffixes stripped for matching (never for display).
_SUFFIXES = [
    "PRIVATE LIMITED", "PVT LTD", "PVT LIMITED", "PRIVATE LTD", "LIMITED", "LTD",
    "PVT", "PRIVATE", "CORPORATION", "CORP", "COMPANY", "CO", "INC", "LLP", "PLC",
]
_SUFFIX_RE = re.compile(r"(?:\s+(?:" + "|".join(re.escape(s) for s in _SUFFIXES) + r"))+$")
_PUNCT_RE = re.compile(r"[^\w\s&]")
_WS_RE = re.compile(r"\s+")


def company_key(raw: str) -> str:
    """Matching key: upper-case, no punctuation, no legal suffix, single spaces.

    "Aravali Cements Pvt. Ltd."  -> "ARAVALI CEMENTS"
    "ARAVALI  CEMENTS LIMITED"   -> "ARAVALI CEMENTS"
    """
    s = (raw or "").upper().replace("&", " AND ")
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    if s.startswith("THE "):
        s = s[4:]
    s = _SUFFIX_RE.sub("", s).strip()
    return s


def display_name(key: str) -> str:
    """Readable name derived from the key ("ARAVALI CEMENTS" -> "Aravali Cements")."""
    words = [w if w in _ACRONYM_HINTS else w.capitalize() for w in key.split()]
    return " ".join(words).replace(" And ", " & ")


# Short tokens that should stay upper case in display names.
_ACRONYM_HINTS = {"ONGC", "NTPC", "IT", "HDFC", "ICICI", "SBI", "LIC", "BHEL", "GAIL", "IOC", "ITC", "MRF", "JSW", "UPL", "DLF", "ABC", "KPR"}


# --- States ----------------------------------------------------------------------

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal", "Delhi", "Jammu and Kashmir", "Ladakh", "Puducherry", "Chandigarh",
    "Andaman and Nicobar Islands", "Dadra and Nagar Haveli and Daman and Diu", "Lakshadweep",
]
_STATE_ALIASES = {
    "orissa": "Odisha",
    "uttaranchal": "Uttarakhand",
    "pondicherry": "Puducherry",
    "nct of delhi": "Delhi",
    "new delhi": "Delhi",
    "j&k": "Jammu and Kashmir",
    "jammu & kashmir": "Jammu and Kashmir",
    "tamilnadu": "Tamil Nadu",
    "lakshadweep islands": "Lakshadweep",
    "dadra nagar haveli and daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
}
_STATE_LOOKUP = {s.lower(): s for s in INDIAN_STATES} | _STATE_ALIASES
_MISSING_TOKENS = {"", "na", "n/a", "nan", "none", "null", "-", "not mentioned", "not specified"}
_MULTI_TOKENS = {"pan india", "pan-india", "all india", "multiple states", "multiple", "various states"}


def normalize_state(raw: str) -> tuple[str | None, list[str], str]:
    """Return (single_state_or_None, states_list, quality).

    quality is one of: ok | multiple | missing. We never guess: a row with
    several states (or "PAN India") stays one row, flagged "multiple", and its
    amount is not attributed to any single state.
    """
    s = (raw or "").strip()
    low = s.lower()
    if low in _MISSING_TOKENS:
        return None, [], "missing"
    if low in _MULTI_TOKENS:
        return None, ["PAN India"], "multiple"
    parts = [p.strip() for p in re.split(r"[;,|/]|\band\b(?=\s+[A-Z])", s) if p.strip()]
    found: list[str] = []
    for p in parts:
        canon = _STATE_LOOKUP.get(_WS_RE.sub(" ", p.lower()))
        if canon and canon not in found:
            found.append(canon)
    if not found:
        # Whole-string lookup handles names containing "and" (Jammu and Kashmir).
        canon = _STATE_LOOKUP.get(_WS_RE.sub(" ", low))
        if canon:
            return canon, [canon], "ok"
        return None, [], "missing"
    if len(found) == 1:
        return found[0], found, "ok"
    return None, found, "multiple"


def parse_fy(raw: str) -> tuple[str, int] | None:
    """'2021-22', 'FY 2021-2022', '2021' -> ('2021-22', 2021)."""
    m = re.search(r"(20\d{2})\s*[-/]\s*(\d{2,4})", raw or "")
    if m:
        start = int(m.group(1))
        return f"{start}-{str(start + 1)[-2:]}", start
    m = re.search(r"(20\d{2})", raw or "")
    if m:
        start = int(m.group(1))
        return f"{start}-{str(start + 1)[-2:]}", start
    return None


def parse_amount(raw: str) -> float | None:
    """Parse INR amounts like '12,50,000', '1250000.00', 'Rs. 4,00,000'. Invalid -> None."""
    s = re.sub(r"(?i)rs\.?|inr|₹", "", str(raw or "")).replace(",", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def clean_district(raw: str) -> str | None:
    s = _WS_RE.sub(" ", (raw or "").strip())
    if s.lower() in _MISSING_TOKENS:
        return None
    return s.title()

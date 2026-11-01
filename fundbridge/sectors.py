"""Controlled sector vocabulary and the mapping table from raw CSR labels.

Raw "development area" labels on the National CSR Portal follow Schedule VII of
the Companies Act but are written in many ways. We map them to a small
controlled list with an explicit, ordered keyword table so every mapping is
reproducible and auditable. Unmatched labels become "other" and are counted in
the data-quality report rather than guessed.
"""
from __future__ import annotations

import re

SECTORS = [
    "education",
    "health",
    "livelihood",
    "environment",
    "rural development",
    "women empowerment",
    "water and sanitation",
    "hunger and poverty",
    "child welfare",
    "disability and elderly",
    "sports and culture",
    "disaster relief",
    "other",
]

# Causes typed in by an NGO or company ("Other"). Stored as typed (lower-cased) next to the standard
# sectors and matched by exact wording only: they are never silently mapped to a standard sector.
MAX_CUSTOM_CAUSES = 5
_CUSTOM_CAUSE_RE = re.compile(r"^[a-z0-9][a-z0-9 &,'()/.-]{1,39}$")


def clean_causes(values) -> list[str]:
    """Standard sectors plus up to MAX_CUSTOM_CAUSES custom causes (2-40 characters). Raises ValueError."""
    out: list[str] = []
    custom = 0
    for raw in values or []:
        v = re.sub(r"\s+", " ", str(raw)).strip().lower()
        if not v or v == "other" or v in out:
            continue  # "other" is the UI switch for typing a cause, not a cause itself
        if v not in SECTORS:
            if not _CUSTOM_CAUSE_RE.match(v):
                raise ValueError(f"'{str(raw).strip()[:40]}' is not a valid cause: use 2-40 letters, numbers, "
                                 "spaces or & , ' ( ) / . -")
            custom += 1
            if custom > MAX_CUSTOM_CAUSES:
                raise ValueError(f"Add at most {MAX_CUSTOM_CAUSES} causes of your own.")
        out.append(v)
    return out


# Ordered (first match wins). Patterns are matched against the lower-cased raw label.
SECTOR_MAPPING_TABLE: list[tuple[str, str]] = [
    (r"sanitation|drinking water|safe water|swachh|toilet", "water and sanitation"),
    (r"hunger|poverty|malnutrition|nutrition", "hunger and poverty"),
    (r"women|gender|girl", "women empowerment"),
    (r"vocational|skill|livelihood|employment", "livelihood"),
    (r"education|school|literacy|scholarship|learning", "education"),
    (r"health|medical|hospital|preventive|disease", "health"),
    (r"environment|ecolog|forest|agroforestry|animal welfare|natural resources|climate|renewable", "environment"),
    (r"rural development|rural|village", "rural development"),
    (r"orphan|child", "child welfare"),
    (r"senior citizen|old age|elderly|differently abled|disab", "disability and elderly"),
    (r"sport|olympic|art|culture|heritage", "sports and culture"),
    (r"disaster|relief|rehabilitation|covid|pm cares|prime minister", "disaster relief"),
]
_COMPILED = [(re.compile(p), s) for p, s in SECTOR_MAPPING_TABLE]


def map_sector(raw_label: str) -> str:
    low = (raw_label or "").lower()
    for rx, sector in _COMPILED:
        if rx.search(low):
            return sector
    return "other"


# Keyword fallback for NGO cause -> sectors (used when the LLM is unavailable,
# and to sanity-check the LLM's choice).
CAUSE_KEYWORDS: dict[str, list[str]] = {
    "education": ["education", "school", "literacy", "learning", "teach", "student", "librar", "reading"],
    "health": ["health", "medical", "clinic", "hospital", "disease", "maternal", "mental health"],
    "livelihood": ["livelihood", "skill", "vocational", "employment", "income", "artisan", "self-help"],
    "environment": ["environment", "tree", "forest", "climate", "waste", "recycl", "solar", "biodiversity"],
    "rural development": ["rural", "village", "gram", "panchayat", "farmer", "agricultur"],
    "women empowerment": ["women", "girl", "gender", "mahila"],
    "water and sanitation": ["water", "sanitation", "toilet", "hygiene", "wash"],
    "hunger and poverty": ["hunger", "food", "nutrition", "malnutrition", "poverty", "meal"],
    "child welfare": ["child", "orphan", "anganwadi", "early childhood"],
    "disability and elderly": ["disab", "elderly", "senior", "blind", "deaf"],
    "sports and culture": ["sport", "culture", "art", "heritage", "music"],
    "disaster relief": ["disaster", "flood", "relief", "cyclone"],
}


def keyword_sectors(text: str, max_n: int = 3) -> list[str]:
    low = (text or "").lower()
    scored = []
    for sector, kws in CAUSE_KEYWORDS.items():
        hits = sum(low.count(k) for k in kws)
        if hits:
            scored.append((hits, sector))
    scored.sort(key=lambda t: (-t[0], SECTORS.index(t[1])))
    # Secondary sectors must be at least half as strong as the best match.
    top = scored[0][0] if scored else 0
    return [s for hits, s in scored[:max_n] if hits * 2 >= top]

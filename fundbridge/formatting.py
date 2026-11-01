"""Number and date formatting.

inr() is used in evidence facts: model output must reuse these exact strings, so it never changes.
The display helpers below follow Indian conventions: lakh/crore digit grouping (12,34,567), an explicit unit
(rupees / lakh / crore, always shown), dates as DD/MM/YYYY in IST, and the April-March financial year.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")
UNITS = ("rupees", "lakh", "crore")


def inr(v: float | None) -> str:
    if v is None:
        return "unknown"
    if v >= 1e7:
        return f"₹{v / 1e7:.2f} crore"
    if v >= 1e5:
        return f"₹{v / 1e5:.1f} lakh"
    return f"₹{v:,.0f}"


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


def group_indian(n: int) -> str:
    """12,34,567: the last three digits, then groups of two."""
    s = str(abs(int(n)))
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ("-" if n < 0 else "") + ",".join(groups + [tail]) if groups else ("-" if n < 0 else "") + tail


def inr_unit(v: float | None, unit: str = "rupees", decimals: int = 2) -> str:
    """₹12,34,567 | ₹12.35 lakh | ₹0.12 crore. The unit is always written out (never a bare number)."""
    if v is None:
        return "unknown"
    if unit not in UNITS:
        raise ValueError(f"unit must be one of {UNITS}")
    if unit == "rupees":
        return f"₹{group_indian(round(v))}"
    div = 1e5 if unit == "lakh" else 1e7
    whole, frac = divmod(round(v / div, decimals), 1)
    s = group_indian(int(whole)) + (f"{frac:.{decimals}f}"[1:] if decimals else "")
    return f"₹{s} {unit}"


def fmt_date(d: date | datetime | str | None) -> str:
    """DD/MM/YYYY; datetimes are shown in IST."""
    if d is None or d == "":
        return "unknown"
    if isinstance(d, str):
        d = datetime.fromisoformat(d) if "T" in d or " " in d else date.fromisoformat(d)
    if isinstance(d, datetime):
        d = (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(IST)
    return d.strftime("%d/%m/%Y")


def fiscal_year(d: date | datetime) -> str:
    """Indian financial year, April to March: 15/03/2026 -> 2025-26, 01/04/2026 -> 2026-27."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"

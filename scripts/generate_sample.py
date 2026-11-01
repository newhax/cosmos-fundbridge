"""Generate a clearly labelled SYNTHETIC CSR dataset (~2,000 rows).

Only used when no real CSV exists in data/raw/. All company names are
FICTIONAL so synthetic numbers are never attributed to a real company. The
data is deliberately messy (name variants, duplicates, missing and
multi-state rows, state aliases) so the ingestion pipeline has real work to do.

Usage: python scripts/generate_sample.py
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fundbridge.config import SAMPLE_DIR  # noqa: E402

OUT = SAMPLE_DIR / "csr_sample_SYNTHETIC.csv"
FYS = ["2019-20", "2020-21", "2021-22", "2022-23", "2023-24"]

DISTRICTS = {
    "Rajasthan": ["Jodhpur", "Barmer", "Ajmer", "Bhilwara", "Udaipur", "Jaipur", "Alwar", "Bikaner", "Dungarpur", "Tonk"],
    "Maharashtra": ["Pune", "Mumbai", "Nagpur", "Nashik", "Aurangabad"],
    "Gujarat": ["Ahmedabad", "Kutch", "Vadodara", "Surat"],
    "Uttar Pradesh": ["Lucknow", "Varanasi", "Bahraich", "Gorakhpur"],
    "Karnataka": ["Bengaluru Urban", "Mysuru", "Raichur"],
    "Tamil Nadu": ["Chennai", "Coimbatore", "Madurai"],
    "Odisha": ["Khordha", "Koraput", "Angul"],
    "Madhya Pradesh": ["Bhopal", "Indore", "Jhabua"],
    "Jharkhand": ["Ranchi", "Dhanbad"],
    "West Bengal": ["Kolkata", "Purulia"],
}

LABELS = {
    "education": ["Education", "Promoting education including special education", "Special Education", "education"],
    "livelihood": ["Vocational Skills", "Livelihood Enhancement Projects"],
    "health": ["Health Care", "Preventive Health Care"],
    "hunger": ["Eradicating Hunger, Poverty and Malnutrition"],
    "water": ["Safe Drinking Water", "Sanitation"],
    "environment": ["Environmental Sustainability", "Agro Forestry", "Animal Welfare"],
    "rural": ["Rural Development Projects"],
    "women": ["Women Empowerment", "Gender Equality"],
    "child": ["Orphanage"],
    "elderly": ["Senior Citizens Welfare"],
    "sports": ["Training to promote sports", "Art and Culture"],
    "disaster": ["Disaster Management", "Prime Minister's National Relief Fund"],
    "other": ["Any Other Fund", "Socio-Economic Development", "Technology Incubator"],
}

# Hand-designed fictional companies that make the demo NGO (education, rural
# Rajasthan) interesting: different strengths so the ranking is explainable.
# (name, cin, {sector: weight}, {state: weight}, preferred districts, ticket_lakh, years_active)
DESIGNED = [
    ("Thar Minerals Ltd", "SYN0000000000001", {"education": 6, "health": 2, "rural": 2}, {"Rajasthan": 9, "Gujarat": 1}, ["Barmer", "Jodhpur"], 45, FYS),
    ("Aravali Cements Pvt. Ltd.", "SYN0000000000002", {"education": 5, "rural": 3, "water": 2}, {"Rajasthan": 8, "Madhya Pradesh": 2}, ["Ajmer", "Bhilwara", "Tonk"], 12, FYS[1:]),
    ("Marudhar Power Limited", "SYN0000000000003", {"environment": 6, "education": 2, "water": 2}, {"Rajasthan": 9, "Gujarat": 1}, ["Bikaner", "Jodhpur"], 30, FYS),
    ("Sahyadri Textiles Ltd", "SYN0000000000004", {"education": 7, "women": 3}, {"Maharashtra": 9, "Rajasthan": 1}, ["Pune"], 25, FYS),
    ("Kesari Agro Foods Ltd", "SYN0000000000005", {"hunger": 4, "rural": 4, "education": 2}, {"Rajasthan": 5, "Uttar Pradesh": 5}, ["Alwar", "Jaipur"], 8, FYS[2:]),
    ("Pink City Software Pvt Ltd", "SYN0000000000006", {"education": 8, "women": 2}, {"Rajasthan": 6, "Karnataka": 4}, ["Jaipur", "Udaipur"], 6, FYS[3:]),
    ("Mewar Zinc & Metals Ltd", "SYN0000000000007", {"health": 5, "education": 3, "livelihood": 2}, {"Rajasthan": 9, "Odisha": 1}, ["Udaipur", "Dungarpur"], 70, FYS),
    ("Ganga Valley Finance Ltd", "SYN0000000000008", {"education": 5, "livelihood": 5}, {"Uttar Pradesh": 6, "Rajasthan": 2, "Madhya Pradesh": 2}, ["Tonk"], 15, FYS),
]

NAME_PARTS_A = ["Deccan", "Kaveri", "Narmada", "Konark", "Vindhya", "Sundarban", "Malabar", "Godavari", "Satpura",
                "Himgiri", "Nilgiri", "Chambal", "Brahmaputra", "Coromandel", "Saurashtra", "Kalinga", "Bundel",
                "Doaba", "Rann", "Shivalik", "Tapti", "Mahanadi", "Konkan", "Chola", "Magadh"]
NAME_PARTS_B = ["Steel", "Chemicals", "Pharma", "Logistics", "Infra", "Motors", "Textiles", "Foods", "Power",
                "Polymers", "Fintech", "Paper", "Cables", "Tyres", "Ceramics", "Bank", "Energy", "Glass"]
SUFFIXES = ["Ltd", "Limited", "Pvt Ltd", "Private Limited"]


def variants(name: str, rng: random.Random) -> list[str]:
    """Messy spellings of one company name, as seen across real filings."""
    base = name
    for s in [" Pvt. Ltd.", " Pvt Ltd", " Private Limited", " Limited", " Ltd"]:
        if base.endswith(s):
            base = base[: -len(s)]
            break
    return [name, f"{base.upper()} LIMITED", f"{base} Ltd.", f"{base}  Ltd", f"The {base} Limited"][: rng.randint(1, 4)]


def pick(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def main() -> None:
    rng = random.Random(42)
    companies = list(DESIGNED)
    used = {c[0] for c in companies}
    i = len(companies)
    while len(companies) < 130:
        name = f"{rng.choice(NAME_PARTS_A)} {rng.choice(NAME_PARTS_B)} {rng.choice(SUFFIXES)}"
        if name in used:
            continue
        used.add(name)
        i += 1
        sectors = {k: rng.random() * 5 + 0.2 for k in rng.sample(list(LABELS), rng.randint(2, 4))}
        states = {k: rng.random() * 5 + 0.2 for k in rng.sample(list(DISTRICTS), rng.randint(1, 3))}
        start = rng.randint(0, 3)
        companies.append((name, f"SYN{i:013d}", sectors, states, [], rng.choice([3, 5, 10, 20, 40, 80]), FYS[start:]))

    rows = []
    for name, cin, sectors, states, pref_districts, ticket_lakh, years in companies:
        names = variants(name, rng)
        for fy in years:
            for _ in range(rng.randint(3, 7)):
                sector = pick(rng, sectors)
                state = pick(rng, states)
                if pref_districts and state == "Rajasthan" and rng.random() < 0.7:
                    district = rng.choice(pref_districts)
                else:
                    district = rng.choice(DISTRICTS[state])
                amount = round(ticket_lakh * 100000 * rng.uniform(0.5, 1.6), -3)
                state_raw = state
                r = rng.random()
                if r < 0.04:
                    state_raw, district = rng.choice(["", "NA", "Not Mentioned"]), ""
                elif r < 0.08:
                    other = rng.choice([s for s in DISTRICTS if s != state])
                    state_raw, district = rng.choice(["PAN India", f"{state}, {other}"]), ""
                elif state == "Odisha" and rng.random() < 0.5:
                    state_raw = "Orissa"
                elif rng.random() < 0.05:
                    state_raw = state.upper()
                rows.append({
                    "Company Name": rng.choice(names),
                    "CIN": cin if rng.random() > 0.15 else "",
                    "Development Area": rng.choice(LABELS[sector]),
                    "State": state_raw,
                    "District": district if rng.random() > 0.03 else "",
                    "Financial Year": fy,
                    "Amount Spent (INR)": f"{amount:,.0f}" if rng.random() < 0.3 else f"{amount:.2f}",
                })

    # Exact duplicate rows, as when the same filing is scraped twice.
    for r in rng.sample(rows, k=int(len(rows) * 0.03)):
        rows.append(dict(r))
    rng.shuffle(rows)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} SYNTHETIC rows for {len(companies)} fictional companies -> {OUT}")


if __name__ == "__main__":
    main()

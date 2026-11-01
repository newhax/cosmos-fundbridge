from fundbridge.normalize import company_key, display_name, normalize_state, parse_amount, parse_fy
from fundbridge.sectors import keyword_sectors, map_sector


def test_company_key_merges_suffix_case_punctuation_variants():
    variants = ["Aravali Cements Pvt. Ltd.", "ARAVALI CEMENTS LIMITED", "Aravali Cements  Ltd",
                "The Aravali Cements Limited", "aravali cements private limited"]
    assert {company_key(v) for v in variants} == {"ARAVALI CEMENTS"}


def test_company_key_keeps_distinct_companies_apart():
    assert company_key("Thar Minerals Ltd") != company_key("Thar Metals Ltd")


def test_ampersand_and_display_name():
    assert company_key("Mewar Zinc & Metals Ltd") == company_key("Mewar Zinc and Metals Limited")
    assert display_name(company_key("Mewar Zinc & Metals Ltd")) == "Mewar Zinc & Metals"


def test_state_ok_alias_and_case():
    assert normalize_state("Rajasthan") == ("Rajasthan", ["Rajasthan"], "ok")
    assert normalize_state("RAJASTHAN ") == ("Rajasthan", ["Rajasthan"], "ok")
    assert normalize_state("Orissa") == ("Odisha", ["Odisha"], "ok")
    assert normalize_state("Jammu and Kashmir")[2] == "ok"


def test_state_multiple_and_missing_are_flagged_not_guessed():
    state, states, q = normalize_state("Rajasthan, Gujarat")
    assert (state, q) == (None, "multiple") and states == ["Rajasthan", "Gujarat"]
    assert normalize_state("PAN India") == (None, ["PAN India"], "multiple")
    for missing in ["", "NA", "Not Mentioned", "  "]:
        assert normalize_state(missing) == (None, [], "missing")
    assert normalize_state("Atlantis")[2] == "missing"


def test_fy_and_amount_parsing():
    assert parse_fy("2021-22") == ("2021-22", 2021)
    assert parse_fy("FY 2021-2022") == ("2021-22", 2021)
    assert parse_fy("garbage") is None
    assert parse_amount("12,50,000") == 1250000.0
    assert parse_amount("Rs. 4,00,000.00") == 400000.0
    assert parse_amount("0") is None and parse_amount("abc") is None


def test_sector_mapping_table():
    assert map_sector("Promoting education including special education") == "education"
    assert map_sector("Eradicating Hunger, Poverty and Malnutrition") == "hunger and poverty"
    assert map_sector("Safe Drinking Water") == "water and sanitation"
    assert map_sector("Vocational Skills") == "livelihood"
    assert map_sector("Gender Equality") == "women empowerment"
    assert map_sector("Any Other Fund") == "other"


def test_keyword_sectors_for_cause():
    assert keyword_sectors("education for girls in rural villages")[0] in {"education", "women empowerment", "rural development"}
    assert "education" in keyword_sectors("school libraries and digital literacy")

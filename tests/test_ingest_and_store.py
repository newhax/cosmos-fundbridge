import csv

import pytest

from fundbridge import datasets
from fundbridge.ingest import clean_rows
from fundbridge.stores.csr_dataset import DatasetCSRStore

HEADER = ["Company Name", "CIN", "Development Area", "State", "District", "Financial Year", "Amount Spent (INR)"]
ROWS = [
    ["Aravali Cements Pvt. Ltd.", "SYN1", "Education", "Rajasthan", "Ajmer", "2022-23", "10,00,000"],
    ["ARAVALI CEMENTS LIMITED", "", "Education", "RAJASTHAN", "Ajmer", "2023-24", "500000"],
    ["ARAVALI CEMENTS LIMITED", "", "Education", "RAJASTHAN", "Ajmer", "2023-24", "500000"],  # exact duplicate
    ["Aravali Cements Ltd", "SYN1", "Health Care", "PAN India", "", "2023-24", "300000"],
    ["Thar Minerals Ltd", "SYN2", "Education", "", "", "2023-24", "900000"],
    ["Thar Minerals Ltd", "SYN2", "Education", "Gujarat", "Kutch", "2022-23", "200000"],
    ["Broken Co", "", "Education", "Rajasthan", "", "2023-24", "not a number"],
]


@pytest.fixture()
def store(tmp_path):
    src = tmp_path / "mini.csv"
    with src.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(ROWS)
    rows, _ = clean_rows(src)
    root = tmp_path / "datasets"
    datasets.activate(datasets.build_version([src], "SAMPLE", root=root)["version"], root)
    store = DatasetCSRStore(root)
    return store, store.data_quality_report(), rows


def test_quality_report_counts(store):
    _, rep, rows = store
    assert rep["rows_read"] == 7
    assert rep["exact_duplicates_removed"] == 1
    assert rep["rows_dropped_invalid_amount"] == 1
    assert rep["rows_missing_state"] == 1
    assert rep["rows_multiple_states"] == 1
    assert rep["companies"] == 2  # name variants + CIN merged into one Aravali
    assert rep["name_variants_merged"] == 2
    assert len(rows) == 5


def test_every_row_has_source_ref(store):
    _, _, rows = store
    assert all(r["source_ref"].startswith("mini.csv#L") and "|FY" in r["source_ref"] for r in rows)


def test_missing_cin_inherited_from_same_name_key(store):
    _, _, rows = store
    assert {r["company_id"] for r in rows if r["company_name"] == "Aravali Cements"} == {"SYN1"}


def test_top_funders_only_attributes_single_state_rows(store):
    csr, _, _ = store
    cands, q = csr.top_funders(["education"], "Rajasthan", "Ajmer", years=5, limit=10)
    by = {c.company_name: c for c in cands}
    ara = by["Aravali Cements"]
    assert ara.sector_state_spend == 1_500_000  # duplicate not double counted
    assert ara.district_spend == 1_500_000
    assert ara.years_funded == 2
    assert all(e.source_ref for e in ara.evidence)
    # Thar's Rajasthan claim is only via a missing-state row, so no state attribution.
    assert by["Thar Minerals"].sector_state_spend == 0
    assert "SELECT" in q["sql"] and q["params"]["state"] == "Rajasthan"


def test_company_profile_caveats_and_rows(store):
    csr, _, rows = store
    prof = csr.company_profile("SYN1")
    assert prof["n_name_variants"] == 3
    assert any("merged from 3" in c for c in prof["caveats"])
    assert any("PAN India" in c for c in prof["caveats"])
    got = csr.get_rows([rows[0]["source_ref"]])
    assert got and got[0]["company_name_raw"] == "Aravali Cements Pvt. Ltd."


def test_mca_lakhs_export_format(tmp_path):
    """India Data Portal / MCA export: snake_case headers, year as a date, amounts in lakhs."""
    src = tmp_path / "csr-projects-by-companies.csv"
    with src.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "year", "state_name", "district_name", "csr_project_name", "development_sector",
                    "company_name", "cin", "amount_spent_in_lakhs"])
        w.writerow(["1", "2019-01-01", "Lakshadweep Islands", "Kavaratti", "School A", "Education", "Acme Ltd", "L1", "12.5"])
        w.writerow(["2", "2019-01-01", "Lakshadweep Islands", "Kavaratti", "School B", "Education", "Acme Ltd", "L1", "12.5"])
    rows, rep = clean_rows(src)
    assert rep.get("exact_duplicates_removed", 0) == 0  # different projects, same figures
    assert [r["amount_inr"] for r in rows] == [1_250_000, 1_250_000]
    assert rows[0]["fy"] == "2019-20" and rows[0]["state"] == "Lakshadweep" and rows[0]["sector"] == "education"

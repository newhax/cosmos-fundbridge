from fundbridge.verify import numbers_in, verify_claims

EVIDENCE = """[F1] Thar Minerals reported ₹10.30 crore of CSR spend in education in Rajasthan across 5 of the last 5 financial years (FY2019-20 to FY2023-24).
[F2] Its average reported project amount in education is ₹47.8 lakh, compared with your ask of ₹10.0 lakh."""


def test_numbers_normalised():
    assert numbers_in("₹10.30 crore, 1,250,000 and 5") == {"10.3", "1.25e+06", "5"}


def test_accepts_grounded_text():
    r = verify_claims(["Thar Minerals reported ₹10.3 crore for education in Rajasthan in 5 of the last 5 years."],
                      EVIDENCE, allowed_companies=["Thar Minerals"])
    assert r.ok, r.problems


def test_rejects_invented_number():
    r = verify_claims(["Thar Minerals plans to spend ₹12 crore next year."], EVIDENCE, allowed_companies=["Thar Minerals"])
    assert not r.ok and any("'12'" in p for p in r.problems)


def test_rejects_other_company():
    r = verify_claims(["Unlike Aravali Cements, Thar Minerals funds education."], EVIDENCE,
                      allowed_companies=["Thar Minerals"], other_companies=["Aravali Cements", "Thar Minerals"])
    assert not r.ok and any("Aravali Cements" in p for p in r.problems)


def test_rejects_invented_contacts_and_people():
    r = verify_claims(["Write to Mr. Sharma at csr@tharminerals.com or call 98765 43210."], EVIDENCE)
    joined = " ".join(r.problems)
    assert "Mr. Sharma" in joined and "csr@tharminerals.com" in joined and "phone" in joined


def test_fact_id_citations_required_and_valid():
    r = verify_claims(["a", "b"], EVIDENCE, cited_fact_ids=[[], ["F9"]], valid_fact_ids={"F1", "F2"})
    assert "sentence 1 cites no fact id" in r.problems
    assert any("F9" in p for p in r.problems)


def test_banned_marketing_phrase():
    assert not verify_claims(["The first of its kind platform."], EVIDENCE).ok


def test_fact_ids_are_stripped_from_readable_text():
    from fundbridge.evidence import strip_fact_ids

    assert strip_fact_ids("Consistent spend in Rajasthan. F1, F5") == "Consistent spend in Rajasthan."
    assert strip_fact_ids("Spent 3 crore [F1] over 5 years [F2].") == "Spent 3 crore over 5 years."
    assert strip_fact_ids("Funds schools (F3, F4) in FY2020-21.") == "Funds schools in FY2020-21."

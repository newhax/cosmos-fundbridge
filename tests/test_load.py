"""Scale (spec test 9): synthetic 2x dataset in the test run (scripts/load_test.py does 10x), query thresholds."""
import sys
from pathlib import Path

import pytest

from fundbridge.config import RAW_DIR

REAL = sorted(RAW_DIR.glob("*.csv"))
pytestmark = pytest.mark.skipif(not REAL, reason="needs the real CSR file in data/raw/ to synthesize from")


def test_queries_stay_under_thresholds_on_a_scaled_dataset():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import load_test

    rep = load_test.run(scale=2, repeat=3, src=REAL[0])
    assert rep["rows_loaded"] > 60_000
    slow = {k: v for k, v in rep["queries"].items() if not v["ok"]}
    assert not slow, slow
    for v in rep["queries"].values():  # repeated queries come from the per-version cache
        assert v["warm_p50_s"] <= v["cold_s"]

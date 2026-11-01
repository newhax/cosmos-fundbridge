"""Load test: build a SYNTHETIC dataset about N times the real one and time the queries the app runs.

  python scripts/load_test.py            # 10x the real file (data/raw/*.csv)
  python scripts/load_test.py --scale 3

The synthetic rows copy the real rows' places, sectors and years, with fictional companies (new CINs and
names) and jittered amounts. It is built in a temporary folder and labelled SAMPLE; nothing touches the
app's datasets. Prints p50 / p95 per query and fails (exit 1) if a threshold is exceeded.
"""
from __future__ import annotations

import csv
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fundbridge import datasets  # noqa: E402
from fundbridge.config import RAW_DIR  # noqa: E402
from fundbridge.stores.csr_dataset import DatasetCSRStore  # noqa: E402

THRESHOLDS_S = {"top_funders": 2.0, "coverage": 0.5, "coverage_states": 2.0, "coverage_districts": 1.0,
                "funders_in": 1.0, "similar_states": 2.0, "company_profile": 0.5}


def synthesize(src: Path, dst: Path, scale: int, seed: int = 7) -> int:
    rnd = random.Random(seed)
    with src.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows, fields = list(reader), reader.fieldnames
    n = 0
    with dst.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for copy in range(scale):
            for r in rows:
                r2 = dict(r)
                if copy:
                    r2["company_name"] = f"Synthetic {copy} {r['company_name']}"
                    r2["cin"] = f"SYN{copy:02d}{r['cin'][-11:]}" if r.get("cin") else ""
                    r2["id"] = f"{copy}-{r['id']}"
                    for k in ("amount_spent_in_lakhs", "project_outlay_amount_in_lakhs"):
                        try:
                            r2[k] = f"{float(r[k]) * rnd.uniform(0.5, 1.5):.2f}"
                        except (KeyError, ValueError):
                            pass
                w.writerow(r2)
                n += 1
    return n


def timed(fn, repeat: int) -> list[float]:
    out = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return out


def run(scale: int = 10, repeat: int = 5, src: Path | None = None, cold: bool = True) -> dict:
    src = src or sorted(RAW_DIR.glob("*.csv"))[0]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        t0 = time.perf_counter()
        n = synthesize(src, tmp / "synthetic.csv", scale)
        root = tmp / "datasets"
        m = datasets.build_version([tmp / "synthetic.csv"], "SAMPLE", root=root, created_by="load_test")
        datasets.activate(m["version"], root)
        build_s = time.perf_counter() - t0
        store = DatasetCSRStore(root)
        states = [r["state_lgd"] for r in store.coverage_states()][:6] or ["8"]
        cid = store._q("SELECT company_id FROM @clean_companies LIMIT 1")[0]["company_id"]
        min_fy = store.latest_fy_start() - 4
        cases = {
            "top_funders": lambda: [store.top_funders(["education"], "Rajasthan", "Barmer", 5, 20),
                                    store.top_funders(["health", "rural development"], "Maharashtra", "", 5, 20)],
            "coverage": lambda: [store.coverage(s, ["education"]) for s in states],
            "coverage_states": lambda: store.coverage_states("education"),
            "coverage_districts": lambda: store.coverage_districts(states[0]),
            "funders_in": lambda: store.funders_in(["education"], min_fy, all_india=True),
            "similar_states": lambda: store.similar_states(states[-1], ["education"]),
            "company_profile": lambda: store.company_profile(cid),
        }
        results = {}
        for name, fn in cases.items():
            if cold:
                store._cache.clear()
            first = timed(fn, 1)[0]  # uncached
            warm = timed(fn, repeat)  # cached per dataset version
            results[name] = {"cold_s": round(first, 3), "warm_p50_s": round(statistics.median(warm), 4),
                             "warm_p95_s": round(sorted(warm)[max(0, int(len(warm) * 0.95) - 1)], 4),
                             "threshold_s": THRESHOLDS_S[name], "ok": first <= THRESHOLDS_S[name]}
        return {"rows_written": n, "rows_loaded": m["rows_loaded"], "companies": m["companies"], "build_s": round(build_s, 1),
                "queries": results, "ok": all(r["ok"] for r in results.values())}


if __name__ == "__main__":
    scale = int(sys.argv[sys.argv.index("--scale") + 1]) if "--scale" in sys.argv else 10
    rep = run(scale)
    print(f"{rep['rows_written']:,} synthetic rows ({scale}x) -> {rep['rows_loaded']:,} loaded, {rep['companies']:,} companies; "
          f"build {rep['build_s']} s")
    print(f"{'query':<20}{'cold s':>9}{'warm p50 s':>12}{'warm p95 s':>12}{'limit s':>9}  ok")
    for k, v in rep["queries"].items():
        print(f"{k:<20}{v['cold_s']:>9}{v['warm_p50_s']:>12}{v['warm_p95_s']:>12}{v['threshold_s']:>9}  {v['ok']}")
    sys.exit(0 if rep["ok"] else 1)

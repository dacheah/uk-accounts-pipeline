"""
regrade.py — re-score saved hard-benchmark results WITHOUT calling any model (free).

  1. Excludes genuinely-dormant companies from the turnover trap (where answering '0' is fine).
  2. Counts net-assets sign-flips (model returned -X for a +X truth) as a data-quality check.

Run from the repo root:  python tools/regrade.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
import config            # noqa: E402
import benchmark_grading as g  # noqa: E402
import pandas as pd      # noqa: E402

BDIR = config.OUT_DIR / "benchmark_hard_v1"
bench_path = BDIR / "benchmark.jsonl"
if not bench_path.exists():
    sys.exit(f"No benchmark at {bench_path}")

bench = {}
for line in open(bench_path, encoding="utf-8"):
    it = json.loads(line)
    bench[it["id"]] = {"cn": str(it["meta"]["company_number"]),
                       "field": it["meta"].get("field"), "category": it["category"]}

trap_cns = {v["cn"] for v in bench.values() if v["category"] == "turnover_trap"}

dirs = sorted(config.OUT_DIR.glob("product1_v*"))
parts = sorted(dirs[-1].glob("product1-*.parquet"))
dormant = {}
for p in parts:
    df = pd.read_parquet(p, columns=["company_number", "dormant"])
    df["company_number"] = df["company_number"].astype(str)
    sub = df[df["company_number"].isin(trap_cns)]
    for cn, dv in zip(sub["company_number"], sub["dormant"]):
        dormant[str(cn)] = bool(dv) if pd.notna(dv) else False

print(f"Trap companies: {len(trap_cns)} | confirmed dormant: {sum(1 for v in dormant.values() if v)}\n")

for rf in sorted(BDIR.glob("results_*.json")):
    data = json.loads(rf.read_text())
    detail = data["detail"]
    orig = data["summary"]["overall_accuracy"]
    by_cat, correct, scored, excluded, signflip = {}, 0, 0, 0, 0
    for d in detail:
        if d.get("errored"):
            continue
        cid, cat = d["id"], d["category"]
        cn = bench.get(cid, {}).get("cn", "")
        if cat == "turnover_trap" and dormant.get(cn, False):
            excluded += 1
            continue
        if cat == "extraction" and d.get("field") == "net_assets" and not d["correct"]:
            v = g.parse_number(str(d["model_answer"]))
            t = d["truth"]
            if v is not None and isinstance(t, (int, float)) and abs(v - (-t)) <= max(1.0, 0.01 * abs(t)):
                signflip += 1
        by_cat.setdefault(cat, [0, 0])
        by_cat[cat][1] += 1
        scored += 1
        if d["correct"]:
            by_cat[cat][0] += 1
            correct += 1
    print(f"{rf.name}")
    print(f"  original overall:           {orig:.1%}")
    print(f"  dormant-corrected overall:  {correct/scored:.1%}   (excluded {excluded} dormant trap items)")
    for c, v in by_cat.items():
        print(f"     {c:<14} {v[0]/v[1]:.1%}  (n={v[1]})")
    print(f"  net_assets sign-flips: {signflip}"
          + (f"  -> if credited: {(correct+signflip)/scored:.1%}" if signflip else ""))
    print()

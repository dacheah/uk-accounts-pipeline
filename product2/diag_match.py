"""Diagnostic: how many Product 2 targets appear in the Product 1 dataset, and in what
company-number format. Run:  python diag_match.py"""
import csv
import itertools
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import config  # noqa: E402
import pandas as pd  # noqa: E402

targets = [r["company_number"] for r in csv.DictReader(open(HERE / "targets.csv", encoding="utf-8"))]
d = sorted(config.OUT_DIR.glob("product1_v*"))[-1]
cn = set()
for p in d.glob("product1-*.parquet"):
    cn.update(pd.read_parquet(p, columns=["company_number"])["company_number"].astype(str))

hit = [x for x in targets if x in cn]
print(f"dataset: {d.name}")
print(f"targets: {len(targets)} | in product1: {len(hit)}")
print(f"target CN samples : {targets[:6]}")
print(f"product1 CN samples: {list(itertools.islice(cn, 6))}")
for probe in ["00947662", "08632552"]:   # Aldermore Bank PLC, Atom Bank PLC
    print(f"  {probe} in product1: {probe in cn}")

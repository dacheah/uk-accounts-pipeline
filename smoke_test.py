"""
smoke_test.py — validate the pipeline against ONE real, recent Companies House daily file.

What it does (on your machine, which has internet):
  1. downloads the smallest recent daily accounts ZIP (small, quick);
  2. parses + normalises the first N filings;
  3. prints coverage, the FRS-102/FRS-105 split, and the most common XBRL tags it saw.

Run it from inside the uk-accounts-pipeline folder:
    python smoke_test.py            (defaults to 50 filings)
    python smoke_test.py 200        (process 200)

Then copy the whole output back to Claude — the "most common tags" list lets Claude
confirm/extend the tag dictionaries against real data before the full run.
"""
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

import config          # noqa: E402
import download        # noqa: E402
import build_dataset   # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 50

config.ensure_dirs()
print("Step 1/2 — downloading a small recent daily accounts file ...")
zip_path = download.download_smallest_recent_accounts(config.RAW_DIR)

print(f"\nStep 2/2 — parsing the first {N} filings ...")
res = build_dataset.build(zip_path, company_csv=None, limit=N,
                          out_dir=config.OUT_DIR, raw_dir=config.RAW_DIR)
man = res["manifest"]

print("\n" + "=" * 60)
print(f"RESULT: parsed {res['records']} filings from {man['sources']['accounts']}")
print(f"CSV written to: {res['csv']}")
print("\nTaxonomy split:", json.dumps(man["taxonomy_distribution"]))
print("\nField coverage (share of filings with a value):")
for field, share in man["field_coverage"].items():
    print(f"   {field:<20} {share:6.1%}")
print("\nMost common XBRL tags observed (tag : count) — paste this back to Claude:")
for concept, count in man["top_concepts_observed"]:
    print(f"   {concept:<55} {count}")

print("\nThree sample records (real data) — paste these back too:")
import csv as _csv  # noqa: E402
with open(res["csv"], newline="", encoding="utf-8") as fh:
    rows = list(_csv.DictReader(fh))
show = ["company_number", "accounting_standard", "accounts_type", "audited", "dormant",
        "taxonomy", "turnover", "total_assets", "total_liabilities", "net_assets",
        "net_debt", "employees", "completeness_score"]
for r in rows[:3]:
    print("   " + json.dumps({k: r.get(k) for k in show}))

print("\nDiagnostic (first 6 filings) — raw employee + meta facts, paste this back:")
import parse_ixbrl  # noqa: E402
work = config.RAW_DIR / "_unzipped"
diag_files = [p for p in sorted(work.rglob("*")) if p.suffix.lower() in (".html", ".xml")][:6]
for p in diag_files:
    ex = parse_ixbrl.extract_facts(p)
    emp = [(f["concept"], f["raw_text"], f["value"], "scale=" + str(f.get("scale")), f.get("unit_ref"))
           for f in ex["facts"] if "employee" in f["concept"].lower()]
    meta = [(f["concept"], repr(f["raw_text"]))
            for f in ex["facts"]
            if f["concept"] in ("AccountsType", "AccountingStandardsApplied", "AccountsStatusAuditedOrUnaudited")]
    print(f"  {ex['company_number']}")
    print(f"     EMP : {emp}")
    print(f"     META: {meta}")
print("=" * 60)
print("Done. Copy everything above back to Claude.")

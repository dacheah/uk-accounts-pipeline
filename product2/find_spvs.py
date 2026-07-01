"""
find_spvs.py — find SPV / non-bank-lender company numbers via the Companies House search API.

Reads the curated seed lists (spvs.txt, lenders.txt), searches each term, then keeps only
companies whose NAME actually contains the searched term (the CH search is fuzzy and returns
many loosely-related hits). No personal data is kept (company-level fields only).

Run from inside the product2 folder (uses your API key from secrets.env):
    python find_spvs.py
Output -> <data>/processed/product2/candidates.csv  (name-matched, clean)
"""
from __future__ import annotations

import csv
import re
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import config  # noqa: E402


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def read_terms(path):
    return [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def search(term, key, n=100, _retry=0):
    url = f"{config.API_BASE}/search/companies"
    r = requests.get(url, params={"q": term, "items_per_page": n}, auth=(key, ""), timeout=30)
    if r.status_code == 429 and _retry < 5:
        time.sleep(8)
        return search(term, key, n, _retry + 1)
    r.raise_for_status()
    return r.json().get("items", [])


def main():
    key = config.get_api_key()
    config.ensure_dirs()
    rows = {}
    for category, fname in [("lender", "lenders.txt"), ("spv", "spvs.txt")]:
        for term in read_terms(HERE / fname):
            try:
                items = search(term, key)
            except Exception as e:
                print(f"  [error] {term}: {e}")
                continue
            kept = 0
            for it in items:
                cn = it.get("company_number")
                name = it.get("title")
                if not cn:
                    continue
                # keep only if the searched term actually appears in the company name
                if _norm(term) not in _norm(name):
                    continue
                kept += 1
                row = rows.setdefault(cn, {
                    "company_number": cn, "company_name": name,
                    "company_status": it.get("company_status"),
                    "company_type": it.get("company_type"),
                    "date_of_creation": it.get("date_of_creation"),
                    "category": category, "matched_terms": set()})
                row["matched_terms"].add(term)
            print(f"  {category:6} '{term}': {len(items)} hits, {kept} name-matched")
            time.sleep(0.2)

    out_dir = config.OUT_DIR / "product2"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "candidates.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["company_number", "company_name", "company_status", "company_type",
                    "date_of_creation", "category", "matched_terms"])
        for cn, r in sorted(rows.items(), key=lambda kv: (kv[1]["category"], kv[1]["company_name"] or "")):
            w.writerow([r["company_number"], r["company_name"], r["company_status"],
                        r["company_type"], r["date_of_creation"], r["category"],
                        "; ".join(sorted(r["matched_terms"]))])

    n_plc = sum(1 for r in rows.values() if (r["company_type"] or "").lower() == "plc")
    n_spv = sum(1 for r in rows.values() if r["category"] == "spv")
    n_lender = sum(1 for r in rows.values() if r["category"] == "lender")
    print(f"\nDONE: {len(rows)} name-matched candidates ({n_spv} spv, {n_lender} lender; {n_plc} PLCs)")
    print(f"  -> {path}")
    print("Open candidates.csv and sanity-check, then we pull charges + financials.")


if __name__ == "__main__":
    main()

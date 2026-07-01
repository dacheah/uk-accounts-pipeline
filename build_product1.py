"""
build_product1.py — produce the full Product 1 dataset from the trailing monthly archives.

Designed for an unattended multi-hour run on your PC:
  • RESUMABLE   — each month is written as its own Parquet "part"; re-running skips
                  months already done, so a crash never costs you the whole run.
  • PARALLEL    — parses filings across CPU cores (default: cores - 1).
  • MEMORY-SAFE — only one month is held in memory at a time; one month is extracted,
                  parsed, written, then deleted before the next.

Typical use (from inside the uk-accounts-pipeline folder):
    python build_product1.py --trial                 # 1 month, capped — a quick test
    python build_product1.py --months 12 --workers 8 # the full run

Output goes to data/processed/product1_v<date>/  (Parquet parts + schema + manifest).
The dataset is kept PRIVATE (local) — we do not publish it.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
sys.path.insert(0, str(SRC))

import config          # noqa: E402
import download        # noqa: E402
import parse_ixbrl     # noqa: E402
import normalise       # noqa: E402
import metadata        # noqa: E402
import build_dataset   # noqa: E402

META_COLS = ["company_name", "company_type", "company_status", "sic_codes",
             "incorporation_date", "region_outcode", "post_town", "country", "accounts_category"]


def _parse_one(path_str):
    """Worker: parse + normalise one filing into a flat record (or None on failure)."""
    try:
        ex = parse_ixbrl.extract_facts(path_str)
        rec = normalise.normalise(ex)
        rec["source_file"] = os.path.basename(path_str)
        return build_dataset._flatten(rec)
    except Exception:
        return None


def process_month(zip_path, parts_dir, workers=4, limit=None):
    """Parse one monthly archive into parts_dir/part-<month>.parquet (financials only)."""
    import pandas as pd
    month = Path(zip_path).stem.replace("Accounts_Monthly_Data-", "")
    part_path = parts_dir / f"part-{month}.parquet"
    if part_path.exists():
        print(f"  [skip] {month} — part already exists")
        return

    work = config.RAW_DIR / "_work"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    print(f"  extracting {Path(zip_path).name} ...")
    build_dataset.extract_zip(zip_path, work)
    files = [str(p) for p in build_dataset.iter_filings(work)]
    if limit:
        files = files[:limit]
    print(f"  parsing {len(files):,} filings with {workers} worker(s) ...")

    records, t0 = [], time.time()
    if workers and workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, flat in enumerate(ex.map(_parse_one, files, chunksize=200), 1):
                if flat is not None:
                    records.append(flat)
                if i % 25000 == 0:
                    print(f"    {i:,}/{len(files):,} ...")
    else:
        for i, fp in enumerate(files, 1):
            flat = _parse_one(fp)
            if flat is not None:
                records.append(flat)
            if i % 25000 == 0:
                print(f"    {i:,}/{len(files):,} ...")

    parts_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_parquet(part_path, index=False)
    shutil.rmtree(work, ignore_errors=True)
    print(f"  wrote {len(records):,} records -> {part_path.name} in {time.time()-t0:.0f}s")


def finalize(parts_dir, out_dir, sources, with_metadata=True):
    """Join metadata (no personal data) and write the versioned dataset + docs."""
    import pandas as pd
    parts = sorted(parts_dir.glob("part-*.parquet"))
    if not parts:
        print("  no parts found — nothing to finalize")
        return

    # company numbers across all parts
    nums = set()
    for p in parts:
        s = pd.read_parquet(p, columns=["company_number"])["company_number"].dropna().astype(str)
        nums.update(metadata.normalise_number(x) for x in s)
    print(f"  {len(nums):,} unique companies across {len(parts)} part(s)")

    meta_df = None
    company_csv = None
    if with_metadata:
        company_csv = download.download_company_data_csv(config.RAW_DIR)
        print(f"  building metadata from {Path(company_csv).name} (one streaming pass) ...")
        meta = metadata.stream_company_metadata(company_csv, nums)
        rows = [{"company_number": k,
                 **{c: ("; ".join(v[c]) if isinstance(v.get(c), list) else v.get(c)) for c in META_COLS}}
                for k, v in meta.items()]
        meta_df = pd.DataFrame(rows) if rows else None
        print(f"  metadata matched for {len(rows):,} companies")

    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    coverage_fields = ["turnover", "gross_profit", "operating_profit", "total_assets",
                       "total_liabilities", "net_assets", "cash", "net_debt", "employees",
                       "property_plant_equipment", "total_assets_less_current_liabilities"]
    cov_hits = {f: 0 for f in coverage_fields}
    tax_dist, type_dist = {}, {}
    sample_written = False

    for p in parts:
        df = pd.read_parquet(p)
        df["company_number"] = df["company_number"].map(
            lambda n: metadata.normalise_number(str(n)) if pd.notna(n) else n)
        if meta_df is not None:
            df = df.merge(meta_df, on="company_number", how="left")
        df.to_parquet(out_dir / p.name.replace("part-", "product1-"), index=False)
        total += len(df)
        for f in coverage_fields:
            if f in df.columns:
                cov_hits[f] += int(df[f].notna().sum())
        for k, v in df.get("taxonomy", pd.Series(dtype=str)).value_counts().items():
            tax_dist[k] = tax_dist.get(k, 0) + int(v)
        for k, v in df.get("accounts_type", pd.Series(dtype=str)).value_counts().items():
            type_dist[k] = type_dist.get(k, 0) + int(v)
        if not sample_written:
            df.head(5000).to_csv(out_dir / "sample_preview.csv", index=False)
            sample_written = True

    coverage = {f: round(cov_hits[f] / total, 4) if total else 0 for f in coverage_fields}
    manifest = {
        "product": "Product 1 — UK company financial statements",
        "version": out_dir.name,
        "generated": datetime.date.today().isoformat(),
        "record_count": total,
        "sources": sources,
        "publisher": "Companies House",
        "licence": config.OGL_ATTRIBUTION,
        "processing_steps": [
            "download trailing monthly Accounts Data Product archives",
            "extract iXBRL filings (incl. nested zips)",
            "parse tagged facts; normalise FRS-102/FRS-105 to one schema; derive totals (provenance recorded)",
            "join Company Data CSV metadata, EXCLUDING personal data (coarse region only)",
            "write versioned Parquet dataset + schema + this manifest",
        ],
        "personal_data": "excluded by design — no names, dates of birth, or address lines; coarse region (outward postcode + town) only",
        "taxonomy_distribution": tax_dist,
        "accounts_type_distribution": type_dist,
        "field_coverage": coverage,
    }
    (out_dir / "provenance_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / "schema.md").write_text(_schema_md(), encoding="utf-8")
    (out_dir / "coverage_report.json").write_text(json.dumps(
        {"record_count": total, "field_coverage": coverage,
         "taxonomy_distribution": tax_dist, "accounts_type_distribution": type_dist}, indent=2), encoding="utf-8")
    print(f"\n  DONE: {total:,} records -> {out_dir}")
    print("  field coverage:", json.dumps(coverage))


def _schema_md():
    return """# Product 1 — Schema

One row = one company's most recent set of accounts in the period covered.
All monetary values are in the filing's reporting currency (see `currency`), in absolute units.

## Identity & period
`company_number`, `taxonomy` (FRS-102 / FRS-105 / IFRS), `taxonomy_version`,
`accounting_standard`, `currency`, `period_start`, `period_end`, `period_length_days`.

## Core financials
`turnover`, `gross_profit`, `total_assets`, `total_liabilities`, `net_assets`, `employees`.

## Extended statement detail
`operating_profit`, `interest_payable`, `interest_receivable`, `profit_before_tax`, `tax`,
`profit_for_year`, `dividends`, `staff_costs`, `provisions`, `property_plant_equipment`,
`current_assets`, `debtors`, `net_current_assets`, `total_assets_less_current_liabilities`,
`cash`, `net_debt`.

## Funding structure (by maturity)
`f_within_*` and `f_after_*` for: bank_loans, other_loans, finance_leases, trade_creditors,
intercompany, director_loans, tax_social_security, accruals_deferred, other_creditors,
creditors_total.

## Filing-behaviour & quality
`accounts_type`, `filleted`, `audited`, `dormant`, `completeness_score`, `provenance`
(JSON: per-figure reported/derived/summed), `source_file`.

## Company metadata (no personal data)
`company_name`, `company_type`, `company_status`, `sic_codes`, `incorporation_date`,
`region_outcode` (outward postcode only), `post_town`, `country`, `accounts_category`.

## Derivations & provenance
`total_assets` = reported, else fixed+current, else (TALCL - net current) + current.
`total_liabilities` = total assets - net assets, else creditors + provisions.
`net_debt` = interest-bearing debt - cash (negative = net cash). `employees` read from
raw text (XBRL scale ignored, to avoid a known mis-scaling quirk). Each figure's method is
recorded in `provenance`.

## Licence
Contains public sector information licensed under the Open Government Licence v3.0.
Source: Companies House.
"""


def run(months=12, workers=4, trial=False, limit=None, with_metadata=True):
    config.ensure_dirs()
    suffix = ""
    if trial:
        months, limit, with_metadata = 1, (limit or 3000), False
        suffix = "_trial"  # keep capped trial parts/outputs separate from the full run
        print("TRIAL MODE: 1 month, capped, no metadata download. (Isolated from the full run.)\n")

    print("Stage 1 — downloading monthly archives ...")
    zips = download.download_monthly_trailing(config.RAW_DIR, months=months)

    parts_dir = config.OUT_DIR / ("_parts" + suffix)
    print("\nStage 2 — parsing each month (resumable) ...")
    for z in zips:
        process_month(z, parts_dir, workers=workers, limit=limit)

    out_dir = config.OUT_DIR / f"product1{suffix}_v{datetime.date.today().isoformat()}"
    print("\nStage 3 — metadata join + finalize ...")
    finalize(parts_dir, out_dir, sources={"monthly_files": [Path(z).name for z in zips]},
             with_metadata=with_metadata)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the full Product 1 dataset.")
    ap.add_argument("--months", type=int, default=12, help="trailing monthly archives to use")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--limit", type=int, default=None, help="cap filings per month (testing)")
    ap.add_argument("--trial", action="store_true", help="quick 1-month capped trial, no metadata")
    ap.add_argument("--no-metadata", action="store_true", help="skip the company metadata join")
    a = ap.parse_args()
    run(months=a.months, workers=a.workers, trial=a.trial,
        limit=a.limit, with_metadata=not a.no_metadata)

"""
build_dataset.py — orchestrate the whole pipeline end to end.

  unzip bulk accounts  ->  parse each iXBRL  ->  normalise to one schema
  ->  (optionally) join company metadata, excluding personal data
  ->  write CSV + Parquet + a provenance manifest.

Run from the command line, e.g.:
    python src/build_dataset.py --accounts-zip data/raw/Accounts_Bulk_Data-2026-06-27.zip \
                                --company-csv data/raw/BasicCompanyDataAsOneFile-2026-06-01.csv \
                                --limit 5000
"""
from __future__ import annotations

import argparse
import csv as _csv
import datetime
import json
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import parse_ixbrl
import normalise
try:
    import metadata
except Exception:  # pragma: no cover
    metadata = None
try:
    import config
    RAW_DIR, OUT_DIR, OGL = config.RAW_DIR, config.OUT_DIR, config.OGL_ATTRIBUTION
except Exception:  # pragma: no cover
    RAW_DIR = HERE.parent / "data" / "raw"
    OUT_DIR = HERE.parent / "data" / "processed"
    OGL = ("Contains public sector information licensed under the "
           "Open Government Licence v3.0. Source: Companies House.")


def extract_zip(zip_path, dest):
    """Unzip, then unzip any nested ZIPs (some daily files wrap iXBRL in inner ZIPs)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    for nested in list(dest.rglob("*.zip")):
        try:
            with zipfile.ZipFile(nested) as z:
                z.extractall(nested.parent)
            nested.unlink()
        except zipfile.BadZipFile:
            pass
    return dest


def iter_filings(folder):
    for p in sorted(Path(folder).rglob("*")):
        if p.is_file() and p.suffix.lower() in (".html", ".xml"):
            yield p


def _flatten(record: dict) -> dict:
    """Flatten the nested funding dicts into columns for tabular output."""
    out = {}
    for k, v in record.items():
        if k in ("funding_within_one_year", "funding_after_one_year") and isinstance(v, dict):
            prefix = "f_within" if "within" in k else "f_after"
            for kk, vv in v.items():
                out[f"{prefix}_{kk}"] = vv
        elif k == "provenance":
            out["provenance"] = json.dumps(v)
        elif k == "sic_codes" and isinstance(v, list):
            out["sic_codes"] = "; ".join(v)
        else:
            out[k] = v
    return out


def _write_csv(rows, path):
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _manifest(accounts_zip, company_csv, records, concept_counter, stamp):
    tax = {}
    for r in records:
        tax[r["taxonomy"]] = tax.get(r["taxonomy"], 0) + 1
    top = sorted(concept_counter.items(), key=lambda kv: kv[1], reverse=True)[:40]
    cov = {}
    for f in ["turnover", "gross_profit", "operating_profit", "total_assets",
              "total_liabilities", "net_assets", "cash", "net_debt", "employees"]:
        cov[f] = round(sum(1 for r in records if r.get(f) is not None) / max(len(records), 1), 3)
    return {
        "product": "UK company financial statements (sample)",
        "generated": stamp,
        "sources": {
            "accounts": Path(accounts_zip).name,
            "company_metadata": Path(company_csv).name if company_csv else None,
            "publisher": "Companies House",
        },
        "licence": OGL,
        "processing_steps": [
            "download bulk iXBRL accounts (Accounts Data Product)",
            "extract tagged numeric facts (parse_ixbrl)",
            "normalise FRS-102/FRS-105 to one schema; derive totals (normalise)",
            "join company metadata from Company Data CSV, excluding personal data (metadata)",
            "write CSV + Parquet + this manifest",
        ],
        "personal_data": "excluded by design (no names, DOBs, or address lines; coarse region only)",
        "record_count": len(records),
        "taxonomy_distribution": tax,
        "field_coverage": cov,
        "top_concepts_observed": top,
    }


def build(accounts_zip, company_csv=None, limit=None, out_dir=OUT_DIR, raw_dir=RAW_DIR):
    work = Path(raw_dir) / "_unzipped"
    if work.exists():
        shutil.rmtree(work)
    extract_zip(accounts_zip, work)

    records, concept_counter, n = [], {}, 0
    for f in iter_filings(work):
        try:
            extracted = parse_ixbrl.extract_facts(f)
        except Exception:
            continue
        rec = normalise.normalise(extracted)
        rec["source_file"] = f.name
        records.append(rec)
        for fact in extracted["facts"]:
            concept_counter[fact["concept"]] = concept_counter.get(fact["concept"], 0) + 1
        n += 1
        if limit and n >= limit:
            break

    if company_csv and metadata:
        wanted = {r["company_number"] for r in records if r["company_number"]}
        meta = metadata.stream_company_metadata(company_csv, wanted)
        for r in records:
            m = meta.get(r["company_number"]) if r["company_number"] else None
            if m:
                for k in ("company_name", "company_type", "company_status", "sic_codes",
                          "incorporation_date", "region_outcode", "post_town", "country",
                          "accounts_category"):
                    if k in m:
                        r[k] = m[k]

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.date.today().isoformat()
    flat = [_flatten(r) for r in records]

    csv_path = out_dir / f"uk_accounts_sample_{stamp}.csv"
    _write_csv(flat, csv_path)
    parquet_path = None
    try:
        import pandas as pd
        parquet_path = out_dir / f"uk_accounts_sample_{stamp}.parquet"
        pd.DataFrame(flat).to_parquet(parquet_path, index=False)
    except Exception:
        parquet_path = None

    manifest = _manifest(accounts_zip, company_csv, records, concept_counter, stamp)
    (out_dir / f"provenance_manifest_{stamp}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"records": len(records), "csv": str(csv_path),
            "parquet": str(parquet_path) if parquet_path else None, "manifest": manifest}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the UK accounts dataset from a bulk accounts ZIP.")
    ap.add_argument("--accounts-zip", required=True)
    ap.add_argument("--company-csv", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args()
    res = build(args.accounts_zip, args.company_csv, args.limit, args.out_dir)
    print(json.dumps({k: v for k, v in res.items() if k != "manifest"}, indent=2))
    print("field coverage:", json.dumps(res["manifest"]["field_coverage"]))
    print("taxonomy:", json.dumps(res["manifest"]["taxonomy_distribution"]))

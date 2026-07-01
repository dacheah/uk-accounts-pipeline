"""
build_product2.py — build the Product 2 datasets from the curated target list.

Reads product2/targets.csv (your reviewed SPVs + lenders), then for each company:
  • pulls the charges register from the Companies House API and annotates it into
    structured structured-finance fields;
  • joins the company's financials from the Product 1 dataset.

Outputs -> <data>/processed/product2/
  spv_charges.csv          one row per charge (secured-lending entries), annotated
  entities.csv             one row per entity: metadata + financials + charge summary
  provenance_manifest.json

GDPR: charge "persons entitled" are the secured parties (trustees/banks — corporate).
Any name lacking a corporate suffix is treated as a possible individual and redacted.

Run from inside product2 (uses your API key):  python build_product2.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import config  # noqa: E402

CORP_TOKENS = ("LIMITED", "LTD", "PLC", "LLP", " LP", "N.A", " NA", "TRUSTEE", "TRUST", "BANK",
               "CORPORATION", "COMPANY", "SERVICES", "S.A", " AG", "GMBH", "INC", "B.V", "N.V",
               "NOMINEE", "SECURITIES", "CAPITAL", "FINANCE", "FUNDING", "HOLDINGS", "GROUP",
               "ASSOCIATION", "SOCIETY", "PARTNERS", "MELLON", "CITI", "HSBC", "BARCLAYS", "GLAS")


def get_charges(cn, key):
    items, start = [], 0
    while True:
        r = requests.get(f"{config.API_BASE}/company/{cn}/charges",
                         params={"items_per_page": 100, "start_index": start}, auth=(key, ""), timeout=30)
        if r.status_code == 404:
            return []
        if r.status_code == 429:
            time.sleep(8)
            continue
        r.raise_for_status()
        j = r.json()
        batch = j.get("items", [])
        items += batch
        start += len(batch)
        if not batch or start >= j.get("total_count", len(items)):
            break
    return items


def redact_persons(persons):
    out, has_individual = [], False
    for p in persons or []:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        if any(tok in name.upper() for tok in CORP_TOKENS):
            out.append(name)
        else:
            out.append("[individual redacted]")
            has_individual = True
    return "; ".join(out), has_individual


def shelf_of(name):
    n = re.sub(r"\b(PLC|LIMITED|LTD)\b", "", (name or "").upper())
    n = re.sub(r"\b20\d{2}[- ]?[0-9A-Z]*\b", "", n)      # 2021-1, 2025-GR1
    n = re.sub(r"\bNO\.?\s*\d+\b", "", n)                 # NO.5
    n = re.sub(r"\b(SERIES|FUNDING|MASTER ISSUER)\b", r"\1", n)
    return re.sub(r"\s+", " ", n).strip(" -")


def annotate_charge(entity, ch):
    part = ch.get("particulars") or {}
    sec = ch.get("secured_details") or {}
    persons, has_ind = redact_persons(ch.get("persons_entitled"))
    return {
        "company_number": entity["company_number"],
        "company_name": entity["company_name"],
        "category": entity["category"],
        "asset_class": entity.get("asset_class_guess"),
        "shelf": shelf_of(entity["company_name"]),
        "charge_code": ch.get("charge_code"),
        "charge_number": ch.get("charge_number"),
        "status": ch.get("status"),
        "created_on": ch.get("created_on"),
        "delivered_on": ch.get("delivered_on"),
        "satisfied_on": ch.get("satisfied_on"),
        "classification": (ch.get("classification") or {}).get("description"),
        "secured_details": sec.get("description"),
        "particulars": part.get("description"),
        "contains_fixed_charge": part.get("contains_fixed_charge"),
        "contains_floating_charge": part.get("contains_floating_charge"),
        "contains_negative_pledge": part.get("contains_negative_pledge"),
        "persons_entitled": persons,          # secured parties (trustees/banks)
        "persons_entitled_has_individual": has_ind,
    }


def load_targets():
    with open(HERE / "targets.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main():
    key = config.get_api_key()
    config.ensure_dirs()
    targets = load_targets()
    print(f"Targets: {len(targets)} entities")

    out_dir = config.OUT_DIR / "product2"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "charges_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    charge_rows, summary = [], {}
    for i, ent in enumerate(targets, 1):
        cn = ent["company_number"]
        if cn in cache:
            charges = cache[cn]
        else:
            try:
                charges = get_charges(cn, key)
            except Exception as e:
                print(f"  [error] {cn} {ent['company_name']}: {e}")
                charges = []
            cache[cn] = charges
            time.sleep(0.25)
        outstanding = sum(1 for c in charges if c.get("status") == "outstanding")
        summary[cn] = {"charges_total": len(charges), "charges_outstanding": outstanding}
        for ch in charges:
            charge_rows.append(annotate_charge(ent, ch))
        if i % 50 == 0:
            print(f"  {i}/{len(targets)} entities, {len(charge_rows)} charges so far ...")
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    # charges dataset
    charge_cols = list(charge_rows[0].keys()) if charge_rows else []
    with open(out_dir / "spv_charges.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=charge_cols)
        w.writeheader()
        w.writerows(charge_rows)

    # entities + charge summary (financials deferred: SPVs/banks file PDF audited accounts
    # that are not in the free iXBRL bulk product, so structured financials aren't available)
    ent_cols = ["company_number", "company_name", "company_status", "company_type",
                "date_of_creation", "category", "refined_entity_type", "asset_class_guess",
                "strict_non_bank_lender_scope", "shelf", "charges_total", "charges_outstanding"]
    with open(out_dir / "entities.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=ent_cols, extrasaction="ignore")
        w.writeheader()
        for t in targets:
            row = dict(t)
            row["shelf"] = shelf_of(t["company_name"]) if t.get("category") == "spv" else ""
            row.update(summary.get(t["company_number"], {}))
            w.writerow(row)

    manifest = {
        "product": "Product 2 — UK securitisation SPVs & non-bank lenders (charges / SPV map)",
        "generated": date.today().isoformat(),
        "entities": len(targets),
        "charges": len(charge_rows),
        "sources": {"targets": "curated Companies House review",
                    "charges": "Companies House REST API", "publisher": "Companies House"},
        "licence": config.OGL_ATTRIBUTION,
        "financials_note": "Financials companion deferred: SPVs and banks file PDF audited "
                           "accounts not present in the free iXBRL bulk product.",
        "personal_data": "charge persons-entitled are corporate secured parties; possible "
                         "individuals are redacted; no director/PSC data included.",
    }
    (out_dir / "provenance_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nDONE: {len(targets)} entities, {len(charge_rows)} charges -> {out_dir}")


if __name__ == "__main__":
    main()

"""
metadata.py — attach company metadata from the free Company Data CSV, EXCLUDING personal data.

The Company Data Product is one big CSV (≈5m+ rows). Rather than load it all into memory,
we stream it once and keep only the rows whose company number we actually need.

GDPR stance: we deliberately DROP the registered-office address lines (a registered office
can be a home address). We keep only a coarse geography — the outward postcode (e.g. "EC1A"),
the post town, and country — plus non-personal company facts (type, status, SIC, dates).
"""
from __future__ import annotations

import csv

# Company-level fields we keep (output name -> possible CSV column names)
KEEP = {
    "company_name": ["CompanyName"],
    "company_type": ["CompanyCategory"],
    "company_status": ["CompanyStatus"],
    "incorporation_date": ["IncorporationDate"],
    "dissolution_date": ["DissolutionDate"],
    "accounts_category": ["Accounts.AccountCategory"],
}
SIC_COLS = ["SICCode.SicText_1", "SICCode.SicText_2", "SICCode.SicText_3", "SICCode.SicText_4"]

# For reference / auditability: columns we intentionally never keep
PERSONAL_DATA_EXCLUDED = (
    "RegAddress.CareOf", "RegAddress.POBox",
    "RegAddress.AddressLine1", "RegAddress.AddressLine2",
)


def normalise_number(num: str) -> str:
    """Companies House numbers are 8 chars; numeric ones are zero-padded (e.g. 1234 -> 00001234)."""
    n = (num or "").strip().upper()
    return n.zfill(8) if n.isdigit() else n


def outward_postcode(postcode: str):
    """Keep only the outward code — 'EC1A 1BB' -> 'EC1A' — coarse geography, not an address."""
    pc = (postcode or "").strip()
    return pc.split(" ")[0] if pc else None


def stream_company_metadata(csv_path, wanted) -> dict:
    """One streaming pass over the (large) CSV. Returns {company_number: cleaned_metadata}.

    The real Companies House CSV has leading spaces in some header names, so we strip keys.
    """
    wanted = {normalise_number(w) for w in wanted if w}
    out: dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
        for raw in csv.DictReader(fh):
            row = {(k.strip() if k else k): v for k, v in raw.items()}
            num = normalise_number(row.get("CompanyNumber"))
            if num not in wanted:
                continue
            rec = {"company_number": num}
            for out_key, candidates in KEEP.items():
                for col in candidates:
                    if row.get(col):
                        rec[out_key] = row[col]
                        break
            rec["sic_codes"] = [row[c] for c in SIC_COLS if row.get(c)]
            rec["region_outcode"] = outward_postcode(row.get("RegAddress.PostCode"))
            rec["post_town"] = row.get("RegAddress.PostTown")
            rec["country"] = row.get("RegAddress.Country")
            out[num] = rec
            if len(out) == len(wanted):
                break  # found everyone we needed
    return out

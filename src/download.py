"""
download.py — fetch Companies House bulk files.

Runs where there is internet access (your PC). Two free products:
  • Accounts Data Product  — daily ZIPs of iXBRL filings  (Accounts_Bulk_Data-YYYY-MM-DD.zip)
  • Company Data Product    — monthly CSV snapshot          (BasicCompanyDataAsOneFile-YYYY-MM-DD.zip)

We read the public index pages, then download by streaming to disk with a progress read-out.
"""
from __future__ import annotations

import re
from pathlib import Path

import requests

BASE = "https://download.companieshouse.gov.uk/"
ACCOUNTS_INDEX = BASE + "en_accountsdata.html"
COMPANY_INDEX = BASE + "en_output.html"
UA = {"User-Agent": "uk-accounts-pipeline/0.1 (research; OGL data)"}

_NAME_SIZE = re.compile(r"(Accounts_Bulk_Data-(\d{4}-\d{2}-\d{2})\.zip)\s*\(([\d.]+)\s*MiB\)", re.I)
_NAME = re.compile(r"(Accounts_Bulk_Data-(\d{4}-\d{2}-\d{2})\.zip)", re.I)
_CD = re.compile(r"(BasicCompanyDataAsOneFile-(\d{4}-\d{2}-\d{2})\.zip)", re.I)


def _get(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    return r.text


def parse_accounts_index(html: str) -> list[dict]:
    """Return [{name, date, size_mb, url}, ...] sorted oldest-first."""
    files: dict[str, dict] = {}
    for name, date, size in _NAME_SIZE.findall(html):
        files[name] = {"name": name, "date": date, "size_mb": float(size), "url": BASE + name}
    for name, date in _NAME.findall(html):  # any without a parsed size
        files.setdefault(name, {"name": name, "date": date, "size_mb": None, "url": BASE + name})
    return sorted(files.values(), key=lambda f: f["date"])


def parse_company_index(html: str) -> list[dict]:
    files: dict[str, dict] = {}
    for name, date in _CD.findall(html):
        files[name] = {"name": name, "date": date, "url": BASE + name}
    return sorted(files.values(), key=lambda f: f["date"])


def list_accounts_files() -> list[dict]:
    return parse_accounts_index(_get(ACCOUNTS_INDEX))


def list_company_data_files() -> list[dict]:
    return parse_company_index(_get(COMPANY_INDEX))


def pick_smallest_recent(files: list[dict], recent: int = 15) -> dict:
    """From the most recent `recent` daily files, choose the smallest — ideal for a quick test."""
    pool = files[-recent:] if len(files) > recent else files
    sized = [f for f in pool if f["size_mb"] is not None]
    return min(sized or pool, key=lambda f: f.get("size_mb") or 1e9)


def download(url: str, dest, chunk: int = 1 << 20):
    """Stream a file to `dest` with a progress read-out. Resumes are not attempted; a
    partial download is written to <dest>.part and renamed only on success."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  already have {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
        return dest
    print(f"  downloading {url}")
    with requests.get(url, headers=UA, stream=True, timeout=180) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        got = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as fh:
            for block in r.iter_content(chunk):
                fh.write(block)
                got += len(block)
                if total:
                    print(f"\r  {got*100/total:5.1f}%  {got/1e6:7.1f}/{total/1e6:.1f} MB", end="", flush=True)
        print()
        tmp.rename(dest)
    return dest


def download_smallest_recent_accounts(dest_dir, recent: int = 15):
    f = pick_smallest_recent(list_accounts_files(), recent)
    print(f"  chosen daily file: {f['name']} ({f['size_mb']} MiB)")
    return download(f["url"], Path(dest_dir) / f["name"])


def download_latest_accounts(dest_dir):
    f = list_accounts_files()[-1]
    return download(f["url"], Path(dest_dir) / f["name"])


def download_company_data_onefile(dest_dir):
    f = list_company_data_files()[-1]
    return download(f["url"], Path(dest_dir) / f["name"])


# ── Monthly accounts files (the trailing-year corpus) ────────────────────────
MONTHLY_INDEX = BASE + "en_monthlyaccountsdata.html"
_MONTHLY = re.compile(r"(Accounts_Monthly_Data-([A-Za-z]+)(\d{4})\.zip)", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"], start=1)}


def parse_monthly_index(html):
    files = {}
    for name, month, year in _MONTHLY.findall(html):
        key = (int(year), _MONTHS.get(month.lower(), 0))
        files[name] = {"name": name, "year": int(year), "month": month, "sort": key, "url": BASE + name}
    return sorted(files.values(), key=lambda f: f["sort"])


def list_monthly_accounts_files():
    return parse_monthly_index(_get(MONTHLY_INDEX))


def download_monthly_trailing(dest_dir, months=12):
    """Download the most recent `months` monthly archives. Returns list of local paths."""
    files = list_monthly_accounts_files()[-months:]
    print(f"  {len(files)} monthly files to fetch: {[f['name'] for f in files]}")
    paths = []
    for f in files:
        paths.append(download(f["url"], Path(dest_dir) / f["name"]))
    return paths


def download_company_data_csv(dest_dir):
    """Download the Company Data one-file ZIP and extract the CSV inside it. Returns the CSV path."""
    import zipfile
    zip_path = download_company_data_onefile(dest_dir)
    with zipfile.ZipFile(zip_path) as z:
        csv_name = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
        if not csv_name:
            raise RuntimeError("No CSV found inside company data zip")
        out = Path(dest_dir) / Path(csv_name).name
        if not out.exists():
            with z.open(csv_name) as src, open(out, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
    return out

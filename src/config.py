"""
Central configuration for the UK accounts pipeline.

Reads two settings from a local 'secrets.env' file (gitignored, never published):
  • CH_API_KEY   — your Companies House API key (used by Product 2 / spot checks)
  • CH_DATA_DIR  — where to store downloads + datasets (e.g. a big drive like D:)

If CH_DATA_DIR is not set, data goes under the project's own 'data/' folder.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_secrets_file() -> None:
    """Load KEY=VALUE lines from secrets.env into the environment, if the file exists."""
    secrets_path = PROJECT_ROOT / "secrets.env"
    if not secrets_path.exists():
        return
    for raw_line in secrets_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        os.environ.setdefault(name.strip(), value.strip())


# Load settings at import so CH_DATA_DIR is known before we compute the data folders.
_load_secrets_file()

# Data folders — point CH_DATA_DIR at a big drive (e.g. D:\UKHouseData) to keep large
# downloads + datasets off the C: drive. Falls back to <project>/data if unset.
_data_root = os.environ.get("CH_DATA_DIR", "").strip()
DATA_DIR = Path(_data_root) if _data_root else (PROJECT_ROOT / "data")
RAW_DIR = DATA_DIR / "raw"
OUT_DIR = DATA_DIR / "processed"

# Companies House sources (all free)
ACCOUNTS_DAILY_INDEX = "https://download.companieshouse.gov.uk/en_accountsdata.html"
ACCOUNTS_MONTHLY_INDEX = "https://download.companieshouse.gov.uk/en_monthlyaccountsdata.html"
COMPANY_DATA_INDEX = "https://download.companieshouse.gov.uk/en_output.html"
API_BASE = "https://api.company-information.service.gov.uk"

# Standard Open Government Licence attribution — carried on every output we produce
OGL_ATTRIBUTION = (
    "Contains public sector information licensed under the "
    "Open Government Licence v3.0. Source: Companies House."
)


def get_api_key() -> str:
    """Return the Companies House API key, or raise a clear, friendly error if missing."""
    key = os.environ.get("CH_API_KEY", "").strip()
    if not key or key == "your-companies-house-api-key-here":
        raise RuntimeError(
            "No Companies House API key found.\n"
            "Fix: copy config.example.env to secrets.env and paste your key "
            "after CH_API_KEY=, then save."
        )
    return key


def ensure_dirs() -> None:
    """Create the data folders if they do not already exist."""
    for d in (DATA_DIR, RAW_DIR, OUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print(f"PROJECT_ROOT : {PROJECT_ROOT}")
    print(f"DATA_DIR     : {DATA_DIR}")
    print(f"RAW_DIR      : {RAW_DIR}")
    print(f"OUT_DIR      : {OUT_DIR}")

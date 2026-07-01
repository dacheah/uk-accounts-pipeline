# Data Quality & Known Limitations

*Honest documentation for datasets produced by this pipeline. Source: Companies House,
reused under the Open Government Licence v3.0 — "Contains public sector information licensed
under the Open Government Licence v3.0."*

Limitations are stated openly because the buyers who matter trust data more when its edges are
documented than when they're hidden.

## Privacy (GDPR)
No personal data. Director/secretary/PSC names, dates of birth, nationalities, and address
lines are never extracted; only tagged numeric/identifier facts are read, and the metadata
join drops all address lines. Geography is kept only as the outward postcode (e.g. "EC1A") and
post town — a registered office can be a home address.

## Coverage (a feature, not just a caveat)
Coverage reflects what UK companies actually file:
net assets ~99%, total liabilities ~81%, total assets ~73–80%, employees ~85%, cash/net debt
~41%; **turnover ~2%, profit ~1%** because most UK companies file *filleted* or *micro-entity*
accounts that legally omit the profit-and-loss account.

## Derivations & provenance
Every figure carries a `provenance` tag (`reported` / `summed` / `derived` + formula):
- **Total assets:** reported, else fixed + current, else (TALCL − net current) + current.
- **Total liabilities:** total assets − net assets, else creditors + provisions.
- **Net debt:** interest-bearing debt − cash (negative = net cash).
- **Employees:** the displayed integer, ignoring XBRL scale (some filers wrongly scale a count).
- **Negatives:** net liabilities shown as "( 13,597 )" with parentheses *outside* the tag are
  detected and signed correctly.

## Known limitations
1. **Total-assets understatement** where a company holds intangibles/investments not captured
   by the fixed+current derivation; treat reported lines as primary, derived totals as indicative.
2. **Rolling source window** — the free bulk product covers a rolling ~12 months; deeper history
   requires accumulating monthly files.
3. **Electronic filings only** — ~60–75% of filings; PDF-filed accounts (large PLCs, banks, and
   securitisation SPVs) are out of scope. This is why Product 2's entities have no financials here.
4. **Metadata match ~97%** — the remainder have financials but no joined SIC/region/status.

## Reproducibility
Each build is a dated, versioned folder with a `provenance_manifest.json` and `schema.md`, and
the full pipeline is source-available, so any figure can be traced from dataset → filing.

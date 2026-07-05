"""
normalise.py — turn raw extracted facts into ONE clean, richly-structured record.

Tuned against real Companies House filings (smoke test), which differ from the textbook:
  • Real balance sheets rarely tag a single "total assets" line — they tag
    PropertyPlantEquipment, CurrentAssets, NetCurrentAssetsLiabilities and
    TotalAssetsLessCurrentLiabilities — so we derive total assets from those.
  • Accounts type / standard / audit status / dormancy are themselves tagged
    (AccountsType, AccountingStandardsApplied, AccountsStatusAuditedOrUnaudited,
    EntityDormantTruefalse) — we read them directly instead of guessing.
  • Bank borrowings are often tagged WITHOUT a maturity dimension — we capture those too.

GDPR: personal-data tags (director names etc.) are never mapped to output, and a final
assert guards against any leaking in. We keep company-level + financial fields only.
"""
from __future__ import annotations

import re
from datetime import date

# Single-value concepts: target field -> taxonomy concept local-names (FRS 102 / 105)
SINGLE_VALUE_SYNONYMS = {
    "turnover": ["TurnoverRevenue", "Turnover", "Revenue"],
    "gross_profit": ["GrossProfitLoss"],
    "operating_profit": ["OperatingProfitLoss"],
    "interest_payable": ["InterestPayableSimilarChargesFinanceCosts", "InterestPayableSimilarCharges", "FinanceCosts"],
    "interest_receivable": ["InterestReceivableSimilarIncomeFinanceIncome", "InterestReceivableSimilarIncome"],
    "profit_before_tax": ["ProfitLossOnOrdinaryActivitiesBeforeTax", "ProfitLossBeforeTax"],
    "tax": ["TaxTaxCreditOnProfitOrLossOnOrdinaryActivities", "TaxOnProfitOrLossOnOrdinaryActivities"],
    "profit_for_year": ["ProfitLoss"],
    "dividends": ["DividendsPaid", "Dividends", "DividendsPaidProposed"],
    "staff_costs": ["StaffCostsEmployeeBenefitsExpense", "StaffCosts"],
    "fixed_assets": ["FixedAssets"],
    "property_plant_equipment": ["PropertyPlantEquipment"],
    "current_assets": ["CurrentAssets"],
    "debtors": ["Debtors"],
    "cash": ["CashBankOnHand", "CashBankInHand", "CashCashEquivalents"],
    "net_current_assets": ["NetCurrentAssetsLiabilities"],
    "talcl": ["TotalAssetsLessCurrentLiabilities"],
    "total_assets_direct": ["Assets", "TotalAssets"],
    "net_assets": ["NetAssetsLiabilities", "NetAssetsLiabilitiesIncludingPensionAssetLiability"],
    "equity": ["Equity", "ShareholdersFunds", "TotalShareholdersFunds", "CapitalEmployed"],
    "provisions": ["ProvisionsForLiabilitiesBalanceSheetSubtotal", "Provisions"],
    "employees": ["AverageNumberEmployeesDuringPeriod", "NumberEmployees", "AverageNumberEmployees"],
}
DURATION_FIELDS = {
    "turnover", "gross_profit", "operating_profit", "interest_payable", "interest_receivable",
    "profit_before_tax", "tax", "profit_for_year", "dividends", "staff_costs", "employees",
}

# Funding breakdown: bucket label -> concept local-names
FUNDING_CONCEPTS = {
    "bank_loans": ["BankBorrowingsOverdrafts", "BankBorrowings", "BankLoans", "BankOverdrafts", "BankLoansOverdrafts"],
    "other_loans": ["OtherBorrowings", "OtherLoans"],
    "finance_leases": ["NetObligationsUnderFinanceLeasesHirePurchaseContracts", "ObligationsUnderFinanceLeasesHirePurchaseContracts"],
    "trade_creditors": ["TradeCreditorsTradePayables", "TradeCreditors"],
    "intercompany": ["AmountsOwedToGroupUndertakings"],
    "director_loans": ["DirectorLoanAccount", "AmountsOwedToDirectors"],
    "tax_social_security": ["TaxationSocialSecurityPayable"],
    "accruals_deferred": ["AccruedLiabilitiesDeferredIncome", "AccrualsDeferredIncome"],
    "other_creditors": ["OtherCreditors"],
}
CREDITORS_TOTAL_NAMES = ["Creditors"]
DEBT_LIKE = {"bank_loans", "other_loans", "finance_leases"}

# Tagged meta facts (nonNumeric): our key -> taxonomy concept name
META_TAGS = {
    "accounts_type": "AccountsType",
    "accounting_standard": "AccountingStandardsApplied",
    "audit_status": "AccountsStatusAuditedOrUnaudited",
    "dormant_tag": "EntityDormantTruefalse",
}

# Personal data — must NEVER be written to output (not mapped anywhere; this is belt-and-braces)
PERSONAL_DATA_CONCEPTS = ["NameEntityOfficer", "DirectorSigningFinancialStatements",
                          "NameIndividualOfficer", "NamePerson"]


def _detect_taxonomy(refs, standard):
    j = (" ".join(refs) + " " + (standard or "")).lower()
    if "frs-105" in j or "frs105" in j or "micro" in j:
        return "FRS-105"
    if "frs-102" in j or "frs102" in j:
        return "FRS-102"
    if "ifrs" in j:
        return "IFRS"
    return "unknown"


def _taxonomy_version(refs):
    for r in refs:
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", r)
        if m:
            return m.group(1)
    return None


def _currency(extracted):
    for measure in extracted["units"].values():
        if measure and "iso4217" in measure.lower():
            return measure.split(":")[-1].upper()
    return None


def _raw_number(text):
    """Parse a number straight from the displayed text, IGNORING any XBRL scale.
    Used for employee counts: some filing software wrongly tags a count like '21' with
    scale=-2 (which would yield 0.21). The displayed integer is the truth."""
    if not text:
        return None
    c = re.sub(r"[^0-9.\-]", "", text.replace("(", "").replace(")", ""))
    if c in ("", "-", ".", "-.", "--"):
        return None
    try:
        return float(c)
    except ValueError:
        return None


def _employees_count(facts, duration_ids):
    """Average employees, read from raw text (scale ignored), current period preferred."""
    names = SINGLE_VALUE_SYNONYMS["employees"]
    cands = [(f, _raw_number(f["raw_text"])) for f in facts if f["concept"] in names]
    cands = [(f, n) for f, n in cands if n is not None]
    if not cands:
        return None
    in_period = [c for c in cands if c[0]["context_ref"] in duration_ids]
    return sorted(in_period or cands, key=lambda c: abs(c[1]), reverse=True)[0][1]


def _primary_periods(contexts):
    """Current period end + the PLAIN (non-dimensioned) context ids carrying headline totals."""
    all_ends = [c["period"]["end"] for c in contexts.values() if c["period"].get("end")]
    if not all_ends:
        return None, set(), set(), None
    period_end = max(all_ends)
    instant_ids, duration_ids, period_start = set(), set(), None
    for cid, c in contexts.items():
        if c.get("dimensions"):
            continue
        p = c["period"]
        if p.get("end") != period_end:
            continue
        if p["type"] == "instant":
            instant_ids.add(cid)
        elif p["type"] == "duration":
            duration_ids.add(cid)
            if p.get("start") and (period_start is None or p["start"] < period_start):
                period_start = p["start"]
    return period_end, instant_ids, duration_ids, period_start


def _pick2(field, facts, instant_ids, duration_ids):
    """Return (value, in_period_flag). in_period_flag is False when the value had to be
    taken from a context OUTSIDE the primary reporting period (e.g. only the prior-year
    comparative is tagged) — callers can then flag the provenance honestly."""
    names = SINGLE_VALUE_SYNONYMS[field]
    preferred = duration_ids if field in DURATION_FIELDS else instant_ids
    cands = [f for f in facts if f["concept"] in names and f["value"] is not None]
    if not cands:
        return None, True
    in_period = [f for f in cands if f["context_ref"] in preferred]
    pool = in_period or cands
    return sorted(pool, key=lambda f: abs(f["value"]), reverse=True)[0]["value"], bool(in_period)


def _pick(field, facts, instant_ids, duration_ids):
    return _pick2(field, facts, instant_ids, duration_ids)[0]


def _read_meta(facts):
    out = {}
    for key, tag in META_TAGS.items():
        for f in facts:
            if f["concept"] == tag and (f["raw_text"] or "").strip():
                out[key] = f["raw_text"].strip()
                break
    return out


def _maturity_bucket(ctx):
    for _dim, member in ctx.get("dimensions", []):
        m = member.lower()
        if "withinoneyear" in m:
            return "within"
        if "afteroneyear" in m or "aftermorethanoneyear" in m:
            return "after"
    return None


def _funding(facts, contexts, period_end, instant_ids):
    """For each funding label, collect within / after (dimensioned) and plain (un-dimensioned)."""
    lab = {k: {"within": None, "after": None, "plain": None} for k in FUNDING_CONCEPTS}
    cred = {"within": None, "after": None, "plain": None}
    for f in facts:
        if f["value"] is None:
            continue
        ctx = contexts.get(f["context_ref"])
        if not ctx or ctx["period"].get("end") != period_end:
            continue
        bucket = _maturity_bucket(ctx)
        slot = bucket if bucket else ("plain" if f["context_ref"] in instant_ids else None)
        if slot is None:
            continue
        for label, names in FUNDING_CONCEPTS.items():
            if f["concept"] in names:
                lab[label][slot] = (lab[label][slot] or 0) + f["value"]
        if f["concept"] in CREDITORS_TOTAL_NAMES:
            cred[slot] = f["value"]
    return lab, cred


def _total(slots):
    """within+after if either present (avoids double-counting a plain total); else plain."""
    w, a, p = slots["within"], slots["after"], slots["plain"]
    return (w or 0) + (a or 0) if (w is not None or a is not None) else p


def normalise(extracted):
    facts = extracted["facts"]
    contexts = extracted["contexts"]
    period_end, instant_ids, duration_ids, period_start = _primary_periods(contexts)
    prov = {}

    def pick(field):
        v, in_period = _pick2(field, facts, instant_ids, duration_ids)
        if v is not None:
            prov[field] = "reported" if in_period else "reported(out-of-period)"
        return v

    turnover = pick("turnover"); gross_profit = pick("gross_profit"); operating_profit = pick("operating_profit")
    interest_payable = pick("interest_payable"); interest_receivable = pick("interest_receivable")
    profit_before_tax = pick("profit_before_tax"); tax = pick("tax"); profit_for_year = pick("profit_for_year")
    dividends = pick("dividends"); staff_costs = pick("staff_costs"); cash = pick("cash")
    provisions = pick("provisions")
    employees = _employees_count(facts, duration_ids)
    if employees is not None:
        prov["employees"] = "reported"

    ppe = _pick("property_plant_equipment", facts, instant_ids, duration_ids)
    fixed_assets = _pick("fixed_assets", facts, instant_ids, duration_ids)
    current_assets = _pick("current_assets", facts, instant_ids, duration_ids)
    debtors = _pick("debtors", facts, instant_ids, duration_ids)
    net_current_assets = _pick("net_current_assets", facts, instant_ids, duration_ids)
    talcl = _pick("talcl", facts, instant_ids, duration_ids)
    fixed = fixed_assets if fixed_assets is not None else ppe

    # total assets: reported -> fixed+current -> (TALCL - net current) + current
    total_assets = _pick("total_assets_direct", facts, instant_ids, duration_ids)
    if total_assets is not None:
        prov["total_assets"] = "reported"
    elif fixed is not None and current_assets is not None:
        total_assets = fixed + current_assets
        prov["total_assets"] = "summed(fixed+current)"
    elif talcl is not None and net_current_assets is not None and current_assets is not None:
        total_assets = talcl - net_current_assets + current_assets
        prov["total_assets"] = "derived(talcl-nca+current)"

    net_assets = _pick("net_assets", facts, instant_ids, duration_ids)
    if net_assets is not None:
        prov["net_assets"] = "reported"
    else:
        net_assets = _pick("equity", facts, instant_ids, duration_ids)
        if net_assets is not None:
            prov["net_assets"] = "reported(equity)"

    funding, cred = _funding(facts, contexts, period_end, instant_ids)

    # total liabilities: assets - net assets, else creditors + provisions
    if total_assets is not None and net_assets is not None:
        total_liabilities = round(total_assets - net_assets, 2)
        prov["total_liabilities"] = "derived(assets-net_assets)"
    else:
        parts = [x for x in (cred["within"], cred["after"], provisions) if x is not None]
        total_liabilities = round(sum(parts), 2) if parts else None
        if total_liabilities is not None:
            prov["total_liabilities"] = "summed(creditors+provisions)"

    debt_total, have_debt = 0.0, False
    for label in DEBT_LIKE:
        t = _total(funding[label])
        if t is not None:
            debt_total += t
            have_debt = True
    # Net debt = interest-bearing debt − cash. Report a NET CASH position (negative)
    # when a company has cash but no tagged borrowings.
    if have_debt or cash is not None:
        net_debt = round(debt_total - (cash or 0), 2)
        prov["net_debt"] = "derived(debt-cash)" if have_debt else "derived(net cash; no debt tagged)"
    else:
        net_debt = None

    meta = _read_meta(facts)
    standard = meta.get("accounting_standard")
    taxonomy = _detect_taxonomy(extracted["taxonomy_refs"], standard)
    has_pl = turnover is not None or operating_profit is not None

    if meta.get("accounts_type"):
        accounts_type = meta["accounts_type"]
    elif taxonomy == "FRS-105":
        accounts_type = "micro"
    elif has_pl:
        accounts_type = "full_or_small_with_pl"
    else:
        accounts_type = "small_filleted_no_pl"
    filleted = taxonomy in ("FRS-102", "FRS-101", "IFRS") and not has_pl

    audited = None
    au = meta.get("audit_status", "")
    if au:
        al = au.lower()
        audited = False if ("unaudited" in al or "exempt" in al) else True if "audited" in al else None
    if audited is None and any("exemptionfromaudit" in f["concept"].lower() for f in facts):
        audited = False

    dormant = None
    if meta.get("dormant_tag"):
        dormant = meta["dormant_tag"].strip().lower() in ("true", "yes", "1")

    period_length_days = None
    if period_start and period_end:
        try:
            period_length_days = (date.fromisoformat(period_end) - date.fromisoformat(period_start)).days
        except ValueError:
            pass

    funding_within = {L: funding[L]["within"] for L in FUNDING_CONCEPTS}
    funding_within["creditors_total"] = cred["within"]
    funding_after = {L: funding[L]["after"] for L in FUNDING_CONCEPTS}
    funding_after["creditors_total"] = cred["after"]

    record = {
        "company_number": extracted["company_number"],
        "taxonomy": taxonomy,
        "taxonomy_version": _taxonomy_version(extracted["taxonomy_refs"]),
        "accounting_standard": standard,
        "currency": _currency(extracted),
        "period_start": period_start,
        "period_end": period_end,
        "period_length_days": period_length_days,
        "turnover": turnover,
        "gross_profit": gross_profit,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "net_assets": net_assets,
        "employees": employees,
        "operating_profit": operating_profit,
        "interest_payable": interest_payable,
        "interest_receivable": interest_receivable,
        "profit_before_tax": profit_before_tax,
        "tax": tax,
        "profit_for_year": profit_for_year,
        "dividends": dividends,
        "staff_costs": staff_costs,
        "provisions": provisions,
        "property_plant_equipment": ppe,
        "current_assets": current_assets,
        "debtors": debtors,
        "net_current_assets": net_current_assets,
        "total_assets_less_current_liabilities": talcl,
        "cash": cash,
        "net_debt": net_debt,
        "funding_within_one_year": funding_within,
        "funding_after_one_year": funding_after,
        "accounts_type": accounts_type,
        "filleted": filleted,
        "audited": audited,
        "dormant": dormant,
        "provenance": prov,
    }

    # GDPR guard: no personal-data concept may appear as a field
    assert not any(p in record for p in PERSONAL_DATA_CONCEPTS)

    target = ["turnover", "gross_profit", "operating_profit", "total_assets",
              "total_liabilities", "net_assets", "cash", "net_debt", "employees"]
    record["completeness_score"] = round(sum(1 for k in target if record.get(k) is not None) / len(target), 2)
    return record

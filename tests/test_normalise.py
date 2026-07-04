"""Golden-file and logic tests for normalise.

The golden files in tests/golden/ are the full normalised records for the two
fixtures. If a change to parse_ixbrl or normalise alters ANY field, these tests
fail and show the exact diff — regenerate deliberately with:

    python -c "import json,sys; sys.path.insert(0,'src'); import parse_ixbrl,normalise; \
      [open(f'tests/golden/{n}.json','w').write(json.dumps(normalise.normalise( \
      parse_ixbrl.extract_facts(f'tests/fixtures/{n}.html')),indent=2,sort_keys=True)+'\n') \
      for n in ('frs102_full_sample','frs105_micro_sample')]"
"""
import json

import pytest

from conftest import FIXTURES, GOLDEN

import parse_ixbrl
import normalise


@pytest.mark.parametrize("name", ["frs102_full_sample", "frs105_micro_sample"])
def test_golden_record(name):
    record = normalise.normalise(parse_ixbrl.extract_facts(FIXTURES / f"{name}.html"))
    expected = json.loads((GOLDEN / f"{name}.json").read_text())
    assert record == expected


@pytest.fixture(scope="module")
def frs102():
    return normalise.normalise(parse_ixbrl.extract_facts(FIXTURES / "frs102_full_sample.html"))


class TestDerivations:
    """Targeted checks that the headline derivations behave as documented."""

    def test_total_assets_summed_with_provenance(self, frs102):
        assert frs102["total_assets"] == 2_700_000
        assert frs102["provenance"]["total_assets"] == "summed(fixed+current)"

    def test_total_liabilities_derived(self, frs102):
        assert frs102["total_liabilities"] == frs102["total_assets"] - frs102["net_assets"]
        assert frs102["provenance"]["total_liabilities"] == "derived(assets-net_assets)"

    def test_net_debt_is_debt_minus_cash(self, frs102):
        # bank 300k (within) + 400k (after) + finance leases 150k - cash 500k
        assert frs102["net_debt"] == 350_000

    def test_maturity_buckets_not_double_counted(self, frs102):
        w = frs102["funding_within_one_year"]
        a = frs102["funding_after_one_year"]
        assert w["bank_loans"] == 300_000 and a["bank_loans"] == 400_000
        assert w["creditors_total"] == 600_000 and a["creditors_total"] == 750_000

    def test_no_personal_data_fields(self, frs102):
        for concept in normalise.PERSONAL_DATA_CONCEPTS:
            assert concept not in frs102


class TestTotalHelper:
    def test_within_after_preferred_over_plain(self):
        # a plain total alongside dimensioned figures must NOT be double-counted
        assert normalise._total({"within": 100, "after": 50, "plain": 150}) == 150

    def test_plain_used_when_no_dimensions(self):
        assert normalise._total({"within": None, "after": None, "plain": 80}) == 80

    def test_single_sided(self):
        assert normalise._total({"within": 100, "after": None, "plain": None}) == 100


class TestEmployeesScaleBug:
    def test_raw_number_ignores_scale(self):
        # some filing software tags a count of 21 with scale=-2; displayed text wins
        assert normalise._raw_number("21") == 21
        assert normalise._raw_number("(3)") == 3
        assert normalise._raw_number("") is None


class TestNetCashPosition:
    def test_cash_no_debt_reports_net_cash(self):
        extracted = {
            "company_number": "00000001",
            "taxonomy_refs": ["https://xbrl.frc.org.uk/FRS-105/2024-01-01/FRS-105-2024-01-01.xsd"],
            "units": {"u1": "iso4217:GBP"},
            "contexts": {
                "c1": {"identifier": "00000001",
                       "period": {"type": "instant", "start": None, "end": "2024-12-31"},
                       "dimensions": []},
            },
            "facts": [
                {"concept": "CashBankOnHand", "namespace": None, "value": 10_000.0,
                 "raw_text": "10,000", "scale": None, "sign": None,
                 "context_ref": "c1", "unit_ref": "u1"},
            ],
        }
        record = normalise.normalise(extracted)
        assert record["net_debt"] == -10_000
        assert record["provenance"]["net_debt"] == "derived(net cash; no debt tagged)"

"""Unit tests for parse_ixbrl: number handling and fact extraction from the fixtures."""
import pytest

from conftest import FIXTURES

import parse_ixbrl
from parse_ixbrl import _to_number


class TestToNumber:
    def test_thousands_separator(self):
        assert _to_number("5,000", None, None) == 5000

    def test_parenthesised_negative(self):
        assert _to_number("(1,234)", None, None) == -1234

    def test_explicit_sign_attribute(self):
        assert _to_number("1234", None, "-") == -1234

    def test_scale_thousands(self):
        assert _to_number("5,000", "3", None) == 5_000_000

    def test_scale_and_paren_negative(self):
        assert _to_number("(250)", "3", None) == -250_000

    def test_invalid_scale_ignored(self):
        assert _to_number("100", "abc", None) == 100

    @pytest.mark.parametrize("junk", [None, "", "  ", "-", ".", "--", "n/a"])
    def test_junk_returns_none(self, junk):
        assert _to_number(junk, None, None) is None


class TestExtractFacts:
    def test_frs102_fixture(self):
        ex = parse_ixbrl.extract_facts(FIXTURES / "frs102_full_sample.html")
        assert ex["company_number"] == "12345678"
        assert len(ex["facts"]) == 20
        assert any("FRS-102" in r for r in ex["taxonomy_refs"])
        assert "GBP" in [m.split(":")[-1].upper() for m in ex["units"].values() if m]
        # every fact must reference a declared context
        for f in ex["facts"]:
            assert f["context_ref"] in ex["contexts"]

    def test_frs105_fixture(self):
        ex = parse_ixbrl.extract_facts(FIXTURES / "frs105_micro_sample.html")
        assert ex["company_number"] == "87654321"
        assert len(ex["facts"]) == 7
        assert any("FRS-105" in r for r in ex["taxonomy_refs"])

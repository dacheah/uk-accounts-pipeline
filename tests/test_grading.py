"""Tests for benchmark_grading — the grader underpins every published score."""
from conftest import ROOT  # noqa: F401  (ensures repo root on sys.path)

from benchmark_grading import (
    parse_number, grade_numeric, grade_choice, grade_boolean, grade_not_disclosed, grade,
)


class TestParseNumber:
    def test_million_suffix(self):
        assert parse_number("£1.2 million") == 1_200_000

    def test_k_suffix(self):
        assert parse_number("about 250k") == 250_000

    def test_working_shown_answer_after_equals(self):
        assert parse_number("26179 / 307 = 85.27") == 85.27

    def test_parenthesised_negative(self):
        assert parse_number("(1,234)") == -1234

    def test_plain_with_currency_and_commas(self):
        assert parse_number("The turnover was £5,000,000.") == 5_000_000

    def test_no_number(self):
        assert parse_number("not disclosed") is None


class TestGradeNumeric:
    def test_exact(self):
        assert grade_numeric("£100,000", 100_000)

    def test_within_one_percent(self):
        assert grade_numeric("100,900", 100_000)

    def test_outside_tolerance(self):
        assert not grade_numeric("102,000", 100_000)

    def test_within_one_pound_of_zero(self):
        assert grade_numeric("£0.50", 0)


class TestGradeChoice:
    def test_dropped_prefix_suffix(self):
        assert grade_choice("A. HINGE & SONS LIMITED", "HINGE & SONS")

    def test_case_and_punctuation(self):
        assert grade_choice("hinge and sons ltd", "HINGE AND SONS")

    def test_wrong_company(self):
        assert not grade_choice("SMITH BROS LIMITED", "HINGE & SONS")


class TestGradeBoolean:
    def test_yes(self):
        assert grade_boolean("Yes, the company is audited.", True)

    def test_no_mismatch(self):
        assert not grade_boolean("No.", True)

    def test_first_token_wins(self):
        assert grade_boolean("No — the accounts say yes nowhere.", False)


class TestGradeNotDisclosed:
    def test_phrase(self):
        assert grade_not_disclosed("The figure is not disclosed in these accounts.")

    def test_punctuated_variant(self):
        assert grade_not_disclosed("Not-Disclosed")

    def test_hallucinated_zero_fails(self):
        # the turnover trap: answering £0 for legally-omitted turnover is WRONG
        assert not grade_not_disclosed("£0")


class TestDispatch:
    def test_numeric_item(self):
        assert grade({"answer_type": "numeric", "answer": 500.0}, "£500")

    def test_unknown_type_fails_closed(self):
        assert not grade({"answer_type": "essay", "answer": "x"}, "anything")

"""
benchmark_grading.py — turn a model's free-text answer into a score.

Models answer in messy ways: "£1.2 million", "1,200,000", "approximately 1.2m",
"(5,000)" for negatives. This module parses those into a number and grades against
the verified truth, with tolerance. Shared by build_benchmark.py and evaluate.py.
"""
from __future__ import annotations

import re

_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
         "bn": 1e9, "b": 1e9, "billion": 1e9}


def _to_float(s: str):
    s = s.strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "").replace("£", "").replace(",", "").replace("+", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_number(text):
    """Extract the most likely numeric value from a free-text answer (in pounds)."""
    if text is None:
        return None
    t = text.strip().lower().replace(",", "")
    # if the model showed its working ("26179 / 307 = 85.27"), the answer is after the last '='
    if "=" in t:
        t = t.rsplit("=", 1)[-1].strip()
    # "1.2 million" / "1.2m" style
    m = re.search(r"(-?\(?£?\d+(?:\.\d+)?\)?)\s*(k|thousand|mn|million|m|bn|billion|b)\b", t)
    if m:
        num = _to_float(m.group(1))
        if num is not None:
            return num * _MULT[m.group(2)]
    # first plain number (allow leading £ and parenthesised negatives)
    m = re.search(r"-?\(?£?\d[\d]*(?:\.\d+)?\)?", t)
    if m:
        return _to_float(m.group(0))
    return None


def grade_numeric(model_text, truth, rel_tol=0.01, abs_tol=1.0) -> bool:
    """Correct if within 1% (relative) or £1 (absolute) of the truth."""
    v = parse_number(model_text)
    if v is None or truth is None:
        return False
    if abs(v - truth) <= abs_tol:
        return True
    if truth != 0 and abs(v - truth) / abs(truth) <= rel_tol:
        return True
    return False


def _norm_name(s):
    s = str(s).lower()
    s = re.sub(r"\b(ltd|limited|plc|llp|llc|uk|the|company|co)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)   # strip spaces/punctuation too (models drop them)


def grade_choice(model_text, truth_label) -> bool:
    """Correct if the company name matches, tolerant of dropped prefixes/suffixes
    (e.g. 'A. HINGE & SONS LIMITED' vs 'HINGE & SONS') and punctuation/case."""
    if not model_text or not truth_label:
        return False
    t, m = _norm_name(truth_label), _norm_name(model_text)
    if len(t) < 3:
        return t in m
    return t in m or m in t


def grade_boolean(model_text, truth_bool) -> bool:
    """Correct if the answer's yes/no matches the truth. Looks at the first yes/no token."""
    if not model_text:
        return False
    t = model_text.strip().lower()
    yes = re.search(r"\b(yes|true|correct)\b", t)
    no = re.search(r"\b(no|false|incorrect)\b", t)
    said = None
    if yes and (not no or yes.start() < no.start()):
        said = True
    elif no:
        said = False
    return said is not None and said == bool(truth_bool)


_NOT_DISCLOSED = ["not disclosed", "not stated", "not provided", "not available", "not shown",
                  "not reported", "cannot be determined", "cannot determine", "can't be determined",
                  "no turnover", "not included", "does not disclose", "is not disclosed",
                  "isn't disclosed", "not present", "not applicable", "n/a", "unable to",
                  "not given", "no figure", "not in these accounts"]


_ND_STRIPPED = ["notdisclosed", "notstated", "notprovided", "notavailable", "notshown",
                "notreported", "cannotbedetermined", "cannotdetermine", "noturnover",
                "notincluded", "doesnotdisclose", "isnotdisclosed", "notpresent",
                "notapplicable", "notgiven", "nofigure", "notintheseaccounts", "unableto"]


def grade_not_disclosed(model_text) -> bool:
    """Correct if the model recognises the figure is NOT in the accounts (filleted/micro),
    rather than hallucinating a number. Whitespace/punctuation-insensitive."""
    if not model_text:
        return False
    t = model_text.lower()
    if any(p in t for p in _NOT_DISCLOSED):
        return True
    stripped = re.sub(r"[^a-z]", "", t)          # catches 'notdisclosed', 'not-disclosed', etc.
    return any(p in stripped for p in _ND_STRIPPED)


def grade(item, model_text) -> bool:
    """Dispatch on the item's answer_type."""
    at = item.get("answer_type")
    if at == "numeric":
        return grade_numeric(model_text, item["answer"],
                             rel_tol=item.get("rel_tol", 0.01), abs_tol=item.get("abs_tol", 1.0))
    if at == "choice":
        return grade_choice(model_text, item["answer"])
    if at == "boolean":
        return grade_boolean(model_text, item["answer"])
    if at == "not_disclosed":
        return grade_not_disclosed(model_text)
    return False

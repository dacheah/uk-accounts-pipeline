"""
parse_ixbrl.py — extract tagged financial facts from a Companies House iXBRL filing.

A Companies House accounts file is an "inline XBRL" (iXBRL) document: a web page
(XHTML) with machine-readable tags inside it. Each meaningful number is wrapped in a
tag saying WHAT it is (e.g. "Turnover"), WHICH period it belongs to (a "context"),
and HOW to read it (a scale/sign).

This module reads those tags into a plain list of facts. It does NOT interpret them
into a schema — that's normalise.py's job — which is what lets Product 2 reuse it.

GDPR: we read every tag here, but only numeric/identifier tags get mapped to output
downstream; names and addresses are dropped, so personal data never reaches a dataset.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from lxml import etree

IX_NAMESPACES = {
    "http://www.xbrl.org/2013/inlineXBRL",
    "http://www.xbrl.org/2008/inlineXBRL",
}
XBRLI_NS = "http://www.xbrl.org/2003/instance"      # contexts, periods, units
LINKBASE_NS = "http://www.xbrl.org/2003/linkbase"   # schemaRef (tells us the taxonomy)
XLINK_NS = "http://www.w3.org/1999/xlink"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"           # dimensions (e.g. maturity within/after 1yr)

_NUM_CLEAN = re.compile(r"[^0-9.\-]")


def _to_number(text: Optional[str], scale: Optional[str], sign: Optional[str]) -> Optional[float]:
    """Turn a fact's displayed text into a real number.

    Handles thousands separators ("5,000"), accounting negatives "(1,234)", an explicit
    sign="-" attribute, and a scale (scale="3" means figures shown in thousands)."""
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None
    negative = (sign == "-") or ("(" in s and ")" in s)
    cleaned = _NUM_CLEAN.sub("", s.replace("(", "").replace(")", ""))
    if cleaned in ("", "-", ".", "-.", "--"):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if scale:
        try:
            value *= 10 ** int(scale)
        except ValueError:
            pass
    if negative:
        value = -abs(value)
    return value


def _parse_tree(path: Path):
    parser = etree.XMLParser(recover=True, huge_tree=True, ns_clean=False)
    return etree.parse(str(path), parser).getroot()


def _elements(node):
    """Yield only real element nodes — skips comments and processing instructions, whose
    .tag is a function rather than a string and would crash etree.QName()."""
    for el in node.iter():
        if isinstance(el.tag, str):
            yield el


def _is_paren_negative(el):
    """True if the tagged value is visually wrapped in parentheses placed OUTSIDE the tag
    (e.g. net liabilities shown as "( 13,597 )" with a bare positive number inside the tag).
    Many filers encode the negative this way instead of using sign='-'."""
    parent = el.getparent()
    prev = el.getprevious()
    before = (prev.tail if prev is not None else (parent.text if parent is not None else "")) or ""
    after = el.tail or ""
    if not before.strip() and prev is not None:        # e.g. a separate "(" element
        before = "".join(prev.itertext()) + (prev.tail or "")
    nxt = el.getnext()
    if not after.strip() and nxt is not None:          # e.g. a separate ")" element
        after = (el.tail or "") + "".join(nxt.itertext())
    return before.rstrip().endswith("(") and after.lstrip().startswith(")")


def extract_facts(path) -> dict:
    """Read one iXBRL filing into a plain dict of contexts, units and facts."""
    path = Path(path)
    root = _parse_tree(path)

    # taxonomy references (schemaRef href -> tells FRS 102 from FRS 105)
    taxonomy_refs: list[str] = []
    for el in _elements(root):
        qn = etree.QName(el)
        if qn.namespace == LINKBASE_NS and qn.localname == "schemaRef":
            href = el.get(f"{{{XLINK_NS}}}href")
            if href:
                taxonomy_refs.append(href)

    # contexts (each defines a period, the entity, and any dimensions)
    contexts: dict[str, dict] = {}
    for ctx in _elements(root):
        qn = etree.QName(ctx)
        if not (qn.namespace == XBRLI_NS and qn.localname == "context"):
            continue
        cid = ctx.get("id")
        identifier = start = end = instant = None
        for sub in _elements(ctx):
            sqn = etree.QName(sub)
            if sqn.namespace != XBRLI_NS:
                continue
            txt = (sub.text or "").strip()
            if sqn.localname == "identifier":
                identifier = txt
            elif sqn.localname == "startDate":
                start = txt
            elif sqn.localname == "endDate":
                end = txt
            elif sqn.localname == "instant":
                instant = txt
        if instant:
            period = {"type": "instant", "start": None, "end": instant}
        elif start or end:
            period = {"type": "duration", "start": start, "end": end}
        else:
            period = {"type": "unknown", "start": None, "end": None}
        dimensions = []
        for sub in _elements(ctx):
            if etree.QName(sub).localname == "explicitMember":
                dim = (sub.get("dimension") or "").split(":")[-1]
                member = (sub.text or "").strip().split(":")[-1]
                dimensions.append((dim, member))
        contexts[cid] = {"identifier": identifier, "period": period, "dimensions": dimensions}

    # units (currency)
    units: dict[str, str] = {}
    for u in _elements(root):
        qn = etree.QName(u)
        if not (qn.namespace == XBRLI_NS and qn.localname == "unit"):
            continue
        measure = None
        for m in _elements(u):
            if etree.QName(m).localname == "measure":
                measure = (m.text or "").strip()
        units[u.get("id")] = measure

    # facts
    facts: list[dict] = []
    for el in _elements(root):
        qn = etree.QName(el)
        if qn.namespace not in IX_NAMESPACES or qn.localname not in ("nonFraction", "nonNumeric"):
            continue
        name = el.get("name")
        if not name:
            continue
        prefix, _, local = name.partition(":")
        raw_text = "".join(el.itertext())
        scale, sign = el.get("scale"), el.get("sign")
        value = _to_number(raw_text, scale, sign) if qn.localname == "nonFraction" else None
        if value is not None and value > 0 and _is_paren_negative(el):
            value = -value  # negative shown by parentheses outside the tag
        facts.append({
            "concept": local,
            "namespace": el.nsmap.get(prefix),
            "value": value,
            "raw_text": raw_text.strip(),
            "scale": scale,
            "sign": sign,
            "context_ref": el.get("contextRef"),
            "unit_ref": el.get("unitRef"),
        })

    company_number = next((c["identifier"] for c in contexts.values() if c["identifier"]), None)
    return {
        "source_file": path.name,
        "company_number": company_number,
        "taxonomy_refs": taxonomy_refs,
        "contexts": contexts,
        "units": units,
        "facts": facts,
    }

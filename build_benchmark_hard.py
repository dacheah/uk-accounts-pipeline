"""
build_benchmark_hard.py — the HARD benchmark: extraction from RAW iXBRL filings, no hints.

This is the honest test of the difficult task: give a model the actual filed accounts
(inline XBRL rendered to text, with the noise, the £'000 scaling, two years side by side,
filleted gaps, and UK-GAAP terminology) and ask it to extract a figure. Ground truth is our
parsed value. Includes the turnover-hallucination trap (ask turnover of a filleted company
with NO hint that it's missing).

PRIVACY: personal data is removed using the iXBRL tags themselves — any nonNumeric/nonFraction
fact whose concept names an officer/director/address/contact is blanked to [redacted] before
the text is rendered. These raw contexts are kept PRIVATE (used for scoring only, not published).

Output -> <data>/processed/benchmark_hard_v1/benchmark.jsonl  (+ sample.md, stats.json)

Run:  python build_benchmark_hard.py
"""
from __future__ import annotations

import json
import math
import random
import sys
import zipfile
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
import config  # noqa: E402

SEED = 7
N_PER = {"net_assets": 90, "employees": 70, "cash": 50, "current_assets": 40,
         "f_within_creditors_total": 30}   # extraction (present, balance-sheet items)
N_HALLUCINATION = 70                       # filleted turnover, no hint
LABELS = {
    "net_assets": "net assets", "employees": "average number of employees",
    "cash": "cash at bank and in hand", "current_assets": "current assets",
    "f_within_creditors_total": "creditors falling due within one year", "turnover": "turnover",
}
USE_COLS = ["company_number", "company_name", "period_end", "source_file", "taxonomy",
            "accounts_type", "filleted", "dormant", "net_assets", "employees", "cash",
            "current_assets", "f_within_creditors_total", "turnover"]

# Redact nonNumeric/nonFraction facts whose concept names a person or contact/address
REDACT_HINTS = ("officer", "signing", "signator", "forename", "surname", "nameindividual",
                "contactname", "addressline", "postalcode", "postcode", "telephone",
                "phone", "email", "faxnumber", "directorname")
MAX_CONTEXT = 45000


def render_redacted(data_bytes):
    """Render an iXBRL filing to plain text with personal-data facts blanked out, keeping
    adjacent cell/element text separated so column figures don't merge into one token."""
    root = etree.fromstring(data_bytes, etree.XMLParser(recover=True, huge_tree=True))
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el).localname in ("nonNumeric", "nonFraction"):
            local = (el.get("name") or "").split(":")[-1].lower()
            if any(h in local for h in REDACT_HINTS):
                for child in list(el):
                    el.remove(child)
                el.text = "[redacted]"
        el.tail = (el.tail or "") + " "  # separator so '...assets' and '1,350' don't glue together
    text = etree.tostring(root, method="text", encoding="unicode")
    text = " ".join(text.split())
    return text[:MAX_CONTEXT]


def index_zip(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return {Path(n).name: n for n in zf.namelist() if n.lower().endswith((".html", ".xml"))}


def load_dataset():
    import pandas as pd
    import pyarrow.parquet as pq
    dirs = sorted(config.OUT_DIR.glob("product1_v*"))
    if not dirs:
        sys.exit("No product1_v* dataset found. Run build_product1.py first.")
    parts = sorted(dirs[-1].glob("product1-*.parquet"))
    cols = [c for c in USE_COLS if c in pq.ParquetFile(parts[0]).schema.names]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in parts], ignore_index=True)
    df = df.sort_values("period_end", ascending=False).drop_duplicates("company_number", keep="first")
    return df


def _present(v):
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def main():
    import pandas as pd
    random.seed(SEED)
    print("Loading dataset ...")
    df = load_dataset()

    zips = sorted(config.RAW_DIR.glob("Accounts_Monthly_Data-*.zip"))
    if not zips:
        sys.exit(f"No monthly zips found in {config.RAW_DIR}.")
    zip_path = zips[-1]
    print(f"Indexing filings in {zip_path.name} ...")
    idx = index_zip(zip_path)
    df = df[df["source_file"].isin(idx.keys())]
    print(f"  {len(df):,} companies whose latest filing is directly in this archive")

    zf = zipfile.ZipFile(zip_path)
    items, uid = [], [0]

    def ctx_for(row):
        member = idx.get(row["source_file"])
        if not member:
            return None
        try:
            return render_redacted(zf.read(member))
        except Exception:
            return None

    def add(category, answer_type, field, question, answer, context, row, **extra):
        uid[0] += 1
        it = {"id": f"h{uid[0]:04d}", "category": category, "answer_type": answer_type,
              "question": question, "answer": answer, "context": context,
              "meta": {"company_number": row["company_number"], "field": field}}
        it.update(extra)
        items.append(it)

    PROMPT = ("Below is a UK company's filed accounts (inline XBRL rendered to text; some "
              "personal data redacted).\n\n{ctx}\n\nQuestion: What was the company's {label} "
              "for the period ending {pe}? Answer with a single {unit}.")

    # extraction (present fields); require positive values for fields that can't be negative
    for field, n in N_PER.items():
        if field == "net_assets":
            pool = df[(df[field].notna()) & (df["company_name"].notna())]
        else:
            pool = df[(df[field] > 0) & (df["company_name"].notna())]
        pool = pool.sample(min(n * 3, len(pool)), random_state=SEED)  # oversample; some contexts may fail
        got = 0
        for _, row in pool.iterrows():
            if got >= n:
                break
            r = row.to_dict()
            ctx = ctx_for(r)
            if not ctx:
                continue
            unit = "whole number" if field == "employees" else "figure in pounds"
            ans = int(round(float(r[field]))) if field == "employees" else float(r[field])
            q = PROMPT.format(ctx=ctx, label=LABELS[field], pe=r["period_end"], unit=unit)
            add("extraction", "numeric", field, q, ans, ctx, r,
                rel_tol=0.005 if field != "employees" else 0.0, abs_tol=1.0)
            got += 1

    # turnover hallucination trap (filleted, NO hint)
    fpool = df[(df["turnover"].isna()) & (df["net_assets"].notna()) &
               (df["dormant"] != True) & (df["company_name"].notna())]  # exclude dormant: '0' would be defensible there
    fpool = fpool.sample(min(N_HALLUCINATION * 3, len(fpool)), random_state=SEED)
    got = 0
    for _, row in fpool.iterrows():
        if got >= N_HALLUCINATION:
            break
        r = row.to_dict()
        ctx = ctx_for(r)
        if not ctx:
            continue
        q = PROMPT.format(ctx=ctx, label="turnover", pe=r["period_end"], unit="figure in pounds")
        add("turnover_trap", "not_disclosed", "turnover", q, "NOT_DISCLOSED", ctx, r)
        got += 1

    zf.close()
    random.shuffle(items)
    for i, it in enumerate(items, 1):
        it["id"] = f"h{i:04d}"

    out_dir = config.OUT_DIR / "benchmark_hard_v1"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "benchmark.jsonl", "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    cats = {}
    for it in items:
        cats[it["category"]] = cats.get(it["category"], 0) + 1
    (out_dir / "stats.json").write_text(json.dumps({"total": len(items), "by_category": cats,
                                                    "source_zip": zip_path.name, "seed": SEED}, indent=2),
                                        encoding="utf-8")
    # privacy-safe sample: show questions WITHOUT the raw context
    md = ["# Hard benchmark — sample (contexts omitted for privacy)\n", f"Total: {len(items)} | {json.dumps(cats)}\n"]
    for it in items[:10]:
        first = it["question"].split("Question:")[-1].strip()
        md.append(f"- **{it['id']}** ({it['category']}): Question: {first}  →  answer `{it['answer']}`")
    (out_dir / "benchmark_sample.md").write_text("\n".join(md), encoding="utf-8")

    print(f"\nDONE: wrote {len(items)} hard items -> {out_dir}")
    print("by category:", json.dumps(cats))


if __name__ == "__main__":
    main()

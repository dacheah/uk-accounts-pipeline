"""
build_benchmark.py — generate 1,000 verified Q&A pairs from the Product 1 dataset.

Runs on your machine against the dataset on D:. Every answer is computed from the data,
so it is verifiably correct. Contexts are reconstructed from the clean, name-free fields
(no director names/addresses), so the benchmark is GDPR-clean and publishable.

Question mix (1,000 total):
  • extraction      — read a figure from a UK balance-sheet extract
  • not_disclosed   — a figure that filleted/micro accounts don't show (hallucination trap)
  • comparison      — which of five companies has the highest metric
  • ratio           — compute debt-to-equity from given figures
  • boolean         — balance-sheet insolvent? micro-entity?

Output -> <data>/processed/benchmark_v1/  (benchmark.jsonl, benchmark_sample.md, stats.json)

Run from the uk-accounts-pipeline folder:
    python build_benchmark.py
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
import config  # noqa: E402

SEED = 42
DATASET_NAME = None
USE_COLS = ["company_number", "company_name", "period_end", "currency", "taxonomy",
            "accounts_type", "filleted", "turnover", "gross_profit", "operating_profit",
            "profit_before_tax", "total_assets", "total_liabilities", "net_assets",
            "employees", "property_plant_equipment", "current_assets", "debtors", "cash",
            "net_current_assets", "total_assets_less_current_liabilities", "provisions",
            "f_within_creditors_total", "f_after_creditors_total"]

# UK balance-sheet presentation order (label only shown if the value is present)
EXTRACT_ROWS = [
    ("property_plant_equipment", "Tangible fixed assets (property, plant & equipment)"),
    ("current_assets", "Current assets"),
    ("debtors", "Debtors"),
    ("cash", "Cash at bank and in hand"),
    ("f_within_creditors_total", "Creditors: amounts falling due within one year"),
    ("net_current_assets", "Net current assets"),
    ("total_assets_less_current_liabilities", "Total assets less current liabilities"),
    ("f_after_creditors_total", "Creditors: amounts falling due after more than one year"),
    ("provisions", "Provisions for liabilities"),
    ("net_assets", "Net assets"),
    ("turnover", "Turnover"),
    ("gross_profit", "Gross profit"),
    ("operating_profit", "Operating profit"),
    ("profit_before_tax", "Profit before taxation"),
    ("employees", "Average number of employees"),
]
# Fields used for extraction questions and a human label for each
EXTRACT_FIELDS = {
    "net_assets": "net assets",
    "current_assets": "current assets",
    "cash": "cash at bank and in hand",
    "property_plant_equipment": "tangible fixed assets",
    "f_within_creditors_total": "creditors falling due within one year",
    "employees": "average number of employees",
    "turnover": "turnover",
}


def _present(v):
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _money(v):
    v = float(v)
    return f"(£{abs(v):,.0f})" if v < 0 else f"£{v:,.0f}"


def name_of(row):
    return row.get("company_name") or f"company {row['company_number']}"


def make_extract(row):
    head = f"{name_of(row)} — accounts for the period ending {row.get('period_end')} (figures in {row.get('currency') or 'GBP'})"
    lines = [head]
    for col, label in EXTRACT_ROWS:
        v = row.get(col)
        if _present(v):
            lines.append(f"  {label}: {int(round(float(v)))}" if col == "employees" else f"  {label}: {_money(v)}")
    basis = "; ".join(x for x in [row.get("accounts_type"), row.get("taxonomy")] if x)
    if basis:
        lines.append(f"  Basis of preparation: {basis}")
    return "\n".join(lines)


def load_dataset():
    import pandas as pd
    import pyarrow.parquet as pq
    global DATASET_NAME
    base = config.OUT_DIR
    dirs = sorted(base.glob("product1_v*"))
    if not dirs:
        sys.exit(f"No product1_v* dataset found under {base}. Run build_product1.py first.")
    data_dir = dirs[-1]
    DATASET_NAME = data_dir.name
    parts = sorted(data_dir.glob("product1-*.parquet"))
    print(f"  loading {len(parts)} part(s) from {data_dir.name} ...")
    cols = [c for c in USE_COLS if c in pq.ParquetFile(parts[0]).schema.names]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in parts], ignore_index=True)
    df = df.sort_values("period_end", ascending=False).drop_duplicates("company_number", keep="first")
    print(f"  {len(df):,} unique companies (latest period each)")
    return df


def rows_with(df, col, named=True):
    import pandas as pd
    sub = df[df[col].notna()]
    if named:
        sub = sub[sub["company_name"].notna()]
    return sub


def main():
    import pandas as pd
    random.seed(SEED)
    print("Loading dataset ...")
    df = load_dataset()
    items = []
    uid = [0]

    def add(category, answer_type, question, answer, context=None, **extra):
        uid[0] += 1
        it = {"id": f"q{uid[0]:04d}", "category": category, "answer_type": answer_type,
              "question": question, "answer": answer}
        if context is not None:
            it["context"] = context
        it.update(extra)
        items.append(it)

    # ---- extraction (380) ----
    alloc = {"net_assets": 90, "current_assets": 55, "cash": 50,
             "property_plant_equipment": 50, "f_within_creditors_total": 45, "employees": 50, "turnover": 40}
    for field, n in alloc.items():
        pool = rows_with(df, field).sample(min(n, len(rows_with(df, field))), random_state=SEED)
        label = EXTRACT_FIELDS[field]
        for _, row in pool.iterrows():
            r = row.to_dict()
            ans = int(round(float(r[field]))) if field == "employees" else float(r[field])
            q = (f"Below is an extract from a UK company's accounts.\n\n{make_extract(r)}\n\n"
                 f"Question: What was the company's {label} for this period? "
                 f"Answer with a single {'whole number' if field=='employees' else 'figure in pounds'}.")
            add("extraction", "numeric", q, ans,
                rel_tol=0.005 if field != "employees" else 0.0, abs_tol=1.0,
                meta={"company_number": r["company_number"], "field": field})

    # ---- not_disclosed: filleted companies, ask turnover (150) ----
    fill = df[(df["turnover"].isna()) & (df["net_assets"].notna()) & (df["company_name"].notna())]
    fill = fill.sample(min(150, len(fill)), random_state=SEED)
    for _, row in fill.iterrows():
        r = row.to_dict()
        q = (f"Below is an extract from a UK company's accounts.\n\n{make_extract(r)}\n\n"
             f"Question: What was the company's turnover for this period? "
             f"If it is not disclosed in these accounts, say so.")
        add("not_disclosed", "not_disclosed", q, "NOT_DISCLOSED",
            meta={"company_number": r["company_number"]})

    # ---- comparison: highest net assets among 5 (200) ----
    comp_pool = df[(df["net_assets"].notna()) & (df["company_name"].notna())]
    for _ in range(200):
        five = comp_pool.sample(5, random_state=random.randint(0, 1_000_000))
        rows = [r for _, r in five.iterrows()]
        listing = "\n".join(f"  - {name_of(r.to_dict())}: net assets {_money(r['net_assets'])}" for r in rows)
        winner = max(rows, key=lambda r: float(r["net_assets"]))
        q = (f"Five UK companies and their net assets:\n{listing}\n\n"
             f"Question: Which company had the highest net assets? Answer with the company name.")
        add("comparison", "choice", q, name_of(winner.to_dict()),
            meta={"company_number": winner["company_number"]})

    # ---- ratio: debt-to-equity (150) ----
    ratio_pool = df[(df["total_liabilities"].notna()) & (df["net_assets"].notna()) &
                    (df["net_assets"] > 0) & (df["total_liabilities"] > 0) & (df["company_name"].notna())]
    ratio_pool = ratio_pool.sample(min(150, len(ratio_pool)), random_state=SEED)
    for _, row in ratio_pool.iterrows():
        r = row.to_dict()
        liab, eq = float(r["total_liabilities"]), float(r["net_assets"])
        ans = round(liab / eq, 2)
        q = (f"{name_of(r)} had total liabilities of {_money(liab)} and net assets (equity) of {_money(eq)}.\n"
             f"Question: What is its debt-to-equity ratio (total liabilities ÷ equity)? "
             f"Answer to two decimal places.")
        add("ratio", "numeric", q, ans, rel_tol=0.02, abs_tol=0.05,
            meta={"company_number": r["company_number"]})

    # ---- boolean: balance-sheet insolvent? (120, mixing solvent/insolvent) ----
    neg = df[(df["net_assets"].notna()) & (df["net_assets"] < 0) & (df["company_name"].notna())]
    pos = df[(df["net_assets"].notna()) & (df["net_assets"] > 0) & (df["company_name"].notna())]
    chosen = pd.concat([neg.sample(min(60, len(neg)), random_state=SEED),
                        pos.sample(min(60, len(pos)), random_state=SEED)])
    for _, row in chosen.iterrows():
        r = row.to_dict()
        na = float(r["net_assets"])
        q = (f"{name_of(r)} reported net assets of {_money(na)} at its period end.\n"
             f"Question: Is the company balance-sheet insolvent (i.e. net assets are negative)? "
             f"Answer yes or no.")
        add("boolean", "boolean", q, bool(na < 0), meta={"company_number": r["company_number"]})

    # ---- write outputs ----
    out_dir = config.OUT_DIR / "benchmark_v1"
    out_dir.mkdir(parents=True, exist_ok=True)
    random.shuffle(items)
    for i, it in enumerate(items, 1):
        it["id"] = f"q{i:04d}"
    with open(out_dir / "benchmark.jsonl", "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")

    cats = {}
    for it in items:
        cats[it["category"]] = cats.get(it["category"], 0) + 1
    stats = {"total": len(items), "by_category": cats, "seed": SEED,
             "source_dataset": DATASET_NAME}
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")

    sample = items[:12]
    md = ["# Benchmark — sample of 12 items\n", f"Total: {len(items)} | {json.dumps(cats)}\n"]
    for it in sample:
        md.append(f"## {it['id']} — {it['category']} ({it['answer_type']})")
        md.append("```\n" + it["question"] + "\n```")
        md.append(f"**Verified answer:** `{it['answer']}`\n")
    (out_dir / "benchmark_sample.md").write_text("\n".join(md), encoding="utf-8")

    print(f"\nDONE: wrote {len(items)} items -> {out_dir}")
    print("by category:", json.dumps(cats))


if __name__ == "__main__":
    main()

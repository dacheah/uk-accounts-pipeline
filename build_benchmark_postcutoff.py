"""
build_benchmark_postcutoff.py — the CONTAMINATION-CONTROL slice.

Same task and question mix as the hard benchmark, but built exclusively from
filings that Companies House PUBLISHED after every evaluated model was released
(threshold in model_cutoffs.json). Content published after a model's release
cannot be in its training data, so scores on this slice bound the contamination
question the main benchmarks cannot.

Unlike build_benchmark_hard.py this script needs no product1 dataset: it parses
the daily archives directly (parse_ixbrl -> normalise) and computes ground truth
on the fly, so the slice is reproducible from the public daily ZIPs alone.

Run (downloads eligible daily ZIPs into <data>/raw/daily/ as needed):
    python build_benchmark_postcutoff.py                # all eligible days, capped
    python build_benchmark_postcutoff.py --max-days 5   # fewer days
    python build_benchmark_postcutoff.py --no-download  # use already-downloaded ZIPs

Evaluate:
    python evaluate.py --provider anthropic --benchmark <data>/processed/benchmark_postcutoff_v1/benchmark.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
import tempfile
import zipfile
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
import config            # noqa: E402
import parse_ixbrl       # noqa: E402
import normalise         # noqa: E402
import download as dl    # noqa: E402
from build_benchmark_hard import render_redacted, LABELS  # noqa: E402

SEED = 11
N_PER = {"net_assets": 90, "employees": 70, "cash": 50, "current_assets": 40,
         "f_within_creditors_total": 30}
N_HALLUCINATION = 70
PROMPT = ("Below is a UK company's filed accounts (inline XBRL rendered to text; some "
          "personal data redacted).\n\n{ctx}\n\nQuestion: What was the company's {label} "
          "for the period ending {pe}? Answer with a single {unit}.")
_ZIP_DATE = re.compile(r"Accounts_Bulk_Data-(\d{4}-\d{2}-\d{2})\.zip")


def load_cutoffs() -> dict:
    c = json.loads((HERE / "model_cutoffs.json").read_text(encoding="utf-8"))
    latest = max(date.fromisoformat(m["released"]) for m in c["models"])
    threshold = latest + timedelta(days=1)
    assert c["threshold_date"] == threshold.isoformat(), (
        f"model_cutoffs.json threshold_date {c['threshold_date']} != computed {threshold} — update the file")
    return c


def eligible_zips(raw_daily: Path, threshold: str, max_days: int, do_download: bool) -> list[Path]:
    raw_daily.mkdir(parents=True, exist_ok=True)
    have = {m.group(1): p for p in raw_daily.glob("Accounts_Bulk_Data-*.zip")
            if (m := _ZIP_DATE.search(p.name)) and m.group(1) >= threshold}
    if do_download:
        listed = [f for f in dl.list_accounts_files() if f["date"] >= threshold]
        for f in listed:
            if f["date"] in have:
                continue
            if len(have) >= max_days:
                break
            have[f["date"]] = dl.download(f["url"], raw_daily / f["name"])
    zips = [have[d] for d in sorted(have)][:max_days]
    if not zips:
        sys.exit(f"No daily ZIPs dated >= {threshold} in {raw_daily} (try without --no-download).")
    return zips


def parse_member(zf: zipfile.ZipFile, member: str):
    """Parse one filing from the archive; returns (record, raw_bytes) or None."""
    try:
        data = zf.read(member)
        with tempfile.NamedTemporaryFile(suffix=Path(member).suffix, delete=False) as t:
            t.write(data)
            tmp = Path(t.name)
        try:
            rec = normalise.normalise(parse_ixbrl.extract_facts(tmp))
        finally:
            tmp.unlink(missing_ok=True)
        return rec, data
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the post-cutoff contamination-control benchmark slice.")
    ap.add_argument("--max-days", type=int, default=8, help="max number of daily archives to use")
    ap.add_argument("--per-day", type=int, default=1500, help="filings sampled per daily archive")
    ap.add_argument("--no-download", action="store_true", help="only use ZIPs already on disk")
    a = ap.parse_args()
    random.seed(SEED)

    cut = load_cutoffs()
    threshold = cut["threshold_date"]
    print(f"Threshold: filings published on/after {threshold} "
          f"(latest evaluated model: {max(cut['models'], key=lambda m: m['released'])['name']})")

    raw_daily = config.RAW_DIR / "daily"
    zips = eligible_zips(raw_daily, threshold, a.max_days, not a.no_download)
    print(f"Using {len(zips)} daily archive(s): {', '.join(p.name for p in zips)}")

    # pools keyed like the hard benchmark
    pools: dict[str, list] = {f: [] for f in N_PER}
    trap: list = []
    zip_meta = []
    for zp in zips:
        zdate = _ZIP_DATE.search(zp.name).group(1)
        zf = zipfile.ZipFile(zp)
        members = [n for n in zf.namelist() if n.lower().endswith((".html", ".xml"))]
        random.shuffle(members)
        members = members[: a.per_day]
        zip_meta.append({"zip": zp.name, "published": zdate, "sampled": len(members),
                         "sha256": hashlib.sha256(zp.read_bytes()).hexdigest()})
        print(f"  {zp.name}: sampling {len(members)} filings ...")
        for mname in members:
            out = parse_member(zf, mname)
            if not out:
                continue
            rec, data = out
            rec["_zip"] = zp.name
            rec["_published"] = zdate
            rec["_member"] = mname
            rec["_bytes"] = data
            fw = (rec.get("funding_within_one_year") or {}).get("creditors_total")
            vals = {"net_assets": rec.get("net_assets"), "employees": rec.get("employees"),
                    "cash": rec.get("cash"), "current_assets": rec.get("current_assets"),
                    "f_within_creditors_total": fw}
            for f, v in vals.items():
                ok = v is not None and not (isinstance(v, float) and math.isnan(v))
                if ok and (f == "net_assets" or v > 0) and rec.get("period_end"):
                    pools[f].append((rec, v))
            if (rec.get("turnover") is None and rec.get("net_assets") is not None
                    and rec.get("dormant") is not True and rec.get("period_end")):
                trap.append(rec)
        zf.close()

    print("pool sizes:", {k: len(v) for k, v in pools.items()}, "| trap:", len(trap))

    items, uid = [], 0

    def add(category, answer_type, field, question, answer, rec, **extra):
        nonlocal uid
        uid += 1
        it = {"id": f"p{uid:04d}", "category": category, "answer_type": answer_type,
              "question": question, "answer": answer,
              "meta": {"company_number": rec.get("company_number"), "field": field,
                       "published": rec["_published"], "source_zip": rec["_zip"]}}
        it.update(extra)
        items.append(it)

    for field, n in N_PER.items():
        random.shuffle(pools[field])
        got = 0
        for rec, v in pools[field]:
            if got >= n:
                break
            ctx = render_redacted(rec["_bytes"])
            if not ctx:
                continue
            unit = "whole number" if field == "employees" else "figure in pounds"
            ans = int(round(float(v))) if field == "employees" else float(v)
            q = PROMPT.format(ctx=ctx, label=LABELS[field], pe=rec["period_end"], unit=unit)
            add("extraction", "numeric", field, q, ans, rec,
                rel_tol=0.005 if field != "employees" else 0.0, abs_tol=1.0)
            got += 1

    random.shuffle(trap)
    got = 0
    for rec in trap:
        if got >= N_HALLUCINATION:
            break
        ctx = render_redacted(rec["_bytes"])
        if not ctx:
            continue
        q = PROMPT.format(ctx=ctx, label="turnover", pe=rec["period_end"], unit="figure in pounds")
        add("turnover_trap", "not_disclosed", "turnover", q, "NOT_DISCLOSED", rec)
        got += 1

    random.shuffle(items)
    for i, it in enumerate(items, 1):
        it["id"] = f"p{i:04d}"

    out_dir = config.OUT_DIR / "benchmark_postcutoff_v1"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "benchmark.jsonl", "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    cats: dict[str, int] = {}
    for it in items:
        cats[it["category"]] = cats.get(it["category"], 0) + 1
    (out_dir / "provenance.json").write_text(json.dumps({
        "purpose": "contamination-control slice: filings published after every evaluated model's release",
        "cutoffs": cut, "archives": zip_meta, "total_items": len(items),
        "by_category": cats, "seed": SEED}, indent=2), encoding="utf-8")
    print(f"\nDONE: wrote {len(items)} post-cutoff items -> {out_dir}")
    print("by category:", json.dumps(cats))
    print("Evaluate with: python evaluate.py --provider <p> --benchmark", out_dir / "benchmark.jsonl")


if __name__ == "__main__":
    main()

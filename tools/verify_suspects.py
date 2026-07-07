"""verify_suspects.py — lock the <style>/<script> renderer fix.

For the two items every model missed 5/5 (p0225 = SC782439 net_assets, p0233 =
14336217 current_assets), this re-renders the actual filing with the FIXED
render_redacted and checks that (a) the CSS no longer leaks into the context and
(b) the balance-sheet figure the parser recorded is now visible to a model.

It also re-parses each filing so you can confirm the parser's ground truth
(3,689 / 971) against the figure shown in the document. No API calls, no cost.

Run from the pipeline repo root:
    python tools/verify_suspects.py
Needs the post-cutoff daily ZIPs already on disk (config.RAW_DIR/daily).
"""
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))          # repo root: build_benchmark_hard lives here
sys.path.insert(0, str(HERE / "src"))  # pipeline modules: config, parse_ixbrl, normalise
import config            # noqa: E402
import parse_ixbrl       # noqa: E402
import normalise         # noqa: E402
from build_benchmark_hard import render_redacted  # noqa: E402

# (company number, field, parser ground truth)
TARGETS = [
    ("SC782439", "net_assets", 3689.0),
    ("14336217", "current_assets", 971.0),
]


def parse_bytes(data: bytes, suffix: str):
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as t:
        t.write(data)
        tmp = Path(t.name)
    try:
        return normalise.normalise(parse_ixbrl.extract_facts(tmp))
    finally:
        tmp.unlink(missing_ok=True)


def check(cno: str, field: str, expected: float, zips: list[Path]) -> bool:
    for zp in zips:
        with zipfile.ZipFile(zp) as zf:
            for name in zf.namelist():
                if cno in name and name.lower().endswith((".html", ".xml")):
                    data = zf.read(name)
                    rec = parse_bytes(data, Path(name).suffix)
                    ctx = render_redacted(data)
                    exp = str(int(expected))
                    flat = ctx.replace(",", "")
                    present = exp in flat
                    css_leak = ("font-family" in ctx) or ("margin:" in ctx) or ("padding:" in ctx)
                    print(f"\n=== {cno}  ({zp.name} :: {name}) ===")
                    print(f"  parser {field:14s}: {rec.get(field)!r}   (expected {expected})")
                    print(f"  context length     : {len(ctx)}")
                    print(f"  CSS still leaking? : {css_leak}   (want False)")
                    print(f"  figure {exp} visible?: {present}   (want True)")
                    i = flat.find(exp)
                    if i >= 0:
                        print(f"  snippet            : ...{flat[max(0, i-55):i+len(exp)+15]}...")
                    ok = present and not css_leak
                    print(f"  RESULT             : {'PASS' if ok else 'FAIL'}")
                    return ok
    print(f"\n=== {cno}: NOT FOUND in {len(zips)} daily ZIP(s) ===")
    return False


def main() -> None:
    raw_daily = config.RAW_DIR / "daily"
    zips = sorted(raw_daily.glob("Accounts_Bulk_Data-*.zip"))
    if not zips:
        sys.exit(f"No daily ZIPs in {raw_daily} — run build_benchmark_postcutoff.py first.")
    print(f"Scanning {len(zips)} daily ZIP(s) for the two suspect filings ...")
    results = [check(cno, field, exp, zips) for cno, field, exp in TARGETS]
    print("\n" + "=" * 60)
    print("OVERALL:", "PASS — fix locked" if all(results) else "review the FAIL lines above")


if __name__ == "__main__":
    main()

"""Quick demonstration: parse the two sample filings and print the clean records."""
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import parse_ixbrl   # noqa: E402
import normalise     # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

for name in ["frs102_full_sample.html", "frs105_micro_sample.html"]:
    extracted = parse_ixbrl.extract_facts(FIXTURES / name)
    record = normalise.normalise(extracted)
    print("=" * 64)
    print(name)
    print(f"  raw facts found : {len(extracted['facts'])}")
    print(f"  taxonomy ref    : {extracted['taxonomy_refs'][0] if extracted['taxonomy_refs'] else None}")
    print("  clean record:")
    print(json.dumps(record, indent=4))

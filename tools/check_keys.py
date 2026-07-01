"""Diagnostic: shows which settings the program reads from secrets.env. Prints names and
lengths only, never the secret values. Run from the repo root:  python tools/check_keys.py"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import config  # noqa: E402  (loads secrets.env into the environment)

secrets = config.PROJECT_ROOT / "secrets.env"
print(f"Looking for secrets.env at:\n  {secrets}")
print(f"File exists: {secrets.exists()}\n")

if secrets.exists():
    names = [ln.split("=", 1)[0].strip()
             for ln in secrets.read_text(encoding="utf-8").splitlines()
             if "=" in ln and not ln.strip().startswith("#")]
    print(f"Setting names found IN the file: {names}\n")

print("Loaded into the environment:")
for k in ("CH_DATA_DIR", "CH_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
    v = os.environ.get(k)
    print(f"  {k:<20} {'SET (' + str(len(v)) + ' chars)' if v else 'MISSING'}")

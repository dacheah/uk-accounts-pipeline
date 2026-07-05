# UK Companies House Accounts Pipeline

A modular Python pipeline that downloads UK Companies House bulk accounts data, parses the
iXBRL filings, extracts key financial line items, normalises them across the **FRS 102**
(full UK GAAP) and **FRS 105** (micro-entity) taxonomies, and joins each record to company
metadata — **excluding personal data**.

Built once, used twice: it powers both **Product 1** (general UK company financials + a
benchmark) and **Product 2** (securitisation SPVs & non-bank lenders).

> **Benchmark finding:** frontier LLMs read UK accounts very well — ~98–99% on clean data,
> ~97–99% on raw filings. The value isn't that models fail; it's turning 3.7M messy, filleted,
> multi-taxonomy filings into clean, verified, provenance-tracked data at scale. See
> [`BENCHMARK_RESULTS.md`](../BENCHMARK_RESULTS.md) and [`DATA_QUALITY_NOTES.md`](../DATA_QUALITY_NOTES.md).

## Structure
```
src/
  config.py          paths, endpoints, secure key loading, OGL attribution
  download.py        fetch bulk accounts + company-data + monthly archives
  parse_ixbrl.py     extract tagged facts from iXBRL (handles scale, parenthesised negatives)
  normalise.py       map FRS 102 / FRS 105 to one schema; derive totals; per-figure provenance
  metadata.py        join company metadata, exclude personal data
  build_dataset.py   orchestrate a single archive -> CSV/Parquet + manifest
build_product1.py    full Product 1 build (resumable, parallel, per-month)
build_benchmark.py   1,000 verified Q&A from the structured dataset
build_benchmark_hard.py  350 Q&A from raw redacted filings (the hard test)
build_benchmark_postcutoff.py  contamination-control slice: same task, but only filings
                     published AFTER every evaluated model's release (model_cutoffs.json);
                     ground truth computed directly from the public daily archives
evaluate.py          run a frontier model against a benchmark, score it
benchmark_grading.py answer parsing + grading
smoke_test.py        validate the pipeline against one real daily file
tools/               diagnostics (check_keys.py, regrade.py)
```

## Setup
```
pip install -r requirements.txt
cp config.example.env secrets.env        # then edit secrets.env
```
`secrets.env` (gitignored, never published) holds:
- `CH_DATA_DIR` — where to store downloads + datasets (e.g. a big drive: `D:\UKHouseData`)
- `CH_API_KEY` — Companies House API key (Product 2 / spot checks)
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` — for benchmark evaluation

## Usage
```
python smoke_test.py                         # quick validation on one real daily file
python build_product1.py --months 12         # full dataset (resumable, parallel)
python build_benchmark.py                    # generate the clean benchmark
python build_benchmark_hard.py               # generate the raw-filing benchmark
python build_benchmark_postcutoff.py         # contamination-control slice (post-cutoff filings)
python evaluate.py --provider anthropic      # score a model (add --hard for the raw set)
```

## Licence
- **Code:** PolyForm Noncommercial License 1.0.0 — free for non-commercial use; **commercial
  use requires a licence** (see [`LICENSE`](LICENSE); contact the author for commercial terms).
- **Data outputs:** Companies House data under the **Open Government Licence v3.0** —
  "Contains public sector information licensed under the Open Government Licence v3.0."
- **Datasets are not in this repo.** Product 1 (UK company financials) is distributed separately
  and gated; Product 2's curated SPV/lender list is a commercial asset and is withheld.

See [`DATA_QUALITY_NOTES.md`](DATA_QUALITY_NOTES.md) for coverage, provenance, and honest limitations.

## Benchmark
The companion **UK-accounts LLM benchmark** (1,000 verified Q&A + a 5-model, proprietary-vs-open
evaluation) lives in a separate repository under CC BY 4.0. Headline finding: frontier models —
proprietary *and* open-weight/self-hostable — read UK accounts at **~96–99.6%**, so the scarce,
defensible asset is the clean, verified data, not the model.

## Author
**Daniel Cheah** — [danielcheah.com](https://danielcheah.com) · [LinkedIn](https://au.linkedin.com/in/dcheah)

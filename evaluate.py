"""
evaluate.py — run a frontier model against the benchmark and score it.

Supports Anthropic (Claude), OpenAI (GPT), and Google (Gemini). Reads API keys from
secrets.env (same file as your other keys). Grades with benchmark_grading.py and reports
accuracy overall and by category.

Setup (one-off):
    pip install anthropic openai google-genai
    # add to secrets.env:
    #   ANTHROPIC_API_KEY=...
    #   OPENAI_API_KEY=...
    #   GEMINI_API_KEY=...

Usage:
    python evaluate.py --provider anthropic --limit 50      # cheap smoke test first
    python evaluate.py --provider anthropic                 # full 1,000
    python evaluate.py --provider openai
    python evaluate.py --provider google --model gemini-3.5-pro
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
import config  # noqa: E402  (importing also loads secrets.env into the environment)
import benchmark_grading as grading  # noqa: E402

DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.5",
    "google": "gemini-2.5-pro",
    "openrouter": "deepseek/deepseek-v4-pro",   # open-weight; pass any slug via --model
}
KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

SYSTEM = (
    "You are a careful financial analyst working with UK company accounts (UK GAAP / FRS 102 "
    "and FRS 105). Answer the question as concisely as possible. For a numeric answer, reply "
    "with just the figure in pounds. If a figure is NOT disclosed in the accounts provided, "
    "reply 'not disclosed' rather than guessing."
)


def call_anthropic(model, prompt, key):
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(model=model, max_tokens=300, system=SYSTEM,
                                 messages=[{"role": "user", "content": prompt}])
    return "".join(getattr(b, "text", "") for b in msg.content)


def call_openai(model, prompt, key):
    from openai import OpenAI
    client = OpenAI(api_key=key)
    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
    return r.choices[0].message.content or ""


def call_google(model, prompt, key):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    r = client.models.generate_content(
        model=model, contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM, max_output_tokens=4096))
    txt = (r.text or "").strip()
    if not txt and getattr(r, "candidates", None):  # thinking models split text across parts
        fr = None
        for c in r.candidates:
            fr = getattr(c, "finish_reason", fr)
            content = getattr(c, "content", None)
            for p in (getattr(content, "parts", None) or []):
                if getattr(p, "text", None):
                    txt += p.text
        txt = txt.strip() or f"<no text; finish_reason={fr}>"
    return txt


def call_openrouter(model, prompt, key):
    """Open-weight models (DeepSeek, GLM, Qwen, ...) via OpenRouter's OpenAI-compatible API."""
    from openai import OpenAI
    client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")
    r = client.chat.completions.create(
        model=model, max_tokens=4096,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
    msg = r.choices[0].message
    return (msg.content or "").strip() or (getattr(msg, "reasoning", None) or "").strip()


CALLERS = {"anthropic": call_anthropic, "openai": call_openai, "google": call_google,
           "openrouter": call_openrouter}


def load_items(path, limit=None):
    items = [json.loads(line) for line in open(path, encoding="utf-8")]
    return items[:limit] if limit else items


def run(provider, model, limit, concurrency, benchmark_path):
    caller = CALLERS[provider]
    key = os.environ.get(KEY_ENV[provider], "").strip().strip('"').strip("'")
    if not key:
        sys.exit(f"No API key found. Add a line to secrets.env (no quotes, no spaces):\n"
                 f"  {KEY_ENV[provider]}=your-key-here\nthen re-run.")
    items = load_items(benchmark_path, limit)
    print(f"Evaluating {provider}/{model} on {len(items)} items (concurrency {concurrency}) ...")

    results = [None] * len(items)
    errors = [0]
    first_error = [None]

    def work(i, item):
        try:
            ans = caller(model, item["question"], key)
            return i, ans, grading.grade(item, ans)
        except Exception as e:
            errors[0] += 1
            if first_error[0] is None:
                first_error[0] = f"{type(e).__name__}: {e}"
            return i, f"<error: {e}>", False

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(work, i, it) for i, it in enumerate(items)]
        for f in as_completed(futs):
            i, ans, correct = f.result()
            results[i] = (ans, correct)
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(items)} ...")

    # tally — errored API calls are EXCLUDED from accuracy and reported separately
    by_cat = {}
    overall_correct = 0
    scored = 0
    detail = []
    for item, (ans, correct) in zip(items, results):
        c = item["category"]
        errored = isinstance(ans, str) and ans.startswith("<error")
        detail.append({"id": item["id"], "category": c, "field": item.get("meta", {}).get("field"),
                       "correct": bool(correct), "errored": errored,
                       "truth": item["answer"], "model_answer": ans})
        if errored:
            continue
        by_cat.setdefault(c, [0, 0])
        by_cat[c][1] += 1
        scored += 1
        if correct:
            by_cat[c][0] += 1
            overall_correct += 1

    summary = {
        "provider": provider, "model": model, "n": len(items), "scored": scored,
        "overall_accuracy": round(overall_correct / scored, 4) if scored else 0.0,
        "by_category": {c: round(v[0] / v[1], 4) for c, v in by_cat.items()},
        "errors": errors[0], "seconds": round(time.time() - t0, 1),
    }
    out = benchmark_path.parent / f"results_{provider}_{model.replace('/', '-')}.json"
    out.write_text(json.dumps({"summary": summary, "detail": detail}, indent=2), encoding="utf-8")

    print("\n" + "=" * 56)
    print(f"{provider}/{model}  —  scored {scored}/{len(items)}  (errors: {errors[0]})")
    if errors[0]:
        print(f"  first error: {first_error[0]}")
    print(f"OVERALL accuracy: {summary['overall_accuracy']:.1%}")
    for c, acc in summary["by_category"].items():
        print(f"  {c:<14} {acc:.1%}")
    wrong = [d for d in detail if not d["correct"] and not d["errored"]][:6]
    if wrong:
        print("sample wrong answers (field | truth vs model):")
        for d in wrong:
            print(f"  [{d['category']}/{d['field']}] truth={d['truth']!r}  model={str(d['model_answer'])[:80]!r}")
    print(f"saved -> {out.name}")
    print("=" * 56)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Evaluate a frontier model on the UK-accounts benchmark.")
    ap.add_argument("--provider", required=True, choices=["anthropic", "openai", "google", "openrouter"])
    ap.add_argument("--model", default=None, help="override the model id")
    ap.add_argument("--limit", type=int, default=None, help="evaluate only the first N (cheap test)")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--benchmark", default=None, help="path to a benchmark .jsonl (default: clean benchmark_v1)")
    ap.add_argument("--hard", action="store_true", help="use the hard benchmark (benchmark_hard_v1)")
    a = ap.parse_args()
    if a.benchmark:
        bp = Path(a.benchmark)
    elif a.hard:
        bp = config.OUT_DIR / "benchmark_hard_v1" / "benchmark.jsonl"
    else:
        bp = config.OUT_DIR / "benchmark_v1" / "benchmark.jsonl"
    run(a.provider, a.model or DEFAULT_MODELS[a.provider], a.limit, a.concurrency, bp)

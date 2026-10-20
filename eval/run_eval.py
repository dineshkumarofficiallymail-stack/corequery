"""Evaluate CoreQuery against the golden question set.

Metric: **execution accuracy** — the generated SQL is correct if executing it returns the same
result set as the golden SQL (order-insensitive unless the question implies ordering, i.e. golden
SQL has ORDER BY; column names are ignored, values are compared with numeric tolerance).

Usage:
    python -m eval.run_eval                 # full set, uses provider from .env
    python -m eval.run_eval --limit 5       # first 5 questions
    python -m eval.run_eval --difficulty hard
    python -m eval.run_eval --check-golden  # just verify the golden SQL runs (no LLM)
Writes eval/results/<timestamp>.json and prints a summary table.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date, datetime
from pathlib import Path

from app.config import settings
from app.core.guardrails import ReadOnlyExecutor
from app.core.pipeline import NLToSQLPipeline
from app.core.schema_catalog import SchemaCatalog
from app.llm.provider import build_provider

GOLDEN = Path(__file__).with_name("golden.jsonl")
RESULTS_DIR = Path(__file__).with_name("results")
TODAY = date(2026, 9, 30)


def load_golden(difficulty: str | None, limit: int | None) -> list[dict]:
    items = [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    if difficulty:
        items = [i for i in items if i["difficulty"] == difficulty]
    return items[:limit] if limit else items


def _norm(v):
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, str):
        try:
            return round(float(v), 2)
        except ValueError:
            return v.strip().lower()
    return v


def results_match(golden_rows: list[tuple], pred_rows: list[list], ordered: bool) -> bool:
    g = [tuple(_norm(v) for v in r) for r in golden_rows]
    p = [tuple(_norm(v) for v in r) for r in pred_rows]
    if len(g) != len(p):
        return False
    if g and p and len(g[0]) != len(p[0]):
        # Allow the prediction to include extra descriptive columns: compare on golden columns' values as a multiset.
        return _subset_match(g, p)
    return g == p if ordered else sorted(map(repr, g)) == sorted(map(repr, p))


def _subset_match(g: list[tuple], p: list[tuple]) -> bool:
    """Every golden row's values must appear (as a sub-multiset) in some predicted row, one-to-one."""
    remaining = list(p)
    for gr in g:
        hit = next((i for i, pr in enumerate(remaining) if all(_contains(pr, v) for v in gr)), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def _contains(row: tuple, v) -> bool:
    for x in row:
        if x == v:
            return True
        if isinstance(x, float) and isinstance(v, float) and math.isclose(x, v, rel_tol=1e-4):
            return True
    return False


def main() -> None:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--difficulty", choices=["easy", "medium", "hard"])
    ap.add_argument("--check-golden", action="store_true")
    args = ap.parse_args()

    items = load_golden(args.difficulty, args.limit)
    executor = ReadOnlyExecutor(settings.database_path, settings.query_timeout_seconds)

    if args.check_golden:
        for it in items:
            cols, rows, _ = executor.execute(it["sql"])
            print(f"#{it['id']:<3} {len(rows):>4} rows  {', '.join(cols)}")
        print(f"\nAll {len(items)} golden queries executed.")
        return

    catalog = SchemaCatalog(settings.database_path)
    pipeline = NLToSQLPipeline(build_provider(settings), catalog, executor, settings.max_result_rows,
                               settings.max_repair_attempts, today=TODAY, narrate=False)

    records = []
    correct = 0
    t_start = time.perf_counter()
    for it in items:
        _, golden_rows, _ = executor.execute(it["sql"])
        ordered = "order by" in it["sql"].lower()
        res = pipeline.run(it["question"])
        ok = res.success and results_match(golden_rows, res.rows, ordered)
        correct += ok
        records.append({
            "id": it["id"], "difficulty": it["difficulty"], "question": it["question"], "correct": ok,
            "executed": res.success, "attempts": len(res.attempts), "repaired": len(res.attempts) > 1 and res.success,
            "golden_sql": it["sql"], "predicted_sql": res.sql, "error": res.error,
            "golden_rows": len(golden_rows), "predicted_rows": res.row_count,
            "latency_ms": round(res.total_ms), "tokens": res.prompt_tokens + res.completion_tokens,
        })
        flag = "PASS" if ok else ("EXEC" if res.success else "FAIL")
        print(f"[{flag}] #{it['id']:<3} ({it['difficulty']:<6}) {it['question'][:70]}")
        time.sleep(1.0)  # be gentle with free-tier rate limits

    n = len(records)
    by_diff = {}
    for r in records:
        d = by_diff.setdefault(r["difficulty"], [0, 0])
        d[0] += r["correct"]
        d[1] += 1
    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"), "model": settings.llm_model,
        "provider": settings.llm_provider, "n": n, "execution_accuracy": round(100 * correct / n, 1) if n else 0,
        "executed_without_error": round(100 * sum(r["executed"] for r in records) / n, 1) if n else 0,
        "repaired_successfully": sum(r["repaired"] for r in records),
        "avg_latency_ms": round(sum(r["latency_ms"] for r in records) / n) if n else 0,
        "by_difficulty": {k: f"{v[0]}/{v[1]}" for k, v in by_diff.items()},
        "wall_time_s": round(time.perf_counter() - t_start, 1),
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{summary['timestamp'].replace(':', '-')}.json"
    out.write_text(json.dumps({"summary": summary, "records": records}, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"Model: {summary['model']}   Questions: {n}")
    print(f"Execution accuracy : {summary['execution_accuracy']}%")
    print(f"Ran without error  : {summary['executed_without_error']}%")
    print(f"Fixed by self-repair: {summary['repaired_successfully']}")
    print(f"By difficulty      : {summary['by_difficulty']}")
    print(f"Avg latency        : {summary['avg_latency_ms']} ms")
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()

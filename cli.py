"""Interactive terminal client:  python cli.py  or  python cli.py "your question" """
from __future__ import annotations

import sys
from datetime import date

from app.config import settings
from app.core.guardrails import ReadOnlyExecutor
from app.core.pipeline import NLToSQLPipeline
from app.core.schema_catalog import SchemaCatalog
from app.llm.provider import build_provider


def main() -> None:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    catalog = SchemaCatalog(settings.database_path)
    pipeline = NLToSQLPipeline(build_provider(settings), catalog, ReadOnlyExecutor(settings.database_path),
                               settings.max_result_rows, settings.max_repair_attempts, today=date(2026, 9, 30))
    questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else None
    print(f"CoreQuery ({settings.llm_model}). Type a question, or 'exit'.\n")
    while True:
        q = questions.pop(0) if questions else input("you> ").strip()
        if not q or q.lower() in {"exit", "quit"}:
            break
        res = pipeline.run(q)
        print("\nSQL:\n" + res.sql)
        if res.success:
            print(f"\n{res.row_count} rows in {res.execution_ms:.0f} ms")
            if res.columns:
                print(" | ".join(res.columns))
                for r in res.rows[:15]:
                    print(" | ".join(str(v) for v in r))
                if res.row_count > 15:
                    print(f"... ({res.row_count - 15} more)")
            print("\nAnswer: " + res.answer)
        else:
            print("\nFailed: " + (res.error or ""))
        if len(res.attempts) > 1:
            print(f"(self-repaired after {len(res.attempts) - 1} failed attempt(s))")
        print()
        if questions is not None and not questions:
            break


if __name__ == "__main__":
    main()

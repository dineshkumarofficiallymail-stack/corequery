"""The NL-to-SQL pipeline.

    question
       │
       ▼
  schema linking ──► prompt (rules + relevant tables + samples)
       │
       ▼
   LLM: generate SQL
       │
       ▼
   guardrails: validate ──fail──► LLM: repair (≤ N attempts)
       │ ok                             │
       ▼                                │
   read-only execute ──error──────────┘
       │ ok
       ▼
   LLM: narrate answer  ──►  QueryResult
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date

from app.core.guardrails import ReadOnlyExecutor, SQLValidationError, strip_sql_fences, validate_sql
from app.core.schema_catalog import SchemaCatalog
from app.llm.prompts import ANSWER_SYSTEM, ANSWER_USER, REPAIR_USER, SQL_SYSTEM, SQL_USER
from app.llm.provider import LLMProvider

log = logging.getLogger(__name__)


@dataclass
class Attempt:
    sql: str
    error: str | None = None


@dataclass
class QueryResult:
    question: str
    sql: str
    columns: list[str]
    rows: list[list]
    row_count: int
    answer: str
    tables_used: list[str]
    attempts: list[Attempt]
    execution_ms: float
    total_ms: float
    model: str
    success: bool = True
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "question": self.question, "sql": self.sql, "columns": self.columns, "rows": self.rows,
            "row_count": self.row_count, "answer": self.answer, "tables_used": self.tables_used,
            "attempts": [{"sql": a.sql, "error": a.error} for a in self.attempts],
            "execution_ms": round(self.execution_ms, 1), "total_ms": round(self.total_ms, 1), "model": self.model,
            "success": self.success, "error": self.error,
            "usage": {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens},
        }


class NLToSQLPipeline:
    def __init__(self, provider: LLMProvider, catalog: SchemaCatalog, executor: ReadOnlyExecutor,
                 max_rows: int = 200, max_repair_attempts: int = 2, today: date | None = None,
                 narrate: bool = True):
        self.provider = provider
        self.catalog = catalog
        self.executor = executor
        self.max_rows = max_rows
        self.max_repair_attempts = max_repair_attempts
        self.today = today or date.today()
        self.narrate = narrate

    # ---------- public ----------
    def run(self, question: str) -> QueryResult:
        t0 = time.perf_counter()
        question = question.strip()
        attempts: list[Attempt] = []
        prompt_tokens = completion_tokens = 0
        model = "unknown"

        schema_text, tables = self.catalog.relevant_prompt(question)
        system = SQL_SYSTEM.format(today=self.today.isoformat(), schema=schema_text)
        messages = [{"role": "user", "content": SQL_USER.format(question=question)}]

        sql = ""
        columns: list[str] = []
        rows: list[tuple] = []
        exec_ms = 0.0
        last_error = ""

        for attempt_no in range(self.max_repair_attempts + 1):
            resp = self.provider.complete(system, messages)
            model = resp.model
            prompt_tokens += resp.prompt_tokens
            completion_tokens += resp.completion_tokens
            raw_sql = strip_sql_fences(resp.content)
            try:
                validated = validate_sql(raw_sql, self.catalog.table_names, self.max_rows)
                sql = validated.sql
                columns, rows, exec_ms = self.executor.execute(sql)
                attempts.append(Attempt(sql=sql))
                last_error = ""
                break
            except (SQLValidationError, TimeoutError) as e:
                last_error = str(e)
            except Exception as e:  # sqlite3 errors: bad column, syntax, etc.
                last_error = f"{type(e).__name__}: {e}"
            attempts.append(Attempt(sql=raw_sql, error=last_error))
            log.info("Attempt %d failed: %s", attempt_no + 1, last_error)
            # Feed the failure back to the model and try again.
            messages = messages + [
                {"role": "assistant", "content": f"```sql\n{raw_sql}\n```"},
                {"role": "user", "content": REPAIR_USER.format(sql=raw_sql, error=last_error)},
            ]

        if last_error:
            return QueryResult(
                question=question, sql=attempts[-1].sql, columns=[], rows=[], row_count=0,
                answer="I couldn't produce a valid query for that question. " + last_error,
                tables_used=tables, attempts=attempts, execution_ms=0.0,
                total_ms=(time.perf_counter() - t0) * 1000, model=model, success=False, error=last_error,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            )

        answer = ""
        if self.narrate:
            resp = self.provider.complete(ANSWER_SYSTEM, [{"role": "user", "content": self._answer_prompt(question, sql, columns, rows)}])
            answer = resp.content.strip()
            prompt_tokens += resp.prompt_tokens
            completion_tokens += resp.completion_tokens

        return QueryResult(
            question=question, sql=sql, columns=columns, rows=[list(r) for r in rows], row_count=len(rows),
            answer=answer, tables_used=validated.tables, attempts=attempts, execution_ms=exec_ms,
            total_ms=(time.perf_counter() - t0) * 1000, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        )

    # ---------- helpers ----------
    @staticmethod
    def _answer_prompt(question: str, sql: str, columns: list[str], rows: list[tuple], preview: int = 25) -> str:
        shown = rows[:preview]
        rendered = "\n".join(" | ".join(str(v) for v in r) for r in shown) or "(no rows)"
        truncated = f", showing first {preview}" if len(rows) > preview else ""
        return ANSWER_USER.format(question=question, sql=sql, columns=", ".join(columns),
                                  row_count=len(rows), truncated=truncated, rows=rendered)

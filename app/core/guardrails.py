"""SQL guardrails: everything that stands between an LLM's output and the database.

Defence in depth:
  1. Static validation of the SQL text (read-only, single statement, allow-listed tables).
  2. A read-only database connection (SQLite `mode=ro`) so even a bypass cannot write.
  3. Row limits and a statement timeout so a bad query cannot exhaust the server.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|GRANT|REVOKE|MERGE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)
# SQLite functions that can read the filesystem or otherwise misbehave.
FORBIDDEN_FUNCS = re.compile(r"\b(load_extension|readfile|writefile|fts3_tokenizer)\s*\(", re.IGNORECASE)


class SQLValidationError(ValueError):
    pass


@dataclass
class ValidatedSQL:
    sql: str
    tables: list[str] = field(default_factory=list)
    limit_added: bool = False


def strip_sql_fences(text: str) -> str:
    """LLMs love markdown. Pull the SQL out of ```sql ... ``` if present."""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    sql = m.group(1) if m else text
    return sql.strip().rstrip(";").strip()


def _strip_literals_and_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    sql = re.sub(r'"(?:[^"]|"")*"', '""', sql)
    return sql


def validate_sql(sql: str, allowed_tables: list[str], max_rows: int) -> ValidatedSQL:
    sql = strip_sql_fences(sql)
    if not sql:
        raise SQLValidationError("Empty SQL.")
    scrubbed = _strip_literals_and_comments(sql)

    if ";" in scrubbed:
        raise SQLValidationError("Multiple statements are not allowed.")
    first_word = scrubbed.lstrip().split(None, 1)[0].upper() if scrubbed.strip() else ""
    if first_word not in ("SELECT", "WITH"):
        raise SQLValidationError("Only SELECT queries are allowed.")
    if FORBIDDEN.search(scrubbed):
        raise SQLValidationError(f"Forbidden keyword found: {FORBIDDEN.search(scrubbed).group(1).upper()}.")
    if FORBIDDEN_FUNCS.search(scrubbed):
        raise SQLValidationError("Forbidden function call.")

    # Table allow-list: every identifier after FROM/JOIN must be a known table or a CTE.
    cte_names = {m.group(1).lower() for m in re.finditer(r"\b(\w+)\s+AS\s*\(", scrubbed, re.IGNORECASE)}
    referenced = [m.group(1) for m in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)", scrubbed, re.IGNORECASE)]
    allowed_lower = {t.lower() for t in allowed_tables}
    unknown = [t for t in referenced if t.lower() not in allowed_lower and t.lower() not in cte_names]
    if unknown:
        raise SQLValidationError(f"Unknown table(s): {', '.join(sorted(set(unknown)))}.")

    limit_added = False
    if not re.search(r"\bLIMIT\s+\d+", scrubbed, re.IGNORECASE):
        sql = f"{sql}\nLIMIT {max_rows}"
        limit_added = True
    else:
        # Clamp an oversized LIMIT.
        def clamp(m: re.Match) -> str:
            return f"LIMIT {min(int(m.group(1)), max_rows)}"
        sql = re.sub(r"\bLIMIT\s+(\d+)", clamp, sql, flags=re.IGNORECASE)

    tables = sorted({t.lower() for t in referenced if t.lower() in allowed_lower})
    return ValidatedSQL(sql=sql, tables=tables, limit_added=limit_added)


class ReadOnlyExecutor:
    """Executes validated SQL against a read-only SQLite connection with a timeout."""

    def __init__(self, db_path: str, timeout_seconds: float = 5.0):
        self.db_path = db_path
        self.timeout_seconds = timeout_seconds

    def execute(self, sql: str) -> tuple[list[str], list[tuple], float]:
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        try:
            deadline = time.monotonic() + self.timeout_seconds

            def interrupt_if_slow() -> None:
                if time.monotonic() > deadline:
                    con.interrupt()

            timer = threading.Timer(self.timeout_seconds, interrupt_if_slow)
            timer.start()
            start = time.perf_counter()
            try:
                cur = con.execute(sql)
                rows = cur.fetchall()
                columns = [d[0] for d in cur.description] if cur.description else []
            except sqlite3.OperationalError as e:
                if "interrupted" in str(e).lower():
                    raise TimeoutError(f"Query exceeded {self.timeout_seconds}s and was cancelled.") from e
                raise
            finally:
                timer.cancel()
            return columns, rows, (time.perf_counter() - start) * 1000
        finally:
            con.close()

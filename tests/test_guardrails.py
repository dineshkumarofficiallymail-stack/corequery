import pytest

from app.core.guardrails import SQLValidationError, strip_sql_fences, validate_sql

TABLES = ["accounts", "customers", "transactions", "nach_mandates", "nach_presentments", "charges", "branches"]


def test_strips_markdown_fences():
    assert strip_sql_fences("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert strip_sql_fences("SELECT 2") == "SELECT 2"


def test_allows_simple_select_and_adds_limit():
    v = validate_sql("SELECT * FROM accounts", TABLES, 100)
    assert v.limit_added and v.sql.endswith("LIMIT 100")
    assert v.tables == ["accounts"]


def test_allows_cte():
    v = validate_sql("WITH t AS (SELECT account_id FROM transactions) SELECT COUNT(*) FROM t", TABLES, 50)
    assert "transactions" in v.tables


def test_clamps_large_limit():
    v = validate_sql("SELECT * FROM accounts LIMIT 999999", TABLES, 100)
    assert "LIMIT 100" in v.sql and not v.limit_added


@pytest.mark.parametrize("sql", [
    "DELETE FROM accounts",
    "UPDATE accounts SET balance = 0",
    "DROP TABLE accounts",
    "SELECT * FROM accounts; DROP TABLE accounts",
    "PRAGMA writable_schema = 1",
    "SELECT load_extension('x')",
    "INSERT INTO charges VALUES (1)",
    "SELECT writefile('bad.txt', 'evil')",
    "SELECT readfile('bad.txt')",
    "SELECT fts3_tokenizer('bad')",
])
def test_blocks_dangerous_sql(sql):
    with pytest.raises(SQLValidationError):
        validate_sql(sql, TABLES, 100)


def test_blocks_unknown_table():
    with pytest.raises(SQLValidationError, match="Unknown table"):
        validate_sql("SELECT * FROM sqlite_master", TABLES, 100)


def test_keywords_inside_string_literals_are_fine():
    v = validate_sql("SELECT * FROM transactions WHERE narration = 'DELETE ME'", TABLES, 100)
    assert "DELETE ME" in v.sql


def test_rejects_non_select():
    with pytest.raises(SQLValidationError, match="Only SELECT"):
        validate_sql("EXPLAIN SELECT 1", TABLES, 100)


def test_empty_sql_raises():
    with pytest.raises(SQLValidationError, match="Empty SQL"):
        validate_sql("   ", TABLES, 100)


def test_case_insensitive_table_names():
    v = validate_sql("SELECT branch_name FROM BRANCHES", TABLES, 50)
    assert "branches" in v.tables


def test_multiple_statements_rejected():
    with pytest.raises(SQLValidationError, match="Multiple statements"):
        validate_sql("SELECT 1 FROM accounts; SELECT 2 FROM customers", TABLES, 100)

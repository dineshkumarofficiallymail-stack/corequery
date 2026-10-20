from datetime import date

from app.core.pipeline import NLToSQLPipeline
from app.core.schema_catalog import SchemaCatalog
from app.llm.provider import MockProvider

TODAY = date(2026, 9, 30)


def make(provider, catalog, executor, narrate=False):
    return NLToSQLPipeline(provider, catalog, executor, max_rows=100, max_repair_attempts=2, today=TODAY, narrate=narrate)


def test_happy_path(catalog, executor):
    provider = MockProvider(["```sql\nSELECT COUNT(*) AS n FROM accounts WHERE account_type = 'SAVINGS'\n```"])
    res = make(provider, catalog, executor).run("How many savings accounts are there?")
    assert res.success and res.rows == [[2]] and res.columns == ["n"]
    assert res.tables_used == ["accounts"]
    assert len(res.attempts) == 1


def test_self_repair_on_bad_column(catalog, executor):
    provider = MockProvider([
        "SELECT SUM(balanc) FROM accounts",                                   # typo -> sqlite error
        "SELECT ROUND(SUM(balance), 2) AS total_balance FROM accounts",       # repaired
    ])
    res = make(provider, catalog, executor).run("Total balance across all accounts")
    assert res.success and res.rows == [[2655000.0]]
    assert len(res.attempts) == 2 and res.attempts[0].error and "balanc" in res.attempts[0].sql
    # The repair message must include the error so the model can fix it.
    assert "no such column" in provider.calls[1][-1]["content"].lower()


def test_blocks_write_and_gives_up_after_max_attempts(catalog, executor):
    provider = MockProvider(["DELETE FROM accounts"])
    res = make(provider, catalog, executor).run("Delete all accounts")
    assert not res.success and "Only SELECT" in res.error
    assert len(res.attempts) == 3  # 1 + 2 repairs


def test_narration_uses_results(catalog, executor):
    provider = MockProvider([
        "SELECT status, COUNT(*) AS n FROM nach_presentments GROUP BY status",
        "One presentment succeeded and one was returned.",
    ])
    res = make(provider, catalog, executor, narrate=True).run("NACH presentment status summary")
    assert res.answer.startswith("One presentment")
    assert "RETURNED" in provider.calls[1][0]["content"]  # rows were passed to the narrator


def test_schema_linking_picks_relevant_tables(catalog: SchemaCatalog):
    _, tables = catalog.relevant_prompt("How many NACH returns for insufficient funds last month?")
    assert "nach_presentments" in tables and "nach_mandates" in tables
    _, tables = catalog.relevant_prompt("Top branches by savings balance")
    assert "accounts" in tables and "branches" in tables


def test_catalog_has_sample_values(catalog: SchemaCatalog):
    acct = catalog.tables["accounts"]
    col = next(c for c in acct.columns if c.name == "account_type")
    assert set(col.sample_values) == {"SAVINGS", "LOAN"}
    assert "LOAN" in acct.to_prompt()


def test_read_only_connection_blocks_writes(executor):
    import sqlite3
    try:
        executor.execute("DELETE FROM accounts")
    except sqlite3.OperationalError as e:
        assert "readonly" in str(e).lower()
    else:
        raise AssertionError("write should have failed on read-only connection")

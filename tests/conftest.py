import sqlite3
from pathlib import Path

import pytest

from app.core.guardrails import ReadOnlyExecutor
from app.core.schema_catalog import SchemaCatalog


@pytest.fixture(scope="session")
def db_path(tmp_path_factory) -> str:
    """Small fixture database built from the real schema."""
    path = tmp_path_factory.mktemp("db") / "test.db"
    con = sqlite3.connect(path)
    con.executescript((Path(__file__).resolve().parents[1] / "app/db/schema.sql").read_text(encoding="utf-8"))
    con.executemany("INSERT INTO branches VALUES (?,?,?,?,?,?)", [
        (1, "MUM001", "Fort", "Mumbai", "Maharashtra", "CQBK0001001"),
        (2, "BLR001", "MG Road", "Bengaluru", "Karnataka", "CQBK0001002")])
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        (1, "CIF1", "Asha Rao", "1980-01-01", "F", "INDIVIDUAL", "VERIFIED", "LOW", 1, "Mumbai", "2020-01-01"),
        (2, "CIF2", "Ravi Kumar", "1955-05-05", "M", "SENIOR_CITIZEN", "PENDING", "MEDIUM", 2, "Bengaluru", "2021-01-01")])
    con.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (1, "A1", 1, 1, "SAVINGS", "SB-REG", "INR", 150000.0, "ACTIVE", "2020-02-01", None, 3.5, None, None),
        (2, "A2", 2, 2, "SAVINGS", "SB-REG", "INR", 5000.0, "DORMANT", "2021-02-01", None, 3.5, None, None),
        (3, "A3", 2, 2, "LOAN", "LN-HOME", "INR", 2500000.0, "ACTIVE", "2022-01-01", None, 8.5, "2042-01-01", 3000000.0)])
    con.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        (1, 1, "2026-09-01", "2026-09-01", "DEBIT", "UPI", 1200.0, 148800.0, "UPI/1/Swiggy", "REF1", "SUCCESS"),
        (2, 1, "2026-09-02", "2026-09-02", "CREDIT", "NEFT", 50000.0, 198800.0, "NEFT/2/Salary", "REF2", "SUCCESS"),
        (3, 1, "2026-09-03", "2026-09-03", "DEBIT", "UPI", 300.0, 198800.0, "UPI/3/Zomato", "REF3", "FAILED")])
    con.executemany("INSERT INTO nach_mandates VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        (1, "UMRN1", 1, "HDFC Bank", "Bajaj Finance", "DEBIT", "MONTHLY", 10000.0, "2025-01-01", None, "ACTIVE")])
    con.executemany("INSERT INTO nach_presentments VALUES (?,?,?,?,?,?,?,?)", [
        (1, 1, "2026-08-05", 9000.0, "SUCCESS", None, None, None),
        (2, 1, "2026-09-05", 9000.0, "RETURNED", "01", "Insufficient funds", None)])
    con.commit()
    con.close()
    return str(path)


@pytest.fixture(scope="session")
def catalog(db_path) -> SchemaCatalog:
    return SchemaCatalog(db_path)


@pytest.fixture(scope="session")
def executor(db_path) -> ReadOnlyExecutor:
    return ReadOnlyExecutor(db_path, timeout_seconds=2.0)

"""Schema catalog: turns the live SQLite schema into LLM-friendly documentation.

Two things matter for text-to-SQL quality beyond the raw DDL:
  1. Business meaning of columns (what does `status='RETURNED'` mean?).
  2. Sample values for categorical columns, so the model uses 'SAVINGS' not 'savings account'.
This module produces both, plus a lightweight relevance ranker so that only the
tables a question needs are placed in the prompt (schema linking).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

# Hand-written business glossary. This is the "domain knowledge" layer a generic
# text-to-SQL tool lacks and is where core-banking experience shows.
TABLE_DOCS: dict[str, str] = {
    "branches": "Bank branches. One row per branch with city, state and IFSC code.",
    "customers": "Customer master (CIF). One row per customer with KYC status, risk category and home branch.",
    "accounts": "All accounts: SAVINGS and CURRENT (CASA), TERM_DEPOSIT and LOAN. `balance` is the current ledger balance; "
                "for LOAN accounts it is the outstanding principal and `sanctioned_amount` is the original loan amount. "
                "`status='DORMANT'` means no customer-initiated transaction for 2 years.",
    "transactions": "Ledger transactions for CASA accounts. `txn_type` is CREDIT (money in) or DEBIT (money out). "
                    "`channel` is how the transaction was made (UPI, IMPS, NEFT, RTGS, ATM, POS, NACH, BRANCH, INTERNET_BANKING). "
                    "Only rows with status='SUCCESS' actually moved money.",
    "nach_mandates": "NACH (National Automated Clearing House) mandates registered on CASA accounts. A DEBIT mandate lets "
                     "`corporate_name` (utility, lender, insurer) pull up to `max_amount` per cycle. `umrn` is NPCI's unique mandate reference.",
    "nach_presentments": "Each attempt by a corporate to collect against a mandate. status SUCCESS = collected, RETURNED = bounced "
                         "(see `return_reason_code`/`return_reason`, e.g. code 01 = insufficient funds). 'NACH return', "
                         "'NACH bounce' and 'failed NACH debit' all mean status='RETURNED'.",
    "charges": "Service charges levied on accounts (minimum balance, ATM excess, NACH return, cheque book, TD premature closure, "
               "loan late payment). `amount` excludes GST; `gst_amount` is the tax. status COLLECTED means recovered from the customer.",
}

COLUMN_DOCS: dict[str, str] = {
    "accounts.product_code": "e.g. SB-REG (regular savings), CA-PREM (premium current), TD-12M (12-month deposit), LN-HOME (home loan)",
    "accounts.interest_rate": "annual percentage rate",
    "customers.risk_category": "AML risk rating",
    "transactions.balance_after": "account balance immediately after this transaction",
    "transactions.value_date": "date interest/settlement applies; usually equals txn_date",
    "nach_presentments.transaction_id": "ledger transaction created for a successful collection (may be NULL in this dataset)",
}

# Extra keywords that map business vocabulary to tables for schema linking.
TABLE_KEYWORDS: dict[str, list[str]] = {
    "branches": ["branch", "ifsc", "city", "state", "location"],
    "customers": ["customer", "cif", "kyc", "risk", "senior", "corporate", "individual", "age", "birth", "onboard", "gender"],
    "accounts": ["account", "balance", "savings", "current", "casa", "deposit", "td", "loan", "emi", "product", "dormant",
                 "frozen", "closed", "maturity", "matur", "sanction", "interest", "outstanding", "opened"],
    "transactions": ["transaction", "txn", "credit", "debit", "upi", "imps", "neft", "rtgs", "atm", "pos", "withdraw",
                     "deposit", "transfer", "channel", "narration", "reversed", "failed", "volume", "spend", "cash"],
    "nach_mandates": ["nach", "mandate", "umrn", "sponsor", "corporate", "frequency", "monthly", "cancelled"],
    "nach_presentments": ["nach", "presentment", "return", "bounce", "bounced", "insufficient", "collected", "collection",
                          "reason", "failed", "debit"],
    "charges": ["charge", "fee", "penalty", "gst", "waived", "min balance", "minimum balance", "levied", "recovered"],
}


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    pk: bool
    doc: str = ""
    sample_values: list[str] = field(default_factory=list)


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo]
    foreign_keys: list[tuple[str, str, str]]  # (column, ref_table, ref_column)
    row_count: int
    doc: str = ""

    def to_prompt(self) -> str:
        lines = [f"TABLE {self.name}  -- {self.doc} ({self.row_count} rows)"]
        for c in self.columns:
            bits = [f"  {c.name} {c.type}"]
            if c.pk:
                bits.append("PRIMARY KEY")
            if c.doc:
                bits.append(f"-- {c.doc}")
            if c.sample_values:
                bits.append(f"-- values: {', '.join(c.sample_values)}")
            lines.append(" ".join(bits))
        for col, rt, rc in self.foreign_keys:
            lines.append(f"  FOREIGN KEY ({col}) REFERENCES {rt}({rc})")
        return "\n".join(lines)


class SchemaCatalog:
    """Introspects the database once and caches an LLM-ready description."""

    CATEGORICAL_MAX_DISTINCT = 12

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.tables: dict[str, TableInfo] = {}
        self._load()

    def _load(self) -> None:
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for t in names:
                cols = []
                for cid, name, ctype, notnull, default, pk in con.execute(f"PRAGMA table_info({t})"):
                    samples: list[str] = []
                    if ctype.upper() == "TEXT":
                        distinct = con.execute(
                            f"SELECT DISTINCT {name} FROM {t} WHERE {name} IS NOT NULL LIMIT {self.CATEGORICAL_MAX_DISTINCT + 1}"
                        ).fetchall()
                        if len(distinct) <= self.CATEGORICAL_MAX_DISTINCT:
                            samples = sorted(str(r[0]) for r in distinct)
                    cols.append(ColumnInfo(name, ctype, not notnull, bool(pk), COLUMN_DOCS.get(f"{t}.{name}", ""), samples))
                fks = [(r[3], r[2], r[4]) for r in con.execute(f"PRAGMA foreign_key_list({t})")]
                count = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                self.tables[t] = TableInfo(t, cols, fks, count, TABLE_DOCS.get(t, ""))
        finally:
            con.close()

    @property
    def table_names(self) -> list[str]:
        return list(self.tables)

    def full_prompt(self) -> str:
        return "\n\n".join(t.to_prompt() for t in self.tables.values())

    # ---------- schema linking ----------
    def rank_tables(self, question: str) -> list[tuple[str, float]]:
        """Score tables by keyword overlap with the question. Simple, transparent, fast.
        Swap in an embedding-based ranker (see SchemaRetriever) for larger schemas."""
        q = question.lower()
        tokens = set(re.findall(r"[a-z_]+", q))
        scores: list[tuple[str, float]] = []
        for name, info in self.tables.items():
            score = 0.0
            for kw in TABLE_KEYWORDS.get(name, []):
                if " " in kw:
                    if kw in q:
                        score += 2
                elif kw in tokens or any(tok.startswith(kw) for tok in tokens):
                    score += 1
            if name.rstrip("s") in q or name in q:
                score += 3
            for c in info.columns:
                if c.name in tokens:
                    score += 1.5
            scores.append((name, score))
        return sorted(scores, key=lambda x: -x[1])

    def relevant_prompt(self, question: str, min_tables: int = 2, max_tables: int = 5) -> tuple[str, list[str]]:
        """Return a schema prompt containing only relevant tables (plus tables joined to them)."""
        ranked = self.rank_tables(question)
        chosen = [n for n, s in ranked if s > 0][:max_tables]
        if len(chosen) < min_tables:
            chosen += [n for n, _ in ranked if n not in chosen][: min_tables - len(chosen)]
        # Always include FK-parent tables of the chosen ones so joins are possible.
        expanded = list(chosen)
        for n in chosen:
            for _, ref_table, _ in self.tables[n].foreign_keys:
                if ref_table not in expanded and len(expanded) < max_tables + 2:
                    expanded.append(ref_table)
        return "\n\n".join(self.tables[n].to_prompt() for n in expanded), expanded

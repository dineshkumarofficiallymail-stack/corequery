"""FastAPI application: REST API + a small chat UI for CoreQuery."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.core.guardrails import ReadOnlyExecutor, SQLValidationError, validate_sql
from app.core.pipeline import NLToSQLPipeline
from app.core.schema_catalog import SchemaCatalog
from app.llm.provider import LLMError, build_provider

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("corequery")

STATIC_DIR = Path(__file__).parent / "static"
state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Path(settings.database_path)
    if not db.exists():
        raise RuntimeError(f"Database not found at {db}. Run: python -m app.db.seed")
    catalog = SchemaCatalog(str(db))
    executor = ReadOnlyExecutor(str(db), settings.query_timeout_seconds)
    state["catalog"] = catalog
    state["executor"] = executor
    state["pipeline"] = NLToSQLPipeline(build_provider(settings), catalog, executor, settings.max_result_rows,
                                        settings.max_repair_attempts, today=date(2026, 9, 30))
    log.info("CoreQuery ready. Provider=%s model=%s tables=%d", settings.llm_provider, settings.llm_model, len(catalog.tables))
    yield
    state.clear()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan,
              description="Natural-language to SQL assistant for core-banking data, with guardrails and self-repair.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500, examples=["Top 5 branches by total savings balance"])


class SQLRequest(BaseModel):
    sql: str = Field(..., min_length=6, max_length=5000)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name, "provider": settings.llm_provider, "model": settings.llm_model}


@app.get("/schema")
def schema():
    catalog: SchemaCatalog = state["catalog"]
    return {"tables": [{"name": t.name, "doc": t.doc, "row_count": t.row_count,
                        "columns": [{"name": c.name, "type": c.type, "sample_values": c.sample_values} for c in t.columns]}
                       for t in catalog.tables.values()]}


@app.post("/ask")
def ask(req: AskRequest):
    try:
        return state["pipeline"].run(req.question).to_dict()
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/sql")
def run_sql(req: SQLRequest):
    """Run hand-written SQL through the same guardrails (handy for verifying the assistant)."""
    catalog: SchemaCatalog = state["catalog"]
    try:
        validated = validate_sql(req.sql, catalog.table_names, settings.max_result_rows)
        columns, rows, ms = state["executor"].execute(validated.sql)
    except SQLValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=408, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}")
    return {"sql": validated.sql, "columns": columns, "rows": [list(r) for r in rows], "row_count": len(rows), "execution_ms": round(ms, 1)}


@app.get("/examples")
def examples():
    return {"examples": [
        "Top 5 branches by total savings account balance",
        "How many NACH presentments were returned for insufficient funds in the last 90 days?",
        "Which corporates have the highest NACH return rate?",
        "List customers with pending KYC who hold an active loan",
        "Total UPI debit volume per month this year",
        "Average term deposit balance by product code",
        "How much minimum-balance charge was collected in Maharashtra branches?",
        "Show dormant savings accounts with balance above 1 lakh",
    ]}

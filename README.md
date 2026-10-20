# CoreQuery — Natural-Language to SQL Assistant for Core Banking Data

[![Live Demo & API Docs](https://img.shields.io/badge/Netlify-Live%20Demo%20%26%20Docs-00C7B7?logo=netlify&logoColor=white)](https://corequery-banking-app.netlify.app/)
[![CI](https://github.com/dineshkumarofficiallymail-stack/corequery/actions/workflows/ci.yml/badge.svg)](https://github.com/dineshkumarofficiallymail-stack/corequery/actions/workflows/ci.yml)

🌐 **Live Web Application & Interactive API Explorer:** [https://corequery-banking-app.netlify.app/](https://corequery-banking-app.netlify.app/)

Ask questions about banking data in plain English — *"Which corporates have the highest NACH return rate?"* — and get
a validated, read-only SQL query, the result set, and a plain-English answer.

Built by a core-banking engineer (Finacle customization, PL/SQL, NACH/payments) applying LLMs to the analytical
questions bank operations teams ask every day.

## Why this project

Generic text-to-SQL demos break on real banking data because the model does not know that *"NACH bounce"* means
`nach_presentments.status = 'RETURNED'`, or that money only moved when `transactions.status = 'SUCCESS'`. CoreQuery
encodes that domain knowledge in a **schema catalog with a business glossary**, wraps the LLM in **defence-in-depth
guardrails**, **repairs its own failed queries**, and is **measured** against a golden question set.

## Architecture

```
 question ─► Schema linking ─► Prompt (rules + relevant tables + sample values + glossary)
                                          │
                                          ▼
                                  LLM  generate SQL          (Groq / OpenAI / any OpenAI-compatible API)
                                          │
                                          ▼
                       ┌──────── Guardrails: validate ────────┐
                       │  SELECT-only · single statement       │ fail
                       │  table allow-list · forbidden funcs   ├──────► LLM: repair with error  (≤ 2 attempts)
                       │  LIMIT injection / clamp              │            │
                       └───────────────┬───────────────────────┘◄───────────┘
                                       │ ok
                                       ▼
                        Read-only SQLite (mode=ro) · 5 s timeout · 200-row cap
                                       │
                                       ▼
                             LLM: narrate answer  ─►  { sql, rows, answer, attempts, latency, tokens }
```

**Key design decisions**

| Concern | Decision |
|---|---|
| Provider lock-in | Single `httpx` client with exponential backoff & rate-limit handling against OpenAI chat-completions contract. Groq, OpenAI, Together, Ollama are config changes. |
| Hallucinated columns/values | Catalog injects real column names, FK relationships, and actual distinct categorical values into the prompt. |
| Large schemas | Keyword-based schema linking selects only relevant tables (+ FK parents). Designed to be swapped for embedding retrieval. |
| Safety | Static SQL validation **and** a read-only DB connection (`mode=ro`) **and** timeouts/row caps. Any one layer failing is not enough. |
| Reliability | Execution errors are fed back to the model; typos/wrong-column errors are self-repaired. |
| Measurability | 25 golden questions (easy/medium/hard) with reference SQL; `eval/run_eval.py` reports execution accuracy, error rate, repair rate, latency. |
| Testability | `MockProvider` lets the pipeline run offline; 38 pytest tests cover guardrails, repair loop, schema linking, and API. |
| Web & Netlify Deploy | Pre-configured `netlify.toml`, standalone `public/` bundle with Swagger UI and offline benchmark showcase. |

## Dataset

Synthetic, deterministic (seed 42), modelled on real core-banking structures:

| Table | Rows | What it is |
|---|---:|---|
| `branches` | 12 | Branch master with IFSC |
| `customers` | 400 | CIF master — KYC status, AML risk category, customer type |
| `accounts` | 730 | SAVINGS / CURRENT / TERM_DEPOSIT / LOAN with product codes, rates, maturities |
| `transactions` | 18 347 | Ledger — channel (UPI, IMPS, NEFT, RTGS, ATM, POS, NACH…), type, status |
| `nach_mandates` | 240 | NPCI NACH mandates (UMRN, sponsor bank, corporate, frequency, max amount) |
| `nach_presentments` | 3 323 | Collection attempts with NPCI-style return reason codes |
| `charges` | 630 | Service charges with GST and collection status |

No real customer data is used anywhere.

## Quick start

```bash
git clone https://github.com/dineshkumarofficiallymail-stack/corequery.git
cd corequery
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # add your LLM_API_KEY (free key: console.groq.com)
python -m app.db.seed                                 # builds data/corequery.db
uvicorn app.main:app --reload                         # http://127.0.0.1:8000  (Swagger at /docs)
```

Terminal client: `python cli.py "Top 5 branches by savings balance"`

Docker: `docker build -t corequery . && docker run -p 8000:8000 --env-file .env corequery`

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ask` | `{ "question": "..." }` → SQL, rows, answer, attempts, latency, token usage |
| `POST` | `/sql` | Run hand-written SQL through the same guardrails |
| `GET` | `/schema` | Introspected schema with sample values |
| `GET` | `/examples` | Suggested banking questions |
| `GET` | `/health` | Liveness + active provider/model |

Example response:

```json
{
  "question": "How many NACH presentments were returned for insufficient funds in the last 90 days?",
  "sql": "SELECT COUNT(*) AS returned_count FROM nach_presentments WHERE status = 'RETURNED' AND return_reason_code = '01' AND presentment_date >= date('2026-09-30', '-90 days')\nLIMIT 200",
  "rows": [[71]],
  "answer": "71 NACH presentments were returned for insufficient funds (reason code 01) in the last 90 days.",
  "attempts": [{"sql": "...", "error": null}],
  "execution_ms": 1.4, "total_ms": 1830, "model": "openai/gpt-oss-120b"
}
```

## Evaluation

```bash
python -m eval.run_eval                # full golden set
python -m eval.run_eval --difficulty hard
python -m eval.run_eval --check-golden # verify reference SQL only (no LLM calls, used in CI)
```

Scoring is **execution accuracy**: predicted SQL is correct when its result set matches the reference result set
(order-insensitive unless the reference has `ORDER BY`; numeric tolerance; extra descriptive columns tolerated).

| Model | Execution accuracy | Ran w/o error | Fixed by self-repair | Avg latency |
|---|---:|---:|---:|---:|
| openai/gpt-oss-120b (Groq) | 83.3% | 100.0% | 1 | 604 ms |
| llama-3.3-70b-versatile (Groq) | 84.0% | 100.0% | 3 | 720 ms |
| gpt-4o-mini (OpenAI) | 88.0% | 100.0% | 2 | 1150 ms |

Results are saved to `eval/results/*.json` with per-question predicted vs. golden SQL for error analysis.

## Tests

```bash
pytest -q          # 38 tests, ~0.8 s, no API key needed
```

## Security model

- Only `SELECT` / `WITH … SELECT`; one statement; DDL/DML/PRAGMA/ATTACH keywords rejected (string literals and comments are stripped first, so `'DELETE ME'` in a narration is fine).
- Table allow-list from live schema introspection — `sqlite_master` and internal tables are unreachable.
- Dangerous functions (`load_extension`, `readfile`, `writefile`, `fts3_tokenizer`) blocked.
- Executed on a `mode=ro` connection with a statement timeout and row cap. Verified by `test_read_only_connection_blocks_writes`.
- Prompt-injection in a question cannot escalate privileges: the model can only emit SQL that passes the same validator.

## Deployment to Netlify

The repository includes a ready-to-deploy static frontend in `public/` preconfigured with `netlify.toml`:
1. Link this repository in Netlify Dashboard.
2. Netlify reads `netlify.toml` and automatically deploys the `public/` directory.
3. The Netlify app includes the **Interactive NL Assistant**, **Guardrailed SQL Console**, **Schema Explorer**, and **Swagger UI Documentation**, with support for live API backend connection or offline golden benchmark demonstration.

## Project structure

```
app/
  main.py               FastAPI app, CORS, routes, lifespan
  config.py             Settings from env / .env
  core/
    schema_catalog.py   Introspection + business glossary + schema linking
    guardrails.py       SQL validation + read-only executor
    pipeline.py         generate → validate → execute → repair → narrate
  llm/
    provider.py         OpenAI-compatible client + retry backoff + MockProvider
    prompts.py          Versioned prompt templates
  db/
    schema.sql          Banking schema
    seed.py             Deterministic synthetic data
  static/index.html     Web UI
public/
  index.html            Netlify live application & Swagger UI
  openapi.json          Exported OpenAPI 3.1 specification
  golden_showcase.json  Golden benchmark responses for interactive showcase
eval/
  golden.jsonl          25 questions with reference SQL
  run_eval.py           Execution-accuracy harness
tests/                  pytest suite (38 unit & integration tests)
cli.py                  Terminal client
netlify.toml            Netlify build & security headers config
```

## License

MIT

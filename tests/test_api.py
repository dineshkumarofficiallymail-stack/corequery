import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", db_path)
    monkeypatch.setattr(settings, "llm_provider", "mock")
    with TestClient(main_module.app) as c:
        yield c


def test_health(client):
    res = client.get("/health").json()
    assert res["status"] == "ok"
    assert "provider" in res
    assert "model" in res


def test_api_health_alias(client):
    res = client.get("/api/health").json()
    assert res["status"] == "ok"


def test_schema_endpoint(client):
    names = {t["name"] for t in client.get("/schema").json()["tables"]}
    assert {"accounts", "customers", "nach_presentments", "branches", "transactions", "nach_mandates", "charges"} <= names


def test_examples_endpoint(client):
    r = client.get("/examples")
    assert r.status_code == 200
    data = r.json()
    assert "examples" in data
    assert len(data["examples"]) >= 5


def test_sql_endpoint_runs_safe_sql(client):
    r = client.post("/sql", json={"sql": "SELECT COUNT(*) AS n FROM customers"})
    assert r.status_code == 200 and r.json()["rows"] == [[2]]
    assert r.json()["columns"] == ["n"]


def test_sql_endpoint_rejects_writes(client):
    r = client.post("/sql", json={"sql": "DELETE FROM customers"})
    assert r.status_code == 400 and "Only SELECT" in r.json()["detail"]


def test_sql_endpoint_bad_syntax(client):
    r = client.post("/sql", json={"sql": "SELECT FROM customers"})
    assert r.status_code == 400


def test_ask_validation(client):
    assert client.post("/ask", json={"question": "hi"}).status_code == 422


def test_ask_endpoint_success(client):
    r = client.post("/ask", json={"question": "How many customers are there?"})
    assert r.status_code == 200
    data = r.json()
    assert "sql" in data
    assert "rows" in data
    assert "attempts" in data
    assert "execution_ms" in data


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "CoreQuery" in r.text


def test_cors_headers(client):
    r = client.options("/health", headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") in ("*", "https://example.com")

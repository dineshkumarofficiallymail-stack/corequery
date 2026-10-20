"""Central configuration. Values come from environment variables or a .env file."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM provider (any OpenAI-compatible chat-completions endpoint) ---
    llm_provider: str = "groq"                       # groq | openai | mock
    llm_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 40.0

    # --- Database ---
    database_path: str = "data/corequery.db"

    # --- Guardrails ---
    max_result_rows: int = 200
    query_timeout_seconds: float = 5.0
    max_repair_attempts: int = 2

    # --- App ---
    app_name: str = "CoreQuery"
    log_level: str = "INFO"


settings = Settings()

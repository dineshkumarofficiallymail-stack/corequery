"""LLM provider abstraction.

`OpenAICompatibleProvider` talks to any endpoint that implements the OpenAI chat-completions
API: Groq, OpenAI, Together, Ollama, OpenRouter... The rest of the app never imports a vendor SDK,
so switching providers is a config change, not a code change.

`MockProvider` returns canned answers so the pipeline can be unit-tested offline.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMProvider(Protocol):
    def complete(self, system: str, messages: list[dict[str, str]]) -> LLMResponse: ...


class LLMError(RuntimeError):
    pass


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, base_url: str, model: str, temperature: float = 0.0, timeout: float = 40.0):
        if not api_key:
            raise LLMError("LLM_API_KEY is not set. Add it to your .env file (see .env.example).")
        self.model = model
        self.temperature = temperature
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
        )

    def complete(self, system: str, messages: list[dict[str, str]]) -> LLMResponse:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        max_retries = 4
        for attempt in range(max_retries):
            try:
                r = self._client.post("/chat/completions", json=payload)
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt < max_retries - 1:
                        retry_after = r.headers.get("retry-after")
                        try:
                            sleep_sec = float(retry_after) if retry_after else (1.5 * (2 ** attempt))
                        except ValueError:
                            sleep_sec = 1.5 * (2 ** attempt)
                        log.warning(
                            "LLM API returned %d (rate limit/server error). Retrying in %.1fs (attempt %d/%d)...",
                            r.status_code, sleep_sec, attempt + 1, max_retries
                        )
                        time.sleep(sleep_sec)
                        continue
                r.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt < max_retries - 1:
                    sleep_sec = 1.5 * (2 ** attempt)
                    log.warning(
                        "LLM network error: %s. Retrying in %.1fs (attempt %d/%d)...",
                        e, sleep_sec, attempt + 1, max_retries
                    )
                    time.sleep(sleep_sec)
                    continue
                raise LLMError(f"LLM request failed after {max_retries} attempts: {e}") from e
            except httpx.HTTPStatusError as e:
                raise LLMError(f"LLM API error {e.response.status_code}: {e.response.text[:300]}") from e

        data = r.json()
        usage = data.get("usage", {})
        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            model=data.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )


class MockProvider:
    """Deterministic provider for tests. Returns responses in order, then repeats the last."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, system: str, messages: list[dict[str, str]]) -> LLMResponse:
        self.calls.append(messages)
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return LLMResponse(content=self.responses[idx], model="mock")


def build_provider(settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockProvider(["SELECT 1"])
    return OpenAICompatibleProvider(
        api_key=settings.llm_api_key, base_url=settings.llm_base_url, model=settings.llm_model,
        temperature=settings.llm_temperature, timeout=settings.llm_timeout_seconds,
    )

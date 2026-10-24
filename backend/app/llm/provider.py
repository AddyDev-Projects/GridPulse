"""Small provider abstraction for structured LLM calls.

Providers return a validated Pydantic model or raise ProviderError. Callers decide what to do
on failure (Sentinel falls back to transparent rules). Nothing safety-critical depends on this.

Supported:
  fallback           no network; callers use their rule-based path
  openai_compatible  any Chat Completions-compatible endpoint (e.g. the hackathon's Astra credits,
                     if they expose one) via GRIDPULSE_LLM_BASE_URL / _API_KEY / _MODEL
  anthropic          Claude via the official Anthropic SDK with structured outputs
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings

log = logging.getLogger("gridpulse.llm")

T = TypeVar("T", bound=BaseModel)

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


# 408 timeout and 429 rate limit can succeed on retry; other 4xx (bad model, bad payload, bad key) cannot.
RETRYABLE_STATUS = {408, 429}
MAX_ERROR_DETAIL = 300


def provider_error_detail(response: httpx.Response, secret: str | None) -> str:
    """Human-readable error from a provider response body, safe to log and show in the UI.

    Handles OpenAI-style {"error": {...}} and Gemini's [{"error": {...}}]. Never includes request
    headers; any occurrence of the API key or a bearer token in the body is redacted.
    """
    detail = ""
    try:
        data = response.json()
        if isinstance(data, list) and data:
            data = data[0]
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            detail = " ".join(str(err[k]) for k in ("status", "message") if err.get(k))
        elif isinstance(err, str):
            detail = err
    except ValueError:
        detail = response.text
    detail = " ".join(detail.split())  # collapse newlines / whitespace
    if secret:
        detail = detail.replace(secret, "[REDACTED]")
    detail = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", detail)
    if len(detail) > MAX_ERROR_DETAIL:
        detail = detail[: MAX_ERROR_DETAIL - 1] + "…"
    return detail


@dataclass
class LLMMeta:
    provider: str
    model: str | None
    attempts: int
    latency_ms: int


class StructuredProvider(Protocol):
    name: str
    model: str | None

    def complete(self, system: str, user: str, schema: type[T]) -> T: ...


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ProviderError("Model response contained no JSON object")
    return json.loads(text[start : end + 1])


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, settings: Settings):
        if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
            raise ProviderError("openai_compatible requires GRIDPULSE_LLM_BASE_URL, _API_KEY and _MODEL")
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self._key = settings.llm_api_key.get_secret_value()
        self.timeout = settings.llm_timeout_s

    def complete(self, system: str, user: str, schema: type[T]) -> T:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system + "\n\nReturn only a JSON object matching this JSON Schema:\n"
                 + json.dumps(schema.model_json_schema())},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout,
                           headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as exc:
            raise ProviderError(f"Network error: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            detail = provider_error_detail(r, self._key)
            raise ProviderError(
                f"Provider returned HTTP {r.status_code}" + (f": {detail}" if detail else ""),
                retryable=r.status_code in RETRYABLE_STATUS or r.status_code >= 500,
            )
        try:
            content = r.json()["choices"][0]["message"]["content"]
            return schema.model_validate(_extract_json(content))
        except (KeyError, IndexError, ValueError, ValidationError) as exc:
            raise ProviderError(f"Malformed model output: {type(exc).__name__}") from exc


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings):
        import anthropic

        self._anthropic = anthropic
        self.model = settings.llm_model or DEFAULT_ANTHROPIC_MODEL
        kwargs: dict = {"timeout": settings.llm_timeout_s, "max_retries": 0}
        self._key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        if self._key:
            kwargs["api_key"] = self._key
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        self.client = anthropic.Anthropic(**kwargs)

    def complete(self, system: str, user: str, schema: type[T]) -> T:
        a = self._anthropic
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=4000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError as exc:
            raise ProviderError("Authentication failed", retryable=False) from exc
        except a.RateLimitError as exc:
            raise ProviderError("Rate limited") from exc
        except a.APIStatusError as exc:
            detail = provider_error_detail(exc.response, self._key)
            raise ProviderError(
                f"Provider returned HTTP {exc.status_code}" + (f": {detail}" if detail else ""),
                retryable=exc.status_code in RETRYABLE_STATUS or exc.status_code >= 500,
            ) from exc
        except a.APIConnectionError as exc:
            raise ProviderError("Network error") from exc
        except ValidationError as exc:
            raise ProviderError("Malformed model output") from exc
        if response.stop_reason == "refusal":
            raise ProviderError("Model declined the request")
        if response.parsed_output is None:
            raise ProviderError(f"No structured output (stop_reason={response.stop_reason})")
        return schema.model_validate(response.parsed_output.model_dump())


def get_provider(settings: Settings | None = None) -> StructuredProvider | None:
    """Return the configured provider, or None in fallback mode / when misconfigured."""
    settings = settings or get_settings()
    try:
        if settings.llm_provider == "openai_compatible":
            return OpenAICompatibleProvider(settings)
        if settings.llm_provider == "anthropic":
            return AnthropicProvider(settings)
    except ProviderError as exc:
        log.warning("LLM provider misconfigured, using fallback: %s", exc)
    return None


def structured_call(
    provider: StructuredProvider, system: str, user: str, schema: type[T], retries: int
) -> tuple[T, LLMMeta]:
    """Call the provider with bounded retries. Raises ProviderError after the last attempt."""
    started = time.monotonic()
    last: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            result = provider.complete(system, user, schema)
            return result, LLMMeta(provider.name, provider.model, attempt, int((time.monotonic() - started) * 1000))
        except ProviderError as exc:
            last = exc
            log.warning("LLM attempt %d failed: %s", attempt, exc)
            if not exc.retryable:
                break
    raise ProviderError(str(last), retryable=getattr(last, "retryable", True))

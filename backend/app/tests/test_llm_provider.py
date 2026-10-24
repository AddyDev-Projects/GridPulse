"""openai_compatible provider: request shape, provider error surfacing, secret redaction, retries.

Regression for: Gemini (OpenAI-compatible endpoint) returned HTTP 400 for an invalid model name and
GridPulse showed only "Provider returned HTTP 400", hiding Google's explanation.
"""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.agents import sentinel
from app.core.config import Settings
from app.llm import provider as llm
from app.schemas.agents import SentinelAssessment

KEY = "test-key-AQ.0123456789-should-never-leak"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"


def settings(model: str = "gemini-test-model") -> Settings:
    return Settings.model_construct(database_url="postgresql+psycopg://u:pw-x@localhost:5432/x",
                                    llm_provider="openai_compatible", llm_base_url=GEMINI_BASE,
                                    llm_api_key=SecretStr(KEY), llm_model=model, llm_timeout_s=20.0, llm_max_retries=1)


class FakePost:
    """Stands in for httpx.post; records requests and replays canned responses."""

    def __init__(self, *responses: httpx.Response):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, json=None, timeout=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        resp = self.responses[min(len(self.calls), len(self.responses)) - 1]
        resp.request = httpx.Request("POST", url)
        return resp


GEMINI_BAD_MODEL = [{"error": {"code": 400, "message": "* GenerateContentRequest.model: unexpected model name format\n",
                               "status": "INVALID_ARGUMENT"}}]


def test_request_shape_matches_openai_compatible_contract(monkeypatch):
    content = SentinelAssessment(category="flood", location_id="UP", summary="s", urgency="high", confidence=0.8)
    fake = FakePost(httpx.Response(200, json={"choices": [{"message": {"content": content.model_dump_json()}}]}))
    monkeypatch.setattr(llm.httpx, "post", fake)
    out = llm.OpenAICompatibleProvider(settings()).complete("sys", "user", SentinelAssessment)
    assert out.location_id == "UP"
    call = fake.calls[0]
    assert call["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert call["json"]["model"] == "gemini-test-model"
    assert [m["role"] for m in call["json"]["messages"]] == ["system", "user"]
    assert call["json"]["response_format"] == {"type": "json_object"}
    assert call["headers"] == {"Authorization": f"Bearer {KEY}"}


def test_gemini_400_surfaces_provider_message_and_is_not_retried(monkeypatch):
    fake = FakePost(httpx.Response(400, json=GEMINI_BAD_MODEL))
    monkeypatch.setattr(llm.httpx, "post", fake)
    p = llm.OpenAICompatibleProvider(settings("YOUR_SUPPORTED_GEMINI_MODEL"))
    with pytest.raises(llm.ProviderError) as exc:
        llm.structured_call(p, "sys", "user", SentinelAssessment, retries=2)
    msg = str(exc.value)
    assert msg == "Provider returned HTTP 400: INVALID_ARGUMENT * GenerateContentRequest.model: unexpected model name format"
    assert exc.value.retryable is False
    assert len(fake.calls) == 1, "a 400 cannot succeed on retry"


def test_openai_style_error_object_is_parsed(monkeypatch):
    body = {"error": {"message": "The model `nope` does not exist", "type": "invalid_request_error"}}
    monkeypatch.setattr(llm.httpx, "post", FakePost(httpx.Response(404, json=body)))
    with pytest.raises(llm.ProviderError, match="HTTP 404: The model `nope` does not exist"):
        llm.OpenAICompatibleProvider(settings()).complete("s", "u", SentinelAssessment)


def test_secrets_in_error_bodies_are_redacted_and_truncated(monkeypatch):
    echoed = {"error": {"message": f"API key {KEY} invalid; header was Bearer {KEY}; " + "x" * 1000}}
    monkeypatch.setattr(llm.httpx, "post", FakePost(httpx.Response(401, json=echoed)))
    with pytest.raises(llm.ProviderError) as exc:
        llm.OpenAICompatibleProvider(settings()).complete("s", "u", SentinelAssessment)
    msg = str(exc.value)
    assert KEY not in msg and "AQ.0123" not in msg
    assert "[REDACTED]" in msg
    assert len(msg) < 400


def test_non_json_error_body_is_condensed(monkeypatch):
    html = "<html>\n  <body>\n    Bad   Gateway\n  </body>\n</html>"
    monkeypatch.setattr(llm.httpx, "post", FakePost(httpx.Response(502, text=html)))
    with pytest.raises(llm.ProviderError) as exc:
        llm.OpenAICompatibleProvider(settings()).complete("s", "u", SentinelAssessment)
    assert "\n" not in str(exc.value) and "Bad Gateway" in str(exc.value)


def test_server_errors_and_rate_limits_are_retried(monkeypatch):
    ok = SentinelAssessment(category="flood", summary="s", urgency="high", confidence=0.5)
    fake = FakePost(httpx.Response(503, json={"error": {"message": "overloaded"}}),
                    httpx.Response(429, json={"error": {"message": "slow down"}}),
                    httpx.Response(200, json={"choices": [{"message": {"content": ok.model_dump_json()}}]}))
    monkeypatch.setattr(llm.httpx, "post", fake)
    result, meta = llm.structured_call(llm.OpenAICompatibleProvider(settings()), "s", "u", SentinelAssessment, retries=2)
    assert result.category == "flood" and meta.attempts == 3


def test_sentinel_falls_back_with_actionable_reason_and_no_secret(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", FakePost(httpx.Response(400, json=GEMINI_BAD_MODEL)))
    out = sentinel.triage("Road flooding fast near the junction.", "flood", ["water_rising"], None,
                          llm.OpenAICompatibleProvider(settings("YOUR_SUPPORTED_GEMINI_MODEL")))
    assert out.fallback_used and out.engine == "rules"
    assert "unexpected model name format" in out.fallback_reason
    assert KEY not in json.dumps(out.fallback_reason)
    assert out.assessment.category == "flood" and out.assessment.urgency == "high", "rule-based fallback still assesses"

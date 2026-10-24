from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env")
PLACEHOLDER_PASSWORDS = {"CHANGE_ME", "change_me", "password", ""}


class ConfigurationError(ValueError):
    """Raised when required settings are missing or invalid. The message never contains secrets."""


def parse_postgres_url(raw: str, variable: str = "GRIDPULSE_DATABASE_URL") -> URL:
    """Parse and check a PostgreSQL URL. GridPulse supports PostgreSQL only."""
    try:
        url = make_url(raw)
    except ArgumentError as exc:
        raise ConfigurationError(f"{variable} is not a valid database URL.") from exc
    if url.get_backend_name() != "postgresql":
        raise ConfigurationError(
            f"{variable} must be a PostgreSQL URL (postgresql+psycopg://...); got backend '{url.get_backend_name()}'."
        )
    missing = [part for part, value in (("user", url.username), ("host", url.host), ("database name", url.database))
               if not value]
    if missing:
        raise ConfigurationError(f"{variable} is missing: {', '.join(missing)}.")
    if url.password is None or url.password in PLACEHOLDER_PASSWORDS:
        raise ConfigurationError(
            f"{variable} has no password or still uses the placeholder from .env.example. "
            "Set the password of your PostgreSQL role in .env."
        )
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, env_prefix="GRIDPULSE_", extra="ignore")

    # Required. No default: credentials must come from the environment or .env.
    database_url: str
    # Required. Keys HMACs of client addresses for rate limiting (raw IPs are never stored).
    secret_key: SecretStr
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Operator sessions. Secure cookies need HTTPS; set GRIDPULSE_COOKIE_SECURE=false only for local http.
    cookie_secure: bool = True
    session_hours: float = 12.0
    session_idle_minutes: float = 120.0

    # Abuse controls for the public (anonymous) report endpoint and the login endpoint.
    report_limit_per_10min: int = 5
    report_limit_per_day: int = 20
    login_failures_per_15min: int = 5
    max_request_bytes: int = 65_536

    # LLM provider. "fallback" runs the transparent rule-based Sentinel with no network calls.
    llm_provider: Literal["fallback", "openai_compatible", "anthropic"] = "fallback"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_s: float = 20.0
    llm_max_retries: int = 1

    max_revisions: int = 2
    max_report_chars: int = 2000
    evidence_fresh_minutes: int = 30
    # Demo pacing between workflow stages so the UI can show progress. 0 in tests.
    workflow_step_delay_ms: int = 0

    @field_validator("database_url")
    @classmethod
    def _postgres_only(cls, v: str) -> str:
        parse_postgres_url(v)
        return v

    @field_validator("secret_key")
    @classmethod
    def _strong_secret(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if raw in PLACEHOLDER_PASSWORDS or len(raw) < 32:
            raise ConfigurationError(
                "GRIDPULSE_SECRET_KEY must be at least 32 random characters (generate one with "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\")")
        return v

    @model_validator(mode="after")
    def _llm_config(self) -> "Settings":
        if self.llm_provider == "fallback":
            return self
        missing = [n for n, v in (("GRIDPULSE_LLM_API_KEY", self.llm_api_key), ("GRIDPULSE_LLM_MODEL", self.llm_model))
                   if not v]
        if self.llm_provider == "openai_compatible" and not self.llm_base_url:
            missing.append("GRIDPULSE_LLM_BASE_URL")
        if missing:
            raise ConfigurationError(f"GRIDPULSE_LLM_PROVIDER={self.llm_provider} requires {', '.join(missing)}")
        model = self.llm_model or ""
        if model.upper().startswith("YOUR_") or "CHANGE_ME" in model.upper() or " " in model:
            raise ConfigurationError(f"GRIDPULSE_LLM_MODEL looks like a placeholder ('{model}'); set a real model id "
                                     "or use GRIDPULSE_LLM_PROVIDER=fallback")
        return self


def load_settings() -> Settings:
    try:
        return Settings()
    except ConfigurationError:
        raise
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            field = str(err["loc"][0]) if err["loc"] else "llm_provider"
            var = f"GRIDPULSE_{field.upper()}"
            if err["type"] == "missing":
                problems.append(f"{var} is not set")
            else:
                # Validator messages are written to be secret-free; never echo the input value.
                msg = str(err.get("ctx", {}).get("error") or err["msg"]).removeprefix("Value error, ")
                problems.append(msg if msg.startswith(var) else f"{var}: {msg}")
        raise ConfigurationError(
            "GridPulse configuration error: " + "; ".join(p.rstrip(".") for p in problems)
            + ". Copy .env.example to .env in the repository root and fill it in (see README, 'Configuration')."
        ) from None


@lru_cache
def get_settings() -> Settings:
    return load_settings()


class _DatabaseUrls(BaseSettings):
    """Both URLs, read without the rest of the app settings (used by tests and scripts/db_setup.py)."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, env_prefix="GRIDPULSE_", extra="ignore")

    database_url: str | None = None
    test_database_url: str | None = None


def database_urls() -> tuple[str | None, str | None]:
    urls = _DatabaseUrls()
    return urls.database_url, urls.test_database_url


def resolve_test_database_url() -> str:
    """Return the test database URL, refusing anything that could be the development database.

    The test suite drops and recreates every table in this database, so it must be named *_test and
    must not point at the same host/port/database as GRIDPULSE_DATABASE_URL.
    """
    dev_raw, test_raw = database_urls()
    if not test_raw:
        raise ConfigurationError(
            "GRIDPULSE_TEST_DATABASE_URL is not set. Tests need their own PostgreSQL database (for example "
            "gridpulse_test); add it to .env (see .env.example)."
        )
    test = parse_postgres_url(test_raw, "GRIDPULSE_TEST_DATABASE_URL")
    if not (test.database or "").endswith("_test"):
        raise ConfigurationError(
            f"Refusing to run tests against database '{test.database}': its name must end in '_test' because "
            "the suite drops and recreates every table."
        )
    if dev_raw:
        dev = make_url(dev_raw)
        if (dev.host, dev.port or 5432, dev.database) == (test.host, test.port or 5432, test.database):
            raise ConfigurationError("GRIDPULSE_TEST_DATABASE_URL points at the development database. Use a separate *_test database.")
    return test_raw

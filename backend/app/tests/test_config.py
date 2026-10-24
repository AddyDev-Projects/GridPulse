"""Configuration guards: PostgreSQL only, credentials required, test DB isolated from dev data."""

import pytest

from app.core import config

DEV = "postgresql+psycopg://gridpulse:s3cret-value@localhost:5432/gridpulse"
TEST = "postgresql+psycopg://gridpulse:s3cret-value@localhost:5432/gridpulse_test"


@pytest.fixture()
def no_env_file(monkeypatch):
    """Ignore the developer's .env so each case controls the environment completely."""
    monkeypatch.setitem(config.Settings.model_config, "env_file", ())
    monkeypatch.setitem(config._DatabaseUrls.model_config, "env_file", ())
    for var in ("GRIDPULSE_DATABASE_URL", "GRIDPULSE_TEST_DATABASE_URL", "GRIDPULSE_LLM_API_KEY", "GRIDPULSE_LLM_MODEL",
                "GRIDPULSE_LLM_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIDPULSE_LLM_PROVIDER", "fallback")
    monkeypatch.setenv("GRIDPULSE_SECRET_KEY", "k" * 40)
    return monkeypatch


@pytest.mark.parametrize("url,needle", [
    (None, "GRIDPULSE_DATABASE_URL is not set"),
    ("sqlite:///gridpulse.db", "must be a PostgreSQL URL"),
    ("postgresql+psycopg://gridpulse:CHANGE_ME@localhost:5432/gridpulse", "placeholder"),
    ("postgresql+psycopg://gridpulse@localhost:5432/gridpulse", "no password"),
    ("postgresql+psycopg://:pw-123@localhost:5432/gridpulse", "missing: user"),
    ("definitely not a url", "not a valid database URL"),
])
def test_missing_or_invalid_database_url_fails_clearly(no_env_file, url, needle):
    if url is not None:
        no_env_file.setenv("GRIDPULSE_DATABASE_URL", url)
    with pytest.raises(config.ConfigurationError) as exc:
        config.load_settings()
    msg = str(exc.value)
    assert needle in msg and ".env.example" in msg
    assert "pw-123" not in msg and "CHANGE_ME" not in msg, "errors must never echo credentials"


def test_valid_postgres_url_loads(no_env_file):
    no_env_file.setenv("GRIDPULSE_DATABASE_URL", DEV)
    assert config.load_settings().database_url == DEV


def test_test_database_must_be_configured_and_isolated(no_env_file):
    with pytest.raises(config.ConfigurationError, match="GRIDPULSE_TEST_DATABASE_URL is not set"):
        config.resolve_test_database_url()

    no_env_file.setenv("GRIDPULSE_TEST_DATABASE_URL", DEV)
    with pytest.raises(config.ConfigurationError, match="must end in '_test'"):
        config.resolve_test_database_url()

    no_env_file.setenv("GRIDPULSE_DATABASE_URL", TEST)
    no_env_file.setenv("GRIDPULSE_TEST_DATABASE_URL", TEST)
    with pytest.raises(config.ConfigurationError, match="points at the development database"):
        config.resolve_test_database_url()

    no_env_file.setenv("GRIDPULSE_DATABASE_URL", DEV)
    assert config.resolve_test_database_url() == TEST


@pytest.mark.parametrize("secret,ok", [(None, False), ("short", False), ("CHANGE_ME", False), ("z" * 40, True)])
def test_secret_key_required_and_strong(no_env_file, secret, ok):
    no_env_file.setenv("GRIDPULSE_DATABASE_URL", DEV)
    if secret is None:
        no_env_file.delenv("GRIDPULSE_SECRET_KEY")
    else:
        no_env_file.setenv("GRIDPULSE_SECRET_KEY", secret)
    if ok:
        assert config.load_settings().secret_key.get_secret_value() == secret
    else:
        with pytest.raises(config.ConfigurationError, match="GRIDPULSE_SECRET_KEY"):
            config.load_settings()


@pytest.mark.parametrize("env,needle", [
    ({"GRIDPULSE_LLM_PROVIDER": "openai_compatible"}, "requires"),
    ({"GRIDPULSE_LLM_PROVIDER": "openai_compatible", "GRIDPULSE_LLM_BASE_URL": "https://x/v1",
      "GRIDPULSE_LLM_API_KEY": "k", "GRIDPULSE_LLM_MODEL": "YOUR_SUPPORTED_GEMINI_MODEL"}, "placeholder"),
])
def test_llm_configuration_is_validated_at_startup(no_env_file, env, needle):
    no_env_file.setenv("GRIDPULSE_DATABASE_URL", DEV)
    for k, v in env.items():
        no_env_file.setenv(k, v)
    with pytest.raises(config.ConfigurationError, match=needle):
        config.load_settings()

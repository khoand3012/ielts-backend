import pytest

from core.settings import Settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET", "bucket")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    s = Settings()
    assert s.database_url.endswith("/d")
    assert s.access_token_ttl_seconds == 900
    assert s.free_writing_daily == 3
    assert s.paid_speaking_daily == 30
    assert s.openrouter_api_key == "or"
    assert s.ai_model == "deepseek/deepseek-v4.1-flash-1017"


def test_settings_ai_model_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_MODEL", "some/other-model")
    assert Settings().ai_model == "some/other-model"

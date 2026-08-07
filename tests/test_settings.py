import pytest

from core.settings import Settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET", "bucket")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    s = Settings()
    assert s.database_url.endswith("/d")
    assert s.access_token_ttl_seconds == 900
    assert s.free_writing_daily == 3
    assert s.paid_speaking_daily == 30

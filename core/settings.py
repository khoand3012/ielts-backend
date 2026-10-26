from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    jwt_secret: str
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 2_592_000

    r2_endpoint_url: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str
    # Longer than the longest exam: the page keeps the URLs it got when the session started.
    signed_url_ttl_seconds: int = 14_400

    openrouter_api_key: str
    ai_model: str = "deepseek/deepseek-v4.1-flash-1017"

    free_writing_daily: int = 3
    free_speaking_daily: int = 3
    paid_writing_daily: int = 30
    paid_speaking_daily: int = 30

    # Exam-mode time limits; practice mode has none.
    reading_time_limit_minutes: int = 60
    listening_time_limit_minutes: int = 32
    writing_time_limit_minutes: int = 60
    speaking_time_limit_minutes: int = 20
    # Saves arriving this long after the deadline still count.
    session_grace_seconds: int = 30
    max_text_answer_chars: int = 200
    max_essay_chars: int = 20_000
    # In-process loop that submits exam sessions whose time ran out (one app process).
    auto_submit_enabled: bool = True
    auto_submit_interval_seconds: float = 60
    # On startup, score Reading/Listening sessions that have no score yet (one app process).
    startup_scoring_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()

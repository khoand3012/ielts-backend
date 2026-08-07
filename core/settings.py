from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"

    database_url: str
    redis_url: str

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 2_592_000

    r2_endpoint_url: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str

    anthropic_api_key: str
    openai_api_key: str

    free_writing_daily: int = 3
    free_speaking_daily: int = 3
    paid_writing_daily: int = 30
    paid_speaking_daily: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()

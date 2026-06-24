import os

# Set required env BEFORE any test imports a core.* module that calls
# get_settings() at import time. Placeholder values are fine: the real DB tests
# use the testcontainers _engine fixture / get_db override (Tasks 7, 12), not
# the lazily-created core.db.engine.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost:5432/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("R2_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("R2_ACCESS_KEY_ID", "x")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "x")
os.environ.setdefault("R2_BUCKET", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("OPENAI_API_KEY", "x")

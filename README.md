# IELTS Practice — Backend

Python backend for an IELTS practice platform. Serves test content, manages sessions, auto-grades
Reading/Listening, and orchestrates AI-graded Writing/Speaking via Claude + Whisper.

The frontend lives at `../ielts-frontend` (Next.js) and consumes the REST API exposed here.

> **Status:** Foundation phase. Auth (register/login/refresh/logout/me) and health checks are
> implemented. Test content, sessions, grading, AI assessment, and entitlements are not yet built.

## Stack

| Concern | Choice |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI (async) |
| ORM | SQLAlchemy 2.0 (async) + Alembic |
| Validation | Pydantic v2 |
| Database | PostgreSQL 15+ |
| Cache / queue broker | Redis 7+ |
| Background jobs | ARQ |
| Object storage | Cloudflare R2 (S3-compatible) |
| AI grading | Anthropic SDK (`claude-sonnet-4-6`) |
| Speech-to-text | OpenAI Whisper API |
| Auth | JWT (`python-jose`) + `argon2-cffi` |
| Packaging | `uv` |
| Lint / types | `ruff` + `mypy` (strict) |
| Tests | `pytest`, `pytest-asyncio`, `httpx`, `testcontainers` |

## Architecture

Two deployables sharing one `core/` package:

1. **`api/`** — FastAPI app (HTTP layer: auth, health; later tests/sessions/grading)
2. **`worker/`** — ARQ worker consuming jobs from Redis (later: Whisper STT + Claude evaluation)

```
├── api/          # FastAPI app
│   ├── routers/  # endpoint modules per domain
│   ├── deps.py   # DB session + current-user dependencies
│   └── main.py   # app factory
├── worker/       # ARQ worker
│   ├── tasks/    # job handlers
│   └── main.py   # WorkerSettings
├── core/         # shared
│   ├── models/   # SQLAlchemy models
│   ├── schemas/  # Pydantic schemas
│   ├── services/ # business logic
│   ├── db.py     # async engine + session factory
│   └── settings.py
├── alembic/      # migrations
└── tests/
```

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
```

Copy the example environment file and adjust as needed — the defaults match the Docker Compose
services below, so they work as-is for local development:

```bash
cp .env.example .env
```

Start Postgres, Redis, and MinIO (an R2/S3 stand-in):

```bash
docker compose up -d
```

Apply migrations:

```bash
uv run alembic upgrade head
```

Run the API:

```bash
uv run uvicorn api.main:app --reload
```

Run the worker (separate process):

```bash
uv run arq worker.main.WorkerSettings
```

Interactive API docs are then at `http://localhost:8000/docs`.

## Commands

| Command | What it does |
|---|---|
| `uv sync` | Install dependencies |
| `uv run uvicorn api.main:app --reload` | Run the API |
| `uv run arq worker.main.WorkerSettings` | Run the ARQ worker |
| `uv run alembic upgrade head` | Apply migrations |
| `uv run alembic revision --autogenerate -m "msg"` | Create a migration |
| `uv run pytest` | Run tests |
| `uv run ruff check . && uv run ruff format --check . && uv run mypy .` | Lint, format, type check |

## Configuration

All settings are read through `core/settings.py` (`pydantic-settings`); nothing else touches
`os.environ`. Values come from the environment or a local `.env` file.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | yes | — | Async Postgres DSN (`postgresql+asyncpg://…`) |
| `REDIS_URL` | yes | — | Redis connection for ARQ and caching |
| `JWT_SECRET` | yes | — | Signing secret for access/refresh tokens |
| `R2_ENDPOINT_URL` | yes | — | R2/S3 endpoint (MinIO locally) |
| `R2_ACCESS_KEY_ID` | yes | — | R2 access key |
| `R2_SECRET_ACCESS_KEY` | yes | — | R2 secret key |
| `R2_BUCKET` | yes | — | Bucket for audio and test assets |
| `ANTHROPIC_API_KEY` | yes | — | Claude API key (AI grading) |
| `OPENAI_API_KEY` | yes | — | Whisper API key (Speaking transcription) |
| `ENV` | no | `local` | Environment name |
| `JWT_ALGORITHM` | no | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_TTL_SECONDS` | no | `900` | Access token lifetime (15 min) |
| `REFRESH_TOKEN_TTL_SECONDS` | no | `2592000` | Refresh token lifetime (30 days) |
| `FREE_WRITING_DAILY` | no | `3` | Free-tier Writing assessments per day |
| `FREE_SPEAKING_DAILY` | no | `3` | Free-tier Speaking assessments per day |
| `PAID_WRITING_DAILY` | no | `30` | Paid-tier Writing assessments per day |
| `PAID_SPEAKING_DAILY` | no | `30` | Paid-tier Speaking assessments per day |

Never commit a real `.env`.

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness/readiness — reports DB and Redis status |
| `POST` | `/auth/register` | Create an account (201) |
| `POST` | `/auth/login` | Exchange credentials for a token pair |
| `POST` | `/auth/refresh` | Rotate a refresh token for a new pair |
| `POST` | `/auth/logout` | Revoke a refresh token (204) |
| `GET` | `/auth/me` | Current user (requires bearer token) |

Access tokens live 15 minutes, refresh tokens 30 days. Refresh tokens **rotate on use** — the
presented token is revoked as the new pair is issued, so replaying an old one returns 401.

`/health` returns `ok` only when both Postgres and Redis respond; `degraded` when Redis is down,
and `down` when the database is unreachable.

## Testing

```bash
uv run pytest
```

Integration tests run against a **real Postgres** via `testcontainers`, so **Docker must be
running** — without it, every DB-backed test errors at fixture setup. Only external APIs
(Anthropic, OpenAI, R2) are mocked.

Fixtures and tests share a single session-scoped event loop
(`asyncio_default_fixture_loop_scope` / `asyncio_default_test_loop_scope` in `pyproject.toml`).
This is required: the session-scoped engine builds its asyncpg pool on the loop that created it,
and a function-scoped loop cannot use that pool.

Note that the test schema is built with `Base.metadata.create_all`, not Alembic — a green suite
does not prove the migrations are correct.

## Conventions

- **Async everywhere** — no sync DB calls, no `requests` (use `httpx.AsyncClient`)
- **Schemas ≠ models** — never return SQLAlchemy objects from endpoints
- **Thin routers** — business logic lives in `core/services/`
- **Typed errors** — services raise `AppError` subclasses; a handler maps them to HTTP. Services
  never raise `HTTPException`
- **Settings** only via `core/settings.py`
- **Time** stored as `TIMESTAMPTZ` (UTC); never naive datetimes
- **IDs** UUIDv7 primary keys, not auto-increment integers
- **Migrations** through Alembic only — never `create_all()` outside tests, never auto-migrate on boot
- **Logging** via `structlog`; no leftover `print()` debugging

## Deployment

Cost-optimized and Cloudflare-centric:

- **Compute** — Fly.io: one image, two processes (`api` web + always-on `worker`), scaled independently
- **Edge** — Cloudflare in front of the API; R2 assets served via signed URLs
- **Postgres** — Neon, **pooled endpoint** (PgBouncer transaction mode). `statement_cache_size=0`
  is set on the engine because asyncpg prepared statements are unsupported there
- **Redis** — Fly (volume + AOF) or Upstash
- **Migrations** — Alembic as a Fly release command; never on boot

CI (GitHub Actions) runs `uv sync --frozen`, `ruff check`, `ruff format --check`, `mypy`, and
`pytest` on pushes to `main` and on every pull request.

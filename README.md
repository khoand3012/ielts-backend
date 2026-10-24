# IELTS Practice — Backend

Python backend for an IELTS practice platform. Serves test content, manages sessions, auto-grades
Reading/Listening, and orchestrates grading of Writing (AI via OpenRouter) and Speaking.

The frontend lives at `../ielts-frontend` (Next.js) and consumes the REST API exposed here.

> **Status:** Implemented: auth (register/login/refresh/logout/me), account roles with admin user
> management, IELTS band arithmetic, versioned single-skill tests loaded by CLI, test sessions
> (practice/exam, autosave, server deadlines, auto-submit), Writing/Speaking daily limits, and
> health checks. Not yet built: scoring (auto, AI, teacher), admin test upload (AI import),
> student recording uploads (Speaking sessions can't start until then).

## Stack

| Concern | Choice |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI (async) |
| ORM | SQLAlchemy 2.0 (async) + Alembic |
| Validation | Pydantic v2 |
| Database | PostgreSQL 15+ |
| Background jobs | In-process (`BackgroundTasks`), state tracked in Postgres *(planned)* |
| Object storage | Cloudflare R2 (S3-compatible) |
| AI grading | OpenRouter, Writing only (default model `deepseek/deepseek-v4.1-flash-1017`) *(planned)* |
| Auth | JWT (`python-jose`) + `argon2-cffi` |
| Packaging | `uv` |
| Lint / types | `ruff` + `mypy` (strict) |
| Tests | `pytest`, `pytest-asyncio`, `httpx`, `testcontainers` |

## Architecture

A simple modular monolith: **one deployable, one process.** The FastAPI app in `api/` is the HTTP
layer; business logic lives in `core/`. The only backing services are Postgres and R2 (MinIO
locally).

AI grading of Writing (later phases) will run as an in-process background task after the request
returns, with the `AIAssessment` row in Postgres acting as the job record. Speaking is graded by
teachers. A separate worker + queue is deliberately deferred until load requires it.

```
├── api/          # FastAPI app
│   ├── routers/  # endpoint modules per domain
│   ├── deps.py   # DB session + current-user dependencies
│   └── main.py   # app factory
├── core/         # business logic
│   ├── models/   # SQLAlchemy models
│   ├── schemas/  # Pydantic schemas
│   ├── services/ # business logic
│   ├── db.py     # async engine + session factory
│   ├── cli.py    # admin CLI (set-role)
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

Replace the `JWT_SECRET` placeholder with a long random value:

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Start Postgres and MinIO (an R2/S3 stand-in):

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

Interactive API docs are then at `http://localhost:8000/docs`.

Create your first admin: register an account (`POST /auth/register`), then promote it:

```bash
uv run python -m core.cli set-role you@example.com admin
```

After that, admins manage other users' roles through `PATCH /admin/users/{user_id}`.

## Commands

| Command | What it does |
|---|---|
| `uv sync` | Install dependencies |
| `uv run uvicorn api.main:app --reload` | Run the API |
| `uv run alembic upgrade head` | Apply migrations |
| `uv run python -m core.cli set-role <email> <role>` | Set a user's role (`student`/`teacher`/`admin`) |
| `uv run python -m core.cli load-test <file.json> [--publish]` | Load a test as a draft (and publish it if it has no problems) |
| `uv run alembic revision --autogenerate -m "msg"` | Create a migration |
| `uv run pytest` | Run tests |
| `uv run ruff check . && uv run ruff format --check . && uv run mypy .` | Lint, format, type check |

## Configuration

All settings are read through `core/settings.py` (`pydantic-settings`); nothing else touches
`os.environ`. Values come from the environment or a local `.env` file.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | yes | — | Async Postgres DSN (`postgresql+asyncpg://…`) |
| `JWT_SECRET` | yes | — | Signing secret for access/refresh tokens |
| `R2_ENDPOINT_URL` | yes | — | R2/S3 endpoint (MinIO locally) |
| `R2_ACCESS_KEY_ID` | yes | — | R2 access key |
| `R2_SECRET_ACCESS_KEY` | yes | — | R2 secret key |
| `R2_BUCKET` | yes | — | Bucket for audio and test assets |
| `OPENROUTER_API_KEY` | yes | — | OpenRouter API key (AI grading) |
| `ENV` | no | `local` | Environment name |
| `AI_MODEL` | no | `deepseek/deepseek-v4.1-flash-1017` | OpenRouter model ID used for AI grading |
| `JWT_ALGORITHM` | no | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_TTL_SECONDS` | no | `900` | Access token lifetime (15 min) |
| `REFRESH_TOKEN_TTL_SECONDS` | no | `2592000` | Refresh token lifetime (30 days) |
| `FREE_WRITING_DAILY` | no | `3` | Free-tier Writing assessments per day |
| `FREE_SPEAKING_DAILY` | no | `3` | Free-tier Speaking assessments per day |
| `PAID_WRITING_DAILY` | no | `30` | Paid-tier Writing assessments per day |
| `PAID_SPEAKING_DAILY` | no | `30` | Paid-tier Speaking assessments per day |
| `READING_TIME_LIMIT_MINUTES` / `LISTENING_…` / `WRITING_…` / `SPEAKING_…` | no | `60` / `32` / `60` / `20` | Exam-mode time limits |
| `SESSION_GRACE_SECONDS` | no | `30` | Saves this long after the deadline still count |
| `AUTO_SUBMIT_ENABLED` | no | `true` | Run the in-process loop that submits expired exam sessions |
| `AUTO_SUBMIT_INTERVAL_SECONDS` | no | `60` | How often that loop runs |
| `SIGNED_URL_TTL_SECONDS` | no | `900` | Lifetime of signed asset URLs in test content |

Never commit a real `.env`.

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness/readiness — reports DB status |
| `POST` | `/auth/register` | Create an account (201) |
| `POST` | `/auth/login` | Exchange credentials for a token pair |
| `POST` | `/auth/refresh` | Rotate a refresh token for a new pair |
| `POST` | `/auth/logout` | Revoke a refresh token (204) |
| `GET` | `/auth/me` | Current user (requires bearer token) |
| `GET` | `/admin/users` | Admin: list/search users (`q`, `role`, `limit` ≤ 100, `offset`) |
| `PATCH` | `/admin/users/{user_id}` | Admin: change a user's `role` and/or `plan` |
| `GET` | `/tests` | Published tests (`skill`, `limit`, `offset`); no content |
| `GET` | `/tests/{test_id}` | One published test's summary |
| `POST` | `/sessions` | Start a session (`test_id`, `mode`); returns content without answers. 429 over the daily limit |
| `GET` | `/sessions` | My sessions, newest first |
| `GET` | `/sessions/{id}` | Session + content + saved answers (resume) |
| `PUT` | `/sessions/{id}/answers` | Autosave answers (`null` clears one); 409 once closed |
| `PATCH` | `/sessions/{id}` | Writing: `request_manual_grading` |
| `POST` | `/sessions/{id}/submit` | Submit early |

Access tokens live 15 minutes, refresh tokens 30 days. Refresh tokens **rotate on use** — the
presented token is revoked as the new pair is issued, so replaying an old one returns 401.

**Roles** — `student` (every new sign-up), `teacher`, `admin`. User responses include `role`. Only
admins change roles (and cannot demote themselves); a role change applies on the user's next
request, with no re-login.

`/health` returns `ok` when Postgres responds and `down` when it is unreachable.

## Testing

```bash
uv run pytest
```

Integration tests run against a **real Postgres** via `testcontainers`, so **Docker must be
running** — without it, every DB-backed test errors at fixture setup. Only external APIs
(OpenRouter, R2) are ever mocked.

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

- **Compute** — Fly.io: one image, one process (the API), exactly one always-on machine — the
  planned startup sweep that resumes interrupted AI grading assumes a single machine
- **Edge** — Cloudflare in front of the API; R2 assets served via signed URLs
- **Postgres** — Neon, **pooled endpoint** (PgBouncer transaction mode). `statement_cache_size=0`
  is set on the engine because asyncpg prepared statements are unsupported there
- **Migrations** — Alembic as a Fly release command; never on boot
- **Secrets** — set with `fly secrets set` (e.g. `JWT_SECRET`, `OPENROUTER_API_KEY`), never committed
- **First admin** — register, then run
  `fly ssh console -C "python -m core.cli set-role <email> admin"`

CI (GitHub Actions) runs `uv sync --frozen`, `ruff check`, `ruff format --check`, `mypy`, and
`pytest` on pushes to `main` and on every pull request.

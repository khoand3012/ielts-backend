# IELTS Practice — Backend

Python backend for an IELTS practice platform. Serves test content, manages sessions, auto-grades Reading/Listening, and orchestrates AI-graded Writing/Speaking via Claude + Whisper.

The frontend lives at `../ielts-frontend` (Next.js). This folder exposes a REST API consumed by it.

## Tech stack

- **Language**: Python 3.11+
- **Web framework**: FastAPI (async)
- **ORM**: SQLAlchemy 2.0 (async) with Alembic migrations
- **Validation**: Pydantic v2 schemas
- **Database**: PostgreSQL 15+
- **Cache + queue broker**: Redis 7+
- **Background jobs**: ARQ (async Redis queue) for AI assessment jobs
- **Object storage**: Cloudflare R2 (S3-compatible) via `aioboto3` (audio uploads, test assets) — only the endpoint/credentials differ from S3; code is identical
- **AI grading**: Anthropic SDK (`anthropic`) — model `claude-sonnet-4-6`
- **Speech-to-Text**: OpenAI Whisper API (for Speaking transcription)
- **Auth**: JWT (access + refresh) via `python-jose`; passwords with `argon2-cffi`
- **Dependency / packaging**: `uv` (fast resolver) + `pyproject.toml`
- **Lint / format**: `ruff` (lint + format) + `mypy` (strict)
- **Testing**: `pytest` + `pytest-asyncio` + `httpx` async client; integration tests hit a real Postgres (testcontainers or docker-compose)

## Architecture

Modular monolith plus a separate AI worker process — two deployables:

1. **`api/`** — FastAPI app handling auth, tests, sessions, auto-grading, progress, media
2. **`worker/`** — ARQ worker consuming jobs from Redis; runs Whisper STT + Claude evaluation

Both share the same `core/` package (models, schemas, db, settings).

### Hosting

Cost-optimized, Cloudflare-centric (Cloudflare's own compute can't run this stack — async Python + native deps + always-on ARQ worker — so the Python processes run behind it):

- **Compute**: Fly.io — one image, two processes (`api` web + `worker` always-on ARQ), scaled independently
- **Edge / CDN / TLS / WAF**: Cloudflare in front of the API; serves R2 assets via signed URLs
- **Postgres**: Neon, **pooled endpoint** (`-pooler` host, PgBouncer transaction mode) — required for async connection-pool safety. Not Hyperdrive (Workers-only binding, unreachable from Fly)
- **Redis**: co-located on Fly (volume + AOF persistence) or Upstash
- **Migrations**: Alembic run as a Fly release command — never on boot, never `create_all()`
- **Local parity**: Docker Compose runs Postgres + Redis + MinIO (R2/S3-compatible stand-in)

```
backend/
├── api/                  # FastAPI app (HTTP layer)
│   ├── routers/          # endpoint modules per domain
│   ├── deps.py           # FastAPI dependencies (auth, db session)
│   └── main.py
├── worker/               # ARQ worker
│   ├── tasks/            # job handlers (grade_writing, grade_speaking)
│   └── main.py
├── core/                 # shared
│   ├── models/           # SQLAlchemy models
│   ├── schemas/          # Pydantic schemas
│   ├── services/         # business logic (grading, AI prompts)
│   ├── db.py             # async engine + session factory
│   ├── settings.py       # Pydantic Settings (env vars)
│   └── ai/               # Claude + Whisper clients, prompts, rubrics
├── alembic/              # migrations
├── tests/
└── pyproject.toml
```

## Key domain concepts

- **User** — has a `plan` (`free` | `paid`) that gates AI-graded test volume (see Freemium)
- **Test** — a complete IELTS test (one of: full / reading / listening / writing / speaking)
- **Section** — within a test (e.g. Reading has 3 passages, Listening has 4 sections)
- **Question** — typed (multiple_choice, fill_blank, matching, short_answer, essay_task1, essay_task2, speaking_part1/2/3)
- **TestSession** — a user's attempt at a test; tracks `started_at`, `submitted_at`, status
- **Answer** — user's response to a question; for auto-graded types, scored synchronously; for AI-graded, points to an `AIAssessment` row
- **AIAssessment** — band scores per criterion + overall + feedback text; populated asynchronously by worker. The frontend **polls** the results/assessment endpoint until status flips `processing → ready` (no SSE/WebSocket in v1)
- **Entitlement** — per-user, per-skill daily counter for AI-graded tests (see Freemium)

## Conventions

- **Async everywhere** — no sync DB calls, no `requests` (use `httpx.AsyncClient`)
- **Pydantic schemas** separate from SQLAlchemy models — never return ORM objects from endpoints
- **Service layer** holds business logic; routers are thin (parse request → call service → return schema)
- **Settings** via `pydantic-settings`; never read `os.environ` directly outside `core/settings.py`
- **Time** stored as `TIMESTAMPTZ` (UTC) in Postgres; never naive datetimes
- **IDs** UUIDv7 (sortable) for primary keys, not auto-increment integers
- **Errors** raise typed exceptions in services; an exception handler maps them to HTTP responses — do not raise `HTTPException` from services
- **Tests** prefer integration tests against a real Postgres over mocks; mock only external APIs (Anthropic, OpenAI, R2)
- **No print/logger.info debugging** left in committed code — use structured logging via `structlog`

## AI grading

- Prompts and IELTS band rubrics live in `core/ai/prompts/` — version them, do not inline in service code
- **Prompt caching is mandatory** — the IELTS rubric system prompt is large and reused on every grading call; mark it with `cache_control` to cut cost and latency dramatically
- Claude is called with **structured outputs** (tool use) to return JSON: per-criterion band scores + overall + feedback bullets
- Writing: send full text to Claude
- Speaking: download audio from R2 → Whisper transcribe → send transcript + IELTS speaking rubric to Claude
- Always store the raw model response alongside the parsed result for debugging and re-evaluation
- Failed AI jobs go to a dead-letter queue, retried up to 3 times with exponential backoff

## Security notes

- Audio uploads use **pre-signed R2 PUT URLs** — backend never proxies upload bytes (R2 bucket CORS required for browser uploads)
- Session timers are **server-enforced** — `started_at` checked on submission, not client-supplied
- JWTs short-lived (15 min access, 30 day refresh); refresh tokens rotate on use

## Freemium / entitlements

The product is freemium. Limits apply **only to the expensive AI-graded path** (Writing/Speaking → Claude/Whisper); Reading/Listening auto-grading is always unlimited.

- **Tiers** — `User.plan` ∈ {`free`, `paid`}
- **Reset** — fixed daily reset (e.g. midnight UTC); stored as a counter keyed by `(user, skill, day)`
- **Pools** — separate per skill (independent Writing and Speaking counters)
- **Limits** — tunable via settings, not hardcoded. Defaults: free 3/day each; paid 30/day each (a high ceiling, not unlimited, so even paid users retain cost protection)
- **Enforcement** — `core/services/entitlements.check_and_consume(user, skill)` is called at the **enqueue point**, before any Anthropic/Whisper spend. Over limit → typed `QuotaExceededError` → HTTP 402/429 with a `resets_at` timestamp
- **Billing** — tiers modeled now; Stripe (Checkout + webhooks + portal) is a later phase. Upgrades are set manually/admin for now

## Commands

*To be added once the project is scaffolded.*

Expected commands:
- `uv sync` — install deps
- `uvicorn api.main:app --reload` — run API
- `arq worker.main.WorkerSettings` — run worker
- `alembic upgrade head` — migrate DB
- `pytest` — run tests
- `ruff check . && ruff format --check . && mypy .` — lint + format check

## Things to check before implementation

- When touching AI grading, read `core/ai/prompts/` first — band descriptors are calibrated and small wording changes shift scores meaningfully
- When adding endpoints, update the OpenAPI tags so the frontend's codegen stays organized
- Always run migrations through Alembic; never `Base.metadata.create_all()` outside tests

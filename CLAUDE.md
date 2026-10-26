# IELTS Practice — Backend

Python backend for an IELTS practice platform. Serves test content, manages sessions, auto-grades Reading/Listening, and orchestrates grading of Writing (AI via OpenRouter) and Speaking.

The frontend lives at `../ielts-frontend` (Next.js). This folder exposes a REST API consumed by it.

## Tech stack

- **Language**: Python 3.11+
- **Web framework**: FastAPI (async)
- **ORM**: SQLAlchemy 2.0 (async) with Alembic migrations
- **Validation**: Pydantic v2 schemas
- **Database**: PostgreSQL 15+ (also the record of AI grading job state — no Redis/queue in v1)
- **Background jobs**: in-process `asyncio` tasks inside the API (FastAPI `BackgroundTasks`) for AI assessment
- **Object storage**: Cloudflare R2 (S3-compatible) via `aioboto3` (audio uploads, test assets) — only the endpoint/credentials differ from S3; code is identical
- **AI grading**: OpenRouter (OpenAI-compatible chat completions API) via `httpx.AsyncClient` — default model `deepseek/deepseek-v4.1-flash-1017`, overridable with `AI_MODEL`
- **Auth**: JWT (access + refresh) via `python-jose`; passwords with `argon2-cffi`
- **Dependency / packaging**: `uv` (fast resolver) + `pyproject.toml`
- **Lint / format**: `ruff` (lint + format) + `mypy` (strict)
- **Testing**: `pytest` + `pytest-asyncio` + `httpx` async client; integration tests hit a real Postgres via testcontainers (Docker must be running)

## Architecture

**Simple modular monolith — one deployable, one process.** The FastAPI app (`api/`) handles everything: auth, tests, sessions, auto-grading, progress, media, and AI grading. Business logic lives in `core/` (models, schemas, services, db, settings, ai).

Reading/Listening auto-grading is synchronous: a session is scored inside the transaction that submits it (`on_session_submitted`). If marking throws, the session is still submitted unscored; on startup the app scores every unscored auto session in the background (`run_startup_scoring`, `STARTUP_SCORING_ENABLED`), so the deploy that fixes a scoring bug also fills in its scores.

AI grading (model evaluation via OpenRouter) runs as an in-process background task started after the request returns. The `AIAssessment` row in Postgres *is* the job record (internal status `processing → ready | failed`), so no Redis or queue is needed. Speaking is graded by teachers (see Roles & human grading).

> **Deferred until scaling is needed:** a separate worker process + job queue (e.g. ARQ on Redis). The grading code lives in `core/services/` precisely so it can later be called from a worker instead of a background task without rewriting it.

### Hosting

Cost-optimized, Cloudflare-centric (Cloudflare's own compute can't run async Python + native deps, so the Python process runs behind it):

- **Compute**: Fly.io — one image, one process (`api`), exactly one always-on machine (the startup grading sweep assumes this)
- **Edge / CDN / TLS / WAF**: Cloudflare in front of the API; serves R2 assets via signed URLs
- **Postgres**: Neon, **pooled endpoint** (`-pooler` host, PgBouncer transaction mode) — required for async connection-pool safety. Not Hyperdrive (Workers-only binding, unreachable from Fly)
- **Migrations**: Alembic run as a Fly release command — never on boot, never `create_all()`
- **Local parity**: Docker Compose runs Postgres + MinIO (R2/S3-compatible stand-in)

```
ielts-backend/
├── api/                  # FastAPI app (HTTP layer)
│   ├── routers/          # endpoint modules per domain
│   ├── deps.py           # FastAPI dependencies (auth, db session)
│   └── main.py
├── core/                 # business logic
│   ├── models/           # SQLAlchemy models
│   ├── schemas/          # Pydantic schemas
│   ├── services/         # business logic (grading, AI prompts)
│   ├── db.py             # async engine + session factory
│   ├── settings.py       # Pydantic Settings (env vars)
│   ├── exceptions.py     # AppError hierarchy → HTTP mapping
│   ├── logging.py        # structlog + request-id middleware
│   ├── cli.py            # admin CLI (set-role)
│   └── ai/               # OpenRouter client, prompts, rubrics (planned)
├── alembic/              # migrations
├── tests/
└── pyproject.toml
```

## Key domain concepts

- **User** — has a `plan` (`free` | `paid`) that gates Writing/Speaking test volume (see Freemium) and a `role` (`student` | `teacher` | `admin`, see Roles)
- **Test** — a single-skill IELTS test (one of: reading / listening / writing / speaking); students take one skill at a time
- **TestVersion** — a test's content as versioned JSONB documents (`content` student-visible, `answer_key`, `staff_content`); `draft → published → retired`; a published version never changes, every edit is a new draft version. Full design: `docs/superpowers/specs/2026-10-24-tests-and-sessions-design.md`
- **Section** — within a test (Reading has 3 passages, Listening has 4 parts — "parts" to students, Writing 2 tasks, Speaking 3 parts)
- **Question** — typed (multiple_choice, fill_blank, matching, short_answer, essay_task1, essay_task2, speaking_part1/2/3)
- **TestSession** — a user's attempt at one test version; `mode` (`practice` = no time limit | `exam` = server deadline + 30 s grace), `started_at`, `deadline`, `submitted_at`, status, and `grading_method` (`auto` | `ai` | `human`, fixed at submission). Answers autosave; expired exam sessions are auto-submitted (on the next read/save, and by an in-process loop every minute)
- **Answer** — user's response to a question; for auto-graded types, scored synchronously (marking rules in `core/services/auto_grading.py`; Reading versions carry a `module`, `academic | general_training`, that picks the band table in `core/services/bands.py`; result at `GET /sessions/{id}/result`); AI-graded Writing points to an `AIAssessment` row; human-graded sessions have a `HumanGrade`
- **AIAssessment** — Writing only. Band scores per criterion + overall + feedback text; populated asynchronously by an in-process background task. The frontend **polls** the result endpoint, where students only ever see `being_graded → graded` (no SSE/WebSocket in v1)
- **HumanGrade** — one per human-graded session (all Speaking; Writing on request or after AI failure); `awaiting_grader → claimed → graded`; `origin` ∈ `speaking | student_request | ai_fallback`
- **Entitlement** — per-user, per-skill daily counter for Writing and Speaking tests (see Freemium)

## Roles & human grading

Full design: `docs/superpowers/specs/2026-10-24-human-grading-design.md`.

- **Roles** — `student` takes tests and sees own results; `teacher` grades from the queue; `admin` does everything a teacher can, sees all results, manages users and claims, and sees the grading overview
- **Speaking** is always graded by a teacher. **Writing** is graded by AI unless the student sets `request_manual_grading` at submission — then it goes to the teacher queue instead
- **One shared queue** (oldest first) with an atomic claim so no two teachers grade the same test; drafts are private until the grade is submitted; admins can release or reassign stuck claims
- **AI failure** — a Writing test whose AI grading fails all 3 attempts drops into the teacher queue (`AI_FAILURE_FALLBACK_TO_TEACHER`, default on)
- **Bands** — criteria in half-band steps (0–9); overall uses the British Council rounding rule (nearest half band, .25/.75 round up); Writing weights Task 2 double. All arithmetic lives in `core/services/bands.py`

## Conventions

- **Async everywhere** — no sync DB calls, no `requests` (use `httpx.AsyncClient`)
- **Pydantic schemas** separate from SQLAlchemy models — never return ORM objects from endpoints
- **Service layer** holds business logic; routers are thin (parse request → call service → return schema)
- **Settings** via `pydantic-settings`; never read `os.environ` directly outside `core/settings.py`
- **Time** stored as `TIMESTAMPTZ` (UTC) in Postgres; never naive datetimes
- **IDs** UUIDv7 (sortable) for primary keys, not auto-increment integers
- **Errors** raise typed exceptions in services; an exception handler maps them to HTTP responses — do not raise `HTTPException` from services
- **Tests** prefer integration tests against a real Postgres over mocks; mock only external APIs (OpenRouter, R2)
- **No print/logger.info debugging** left in committed code — use structured logging via `structlog`

## AI grading

AI grading covers **Writing only** (Speaking is graded by teachers).

- Prompts and IELTS band rubrics live in `core/ai/prompts/` — version them, do not inline in service code
- **Prompt caching is mandatory** — the IELTS rubric system prompt is large and reused on every grading call; send it as an identical, unchanging prefix (system message first, student text last) so the provider's automatic prefix caching applies (DeepSeek caches repeated prefixes automatically). If `AI_MODEL` is switched to a provider that needs explicit `cache_control` breakpoints (e.g. Anthropic, Gemini), add them
- The model is called with **structured outputs** (`response_format` JSON schema, or tool calling) to return JSON: per-criterion band scores + overall + feedback bullets; always validate the JSON with Pydantic, and check a new `AI_MODEL` supports structured outputs on OpenRouter before switching
- Writing: send full text to the model
- Always store the raw model response alongside the parsed result for debugging and re-evaluation
- Grading runs as an in-process background task: up to 3 attempts with exponential backoff; on final failure the `AIAssessment` row is marked `failed` with the error stored **and** the test is handed to the teacher queue as a `HumanGrade(origin=ai_fallback)` (unless `AI_FAILURE_FALLBACK_TO_TEACHER` is off)
- In-process tasks die with the process — on startup, re-run any `AIAssessment` rows stuck in `processing` (never `failed` ones) so a deploy/restart never strands a grading job. This assumes a single machine — revisit (lease/stale threshold or a real worker) before running more than one

## Security notes

- Audio uploads use **pre-signed R2 PUT URLs** — backend never proxies upload bytes (R2 bucket CORS required for browser uploads)
- Session timers are **server-enforced** — `started_at` checked on submission, not client-supplied
- JWTs short-lived (15 min access, 30 day refresh); refresh tokens rotate on use
- **Roles** are checked from the `users` row on every request (never a JWT claim), so a role change applies on the next request. Sign-up always creates a `student`; only admins change roles; the first admin is created with `python -m core.cli set-role <email> admin`

## Freemium / entitlements

The product is freemium. Limits apply **only to Writing and Speaking**; Reading/Listening auto-grading is always unlimited.

- **Tiers** — `User.plan` ∈ {`free`, `paid`}
- **Reset** — fixed daily reset (e.g. midnight UTC); stored as a counter keyed by `(user, skill, day)`
- **Pools** — separate per skill (independent Writing and Speaking counters)
- **Limits** — tunable via settings, not hardcoded. Defaults: free 3/day each; paid 30/day each (a high ceiling, not unlimited, so even paid users retain cost protection)
- **Enforcement** — `core/services/entitlements.check_and_consume(db, user, skill, now)` is called when a Writing/Speaking session **starts** (practice or exam), before any work or AI spend; an abandoned test still counts. Over limit → typed `QuotaExceededError` → HTTP 429 with a `resets_at` timestamp
- **Billing** — tiers modeled now; Stripe (Checkout + webhooks + portal) is a later phase. Upgrades are set manually/admin for now

## Commands

- `uv sync` — install deps
- `docker compose up -d` — start Postgres + MinIO locally
- `uv run uvicorn api.main:app --reload` — run the app (the only process)
- `uv run alembic upgrade head` — migrate DB
- `uv run python -m core.cli set-role <email> <role>` — set a user's role (bootstrap the first admin)
- `uv run python -m core.cli load-test <file.json> [--publish]` — load a test file (e.g. `seed/tests/practice-01-reading.json`) as a draft, optionally publish it
- `uv run python -m core.cli regrade-auto [--all]` — score Reading/Listening sessions that have no score (the startup sweep does this on every deploy); `--all` re-scores every session after a marking-rule or band-table change
- `uv run pytest` — run tests (Docker must be running)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — lint + format + type check

## Things to check before implementation

- When touching AI grading, read `core/ai/prompts/` first — band descriptors are calibrated and small wording changes shift scores meaningfully
- When adding endpoints, update the OpenAPI tags so the frontend's codegen stays organized
- Always run migrations through Alembic; never `Base.metadata.create_all()` outside tests

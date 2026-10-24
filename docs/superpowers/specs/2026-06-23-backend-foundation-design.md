# IELTS Backend — Design & Build-Out Plan

**Date:** 2026-06-23
**Status:** Approved (design); Foundation phase implemented.
**Revised 2026-10-20:** simplified to a **single-process monolith** — the separate
ARQ worker and Redis were removed so the whole system is one FastAPI app plus
Postgres (and R2 for files). A worker + queue is deferred until scaling needs it
(see §5).
**Scope:** Full backend build-out, foundation-first, production-ready maturity.

This document is the master design for the IELTS practice backend. It fully
specs the **Foundation phase** and lays out the remaining domains as a
**roadmap**, each of which gets its own spec → plan → build cycle.

---

## 1. Goal & intent

Build the entire FastAPI backend described in `CLAUDE.md` from an empty repo.
Both `ielts-backend` and `ielts-frontend` are greenfield (only a `CLAUDE.md`
each). "Integrating the backend" here means building the backend so it boots,
is deployable, and honors the contract the Next.js frontend depends on.

**Decisions taken during brainstorming:**

- **Build the whole backend** (not just a slice), but specced incrementally.
- **Foundation-first**, then domains one at a time.
- **Production-ready** maturity bar from day one.
- **Cloudflare-centric, cost-optimized** infrastructure (cheaper than AWS).
- **Polling** (not SSE) to deliver async AI results to the frontend for v1.
- **Single-process monolith** (revised 2026-10-20): AI grading runs as in-process
  background tasks tracked in Postgres. No Redis, no ARQ, no worker — yet.
- **Freemium** product: unlimited auto-graded tests; daily-capped AI-graded tests.

---

## 2. Architecture recap

Modular monolith — **one deployable, one process**. `api/` is the HTTP layer;
`core/` holds everything else:

| Module | Responsibility | Depends on |
|---|---|---|
| `core/settings` | typed env config (pydantic-settings) | — |
| `core/db` | async engine, session factory, `Base` | settings |
| `core/models` | SQLAlchemy models (UUIDv7 PKs, TIMESTAMPTZ) | db |
| `core/schemas` | Pydantic v2 request/response DTOs | — |
| `core/services` | business logic; raises typed exceptions | models, schemas |
| `core/ai` | Claude client, prompts, rubrics | settings |
| `api` | FastAPI HTTP layer, routers, deps, exc handlers; starts grading background tasks | core |

All durable state lives in Postgres and object storage. The one in-memory thing
is an in-flight grading task; because its status is persisted on the
`AIAssessment` row, a restart can find and resume it (§5).

---

## 3. Deployment & infrastructure

Cost-optimized, Cloudflare-centric. Cloudflare's compute (Workers/Containers)
**cannot** run this stack (async Python, native deps), so the Python process
runs on a cheap container host **behind** Cloudflare.

| Concern | Choice | Notes |
|---|---|---|
| Edge / CDN / TLS / WAF | **Cloudflare** | Free tier; proxies the Fly API, serves R2 assets |
| Compute | **Fly.io** | One image, one process (`api`), **exactly one machine**, always on (no scale-to-zero mid-grading; see §5 for why not more) |
| Object storage | **Cloudflare R2** | S3-compatible → `aioboto3` unchanged; pre-signed PUT for uploads; **zero egress** for Listening audio |
| Asset delivery | R2 + Cloudflare, **signed URLs** | Paid IELTS content stays private |
| Postgres | **Neon**, **pooled endpoint** | Use Neon's `-pooler` host (PgBouncer, transaction mode) — fixes async pool exhaustion. *(Not Hyperdrive — that's a Workers-only binding, unreachable from Fly.)* |
| Migrations | **Alembic as a Fly release command** | Never auto-run on boot; never `create_all()` |
| Secrets | **Fly secrets** | Read via `core/settings.py` only |
| Local parity | **Docker Compose** | Postgres + **MinIO** (R2/S3-compatible stand-in) |

**Portability note:** the application code is identical to an AWS deployment —
only endpoints and credentials change (R2 endpoint, Neon pooled URL). Nothing about the application design is locked to Cloudflare.

---

## 4. Frontend integration contract

Pinned so the two greenfield repos don't drift:

- **OpenAPI codegen** — the frontend runs `openapi-typescript` against the
  backend's schema. Every endpoint gets an explicit OpenAPI **tag** and a stable
  **`operation_id`** (documented naming convention) so generated types stay clean.
- **Auth** — JWT **access (15 min) + refresh (30 day)**, refresh **rotates on
  use**, consumed behind the Next.js httpOnly-cookie proxy.
- **Uploads** — pre-signed **R2 PUT** URLs; backend never proxies upload bytes.
- **Async AI results** — **polling**: frontend `GET`s the assessment/results
  endpoint until status flips `processing → ready`. SSE/WebSocket noted as a
  future enhancement (e.g. live grading progress) — not v1.
- **Path note:** both `CLAUDE.md` files reference `../frontend` / `../backend`,
  but the real directories are `ielts-frontend` / `ielts-backend`. Any codegen or
  cross-repo path must use the real names.

---

## 5. Async grading model (in-process; queue later)

The freemium UX — *submit → "grading in progress" → user leaves → state turns
green → results + breakdown appear* — needs work to happen **after** the HTTP
response. In v1 that work runs **inside the API process**; no Redis, no queue,
no worker:

1. User submits a Writing test (Speaking is graded by teachers — see
   `2026-10-24-human-grading-design.md`).
2. API checks entitlements (§6), inserts an `AIAssessment` row with status
   `processing`, schedules the grading coroutine as a background task (FastAPI
   `BackgroundTasks`), and returns immediately.
3. The background task runs `core/services/grading`: Claude evaluates the
   text → write scores + raw response to the row; flip status to `ready`.
4. Frontend **polls** until the row is `ready` (or `failed`); shows bands +
   per-criterion breakdown + feedback.

**The `AIAssessment` row is the job record.** That gives us, without a queue:

- **Retries** — the task makes up to 3 attempts with exponential backoff. After
  the last one it sets status `failed`, stores the error, and hands the test to
  the teacher queue (see `2026-10-24-human-grading-design.md` §4.1).
- **Restart safety** — in-process tasks die with the process (deploys, crashes).
  On app startup (lifespan hook), re-schedule every row still in `processing`.
  Grading must therefore be **idempotent** per row (overwrite, never append).
  This sweep assumes **exactly one API machine**: with two, a booting machine
  would re-grade rows the other is still working on (double spend, racing
  writes). Going multi-machine is therefore a trigger for adding a worker (below).
- **Background task opens its own DB session** — the request's session is closed
  by the time the task runs.

**Known trade-offs (accepted for v1):** grading shares CPU/event loop and
connection pool with HTTP traffic; you can't scale grading separately from the
API; long Claude calls hold a coroutine (cheap — they're I/O-bound).

**When to add a worker:** if grading load starts slowing HTTP responses, or we
need more than one API machine. Then: add a queue (ARQ on Redis
is the default choice), move the call site from `BackgroundTasks` to `enqueue`,
and run the same `core/services/grading` function from the worker. Because the
logic already lives in `core/`, that is a small change.

**Kafka is out of scope:** it's a backend-internal event log (the browser can't
consume it, so polling is still required), and it's heavy/costly versus this
workload.

---

## 6. Freemium / entitlements model

Limits apply **only to Writing and Speaking**: the Writing limit caps Claude
spend, the Speaking limit caps teacher workload. Reading/Listening auto-grading
is always unlimited.

- **Tiers** — `User.plan` ∈ {`free`, `paid`}.
- **Reset model** — **fixed daily reset** (e.g. midnight UTC). Stored as a
  counter keyed by `(user, skill, day)`.
- **Pools** — **separate** per skill (independent Writing and Speaking counters).
- **Default limits (tunable via settings, not hardcoded):**
  - Free: **3 Writing/day + 3 Speaking/day**
  - Paid: **30 Writing/day + 30 Speaking/day** (high ceiling, not unlimited, so
    even paid users retain CLAUDE.md's cost-protection guarantee)
- **Enforcement** — `core/services/entitlements.check_and_consume(user, skill)`
  is called at **submission**, *before* the grading task starts and before any
  Anthropic spend.
  Reading/Listening bypass it. Over limit → typed `QuotaExceededError` → HTTP
  **402/429** with a `resets_at` timestamp the frontend displays.
- **Billing** — **tiers modeled now; payments deferred.** Upgrade is set
  manually/admin for now. Full **Stripe** integration (Checkout + webhooks +
  customer portal) is its own later phase.

---

## 7. Build phases (roadmap)

1. **Foundation** — fully specced in §8.
1b. **Roles & admin users** — `users.role` (student/teacher/admin), role checks,
   admin user management, first-admin CLI, band arithmetic. Plan:
   `plans/2026-10-24-roles-and-admin-users.md`.
2. **Test content & sessions** — Test/Section/Question/TestSession/Answer
   models, content read endpoints, session lifecycle with server-enforced timers.
3. **Auto-grading** — Reading + Listening synchronous scoring.
4. **AI pipeline (Writing only)** — `core/ai`, `AIAssessment`, in-process background grading
   task (§5), mandatory prompt caching, retry + `failed` status, startup resume
   of stuck rows, polling result delivery.
5. **Entitlements** — per-skill daily quotas, `plan` gate at submission (§6).
6. **Media** — R2 pre-signed PUT URLs; **R2 bucket CORS** for browser uploads.
6b. **Human grading** — teacher queue for Speaking and requested/failed
   Writing, claims, drafts, admin results + overview
   (`specs/2026-10-24-human-grading-design.md`). Needs phases 2, 4, 5, 6.
7. **Progress dashboard** — band-score trends per skill, weak areas.
8. *(later)* **Stripe billing.**
9. *(later)* **Deploy** to Fly.io + Cloudflare.
10. *(later, only when needed)* **Scale out grading** — add a job queue + separate
    worker process (§5, "When to add a worker").

Each phase (2–9) gets its own dated spec → plan → build cycle.

---

## 8. Foundation phase — deep spec

**End state (acceptance criteria):**

- The `api` process boots (single deployable; no worker, no Redis).
- Full auth flow works: register → login → access protected route → refresh → logout.
- `GET /health` returns green (checks DB).
- `GET /docs` emits OpenAPI with explicit tags and stable operation IDs.
- Schema changes run via Alembic; first migration creates `users` + refresh tokens.
- CI is green: `uv sync`, `ruff check`, `ruff format --check`, `mypy` (strict), `pytest`.

**Repo skeleton:**

```
ielts-backend/
├── api/
│   ├── main.py                 # app factory, routers, exc handlers, OpenAPI tags
│   ├── deps.py                 # get_db, get_current_user
│   └── routers/{auth.py, health.py}
├── core/
│   ├── settings.py             # pydantic-settings: DB, R2, Anthropic, JWT, plan limits
│   ├── db.py                   # async engine (asyncpg) + session factory + Base
│   ├── logging.py              # structlog config + request-id middleware
│   ├── exceptions.py           # AppError hierarchy → HTTP mapping
│   ├── models/{base.py, user.py, refresh_token.py}
│   ├── schemas/{auth.py, user.py}
│   └── services/auth.py
├── alembic/                    # async env.py + first migration
├── tests/                      # pytest + testcontainers Postgres
├── Dockerfile
├── docker-compose.yml          # postgres + minio
├── pyproject.toml              # uv, ruff, mypy strict
└── .github/workflows/ci.yml
```

**Key decisions:**

- **Settings** — one `Settings` object; nothing else reads `os.environ`. Groups
  per external dep. R2 uses S3-compatible vars (`endpoint_url` + key/secret) so
  `aioboto3` is unchanged. Plan limits live here (tunable).
- **Base model** — `DeclarativeBase` + mixin: **UUIDv7** PK, `created_at` /
  `updated_at` as `TIMESTAMPTZ` (UTC), Alembic-friendly constraint naming
  convention. **UUIDv7 needs a library** — Python 3.11 `uuid` has no `uuid7()`
  and Postgres 15 has no native `uuidv7()`; generate in Python via `uuid-utils`
  (or `uuid6`). The implementation plan picks the lib.
- **Auth** — register / login / refresh / logout / me. **Argon2** passwords
  (`argon2-cffi`); JWT **access 15 min / refresh 30 day** (`python-jose`);
  **refresh rotation** — persist `refresh_token` rows by `jti`, revoke-on-use to
  detect token reuse. `User.plan` defaults to `free`.
- **Errors** — services raise typed `AppError`s; a single exception handler maps
  them to HTTP. Services never raise `HTTPException`.
- **Logging** — `structlog` only; request-id middleware; no `print` /
  `logger.info` debugging left in committed code.
- **Frontend contract** — explicit OpenAPI tags + stable `operation_id`
  convention documented and applied from the first endpoints.
- **Single process** — no worker in this phase (originally an ARQ worker +
  Redis; removed 2026-10-20 to keep the system a simple monolith).
- **Local parity** — Docker Compose: Postgres + MinIO (R2 stand-in).
- **CI** — GitHub Actions: `uv sync` → `ruff check` + `ruff format --check` →
  `mypy` (strict) → `pytest` (testcontainers Postgres).
- **Tests** — auth flow end-to-end + `/health`, against real Postgres;
  external APIs mocked (none needed this phase).

---

## 9. Open items for implementation planning

- Pick the UUIDv7 library (`uuid-utils` vs `uuid6`).
- Confirm CORS posture: browser→Next→backend is same-origin (cookie proxy), so
  API CORS may be unnecessary; the CORS that matters is **R2 bucket CORS** for
  pre-signed browser uploads (Media phase).
- *(When a worker is added later)* decide queue/broker hosting, e.g. Redis on Fly vs Upstash.

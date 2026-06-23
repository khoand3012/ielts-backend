# IELTS Backend — Design & Build-Out Plan

**Date:** 2026-06-23
**Status:** Approved (design); Foundation phase ready for implementation planning
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
- **Redis + ARQ** (not Kafka) for the grading job queue.
- **Freemium** product: unlimited auto-graded tests; daily-capped AI-graded tests.

---

## 2. Architecture recap

Modular monolith + a separate AI worker — two deployables sharing `core/`:

| Module | Responsibility | Depends on |
|---|---|---|
| `core/settings` | typed env config (pydantic-settings) | — |
| `core/db` | async engine, session factory, `Base` | settings |
| `core/models` | SQLAlchemy models (UUIDv7 PKs, TIMESTAMPTZ) | db |
| `core/schemas` | Pydantic v2 request/response DTOs | — |
| `core/services` | business logic; raises typed exceptions | models, schemas |
| `core/ai` | Claude + Whisper clients, prompts, rubrics | settings |
| `api` | FastAPI HTTP layer, routers, deps, exc handlers | core |
| `worker` | ARQ jobs (grade_writing, grade_speaking) | core/ai, core |

The API is stateless; the worker is an **always-on process** that watches the
Redis queue. All state lives in Postgres / Redis / object storage, so both
processes scale horizontally and restart freely.

---

## 3. Deployment & infrastructure

Cost-optimized, Cloudflare-centric. Cloudflare's compute (Workers/Containers)
**cannot** run this stack (async Python, native deps, always-on ARQ worker), so
the Python processes run on a cheap container host **behind** Cloudflare.

| Concern | Choice | Notes |
|---|---|---|
| Edge / CDN / TLS / WAF | **Cloudflare** | Free tier; proxies the Fly API, serves R2 assets |
| API + worker compute | **Fly.io** | One image, two processes: `api` (web) + `worker` (always-on ARQ); scale independently |
| Object storage | **Cloudflare R2** | S3-compatible → `aioboto3` unchanged; pre-signed PUT for uploads; **zero egress** for Listening audio |
| Asset delivery | R2 + Cloudflare, **signed URLs** | Paid IELTS content stays private |
| Postgres | **Neon**, **pooled endpoint** | Use Neon's `-pooler` host (PgBouncer, transaction mode) — fixes async pool exhaustion. *(Not Hyperdrive — that's a Workers-only binding, unreachable from Fly.)* |
| Redis (broker + cache) | **Co-located on Fly** (recommended) or Upstash | Fly Redis app + volume + AOF persistence so queued jobs survive restarts |
| Migrations | **Alembic as a Fly release command** | Never auto-run on boot; never `create_all()` |
| Secrets | **Fly secrets** | Read via `core/settings.py` only |
| Local parity | **Docker Compose** | Postgres + Redis + **MinIO** (R2/S3-compatible stand-in) |

**Portability note:** the application code is identical to an AWS deployment —
only endpoints and credentials change (R2 endpoint, Neon pooled URL, Fly Redis
URL). Nothing about the application design is locked to Cloudflare.

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
  future enhancement (e.g. live "transcribing → evaluating" progress) — not v1.
- **Path note:** both `CLAUDE.md` files reference `../frontend` / `../backend`,
  but the real directories are `ielts-frontend` / `ielts-backend`. Any codegen or
  cross-repo path must use the real names.

---

## 5. Async grading model (why ARQ, not Kafka)

The freemium UX — *submit → "grading in progress" → user leaves → state turns
green → results + breakdown appear* — is delivered by the standard async-worker
pattern, no event-streaming platform required:

1. User submits a Writing/Speaking test.
2. API checks entitlements (§7), then **enqueues a grading job** in Redis and
   returns immediately (`processing`).
3. The **ARQ worker** drains the queue: (Speaking) download audio from R2 →
   Whisper transcribe → Claude evaluate → write `AIAssessment`; flip status.
4. Frontend **polls** until the row is ready; shows bands + per-criterion
   breakdown + feedback.

ARQ also provides the retries + dead-letter behavior CLAUDE.md requires (≤3
retries, exponential backoff). **Kafka is out of scope:** it's a backend-internal
event log (the browser can't consume it, so polling is still required), and it's
heavy/costly versus this workload. It would only earn its place if many services
later need to react to the same events; noted as a future option, not v1.

---

## 6. Freemium / entitlements model

Limits apply **only to the expensive AI-graded path** (Writing/Speaking →
Claude/Whisper). Reading/Listening auto-grading is always unlimited.

- **Tiers** — `User.plan` ∈ {`free`, `paid`}.
- **Reset model** — **fixed daily reset** (e.g. midnight UTC). Stored as a
  counter keyed by `(user, skill, day)`.
- **Pools** — **separate** per skill (independent Writing and Speaking counters).
- **Default limits (tunable via settings, not hardcoded):**
  - Free: **3 Writing/day + 3 Speaking/day**
  - Paid: **30 Writing/day + 30 Speaking/day** (high ceiling, not unlimited, so
    even paid users retain CLAUDE.md's cost-protection guarantee)
- **Enforcement** — `core/services/entitlements.check_and_consume(user, skill)`
  is called at the **enqueue point**, *before* any Anthropic/Whisper spend.
  Reading/Listening bypass it. Over limit → typed `QuotaExceededError` → HTTP
  **402/429** with a `resets_at` timestamp the frontend displays.
- **Billing** — **tiers modeled now; payments deferred.** Upgrade is set
  manually/admin for now. Full **Stripe** integration (Checkout + webhooks +
  customer portal) is its own later phase.

---

## 7. Build phases (roadmap)

1. **Foundation** — fully specced in §8.
2. **Test content & sessions** — Test/Section/Question/TestSession/Answer
   models, content read endpoints, session lifecycle with server-enforced timers.
3. **Auto-grading** — Reading + Listening synchronous scoring.
4. **AI pipeline** — `core/ai`, ARQ worker, `AIAssessment`, mandatory prompt
   caching, dead-letter + retry, polling result delivery.
5. **Entitlements** — per-skill daily quotas, `plan` gate at enqueue (§6).
6. **Media** — R2 pre-signed PUT URLs; **R2 bucket CORS** for browser uploads.
7. **Progress dashboard** — band-score trends per skill, weak areas.
8. *(later)* **Stripe billing.**
9. *(later)* **Deploy** to Fly.io + Cloudflare.

Each phase (2–9) gets its own dated spec → plan → build cycle.

---

## 8. Foundation phase — deep spec

**End state (acceptance criteria):**

- `api` and `worker` both boot (worker connects to Redis, runs a healthcheck task).
- Full auth flow works: register → login → access protected route → refresh → logout.
- `GET /health` returns green (checks DB + Redis).
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
├── worker/
│   ├── main.py                 # ARQ WorkerSettings; boots against Redis
│   └── tasks/__init__.py       # one no-op/health task (real jobs land Phase 4)
├── core/
│   ├── settings.py             # pydantic-settings: DB, Redis, R2, Anthropic, OpenAI, JWT, plan limits
│   ├── db.py                   # async engine (asyncpg) + session factory + Base
│   ├── logging.py              # structlog config + request-id middleware
│   ├── exceptions.py           # AppError hierarchy → HTTP mapping
│   ├── models/{base.py, user.py, refresh_token.py}
│   ├── schemas/{auth.py, user.py}
│   └── services/auth.py
├── alembic/                    # async env.py + first migration
├── tests/                      # pytest + testcontainers Postgres
├── Dockerfile
├── docker-compose.yml          # postgres + redis + minio
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
- **Worker** — minimal `WorkerSettings` + healthcheck task proving the second
  deployable boots against Redis.
- **Local parity** — Docker Compose: Postgres + Redis + MinIO (R2 stand-in).
- **CI** — GitHub Actions: `uv sync` → `ruff check` + `ruff format --check` →
  `mypy` (strict) → `pytest` (testcontainers Postgres).
- **Tests** — auth flow end-to-end + `/health`, against real Postgres;
  external APIs mocked (none needed this phase).

---

## 9. Deviations from CLAUDE.md

CLAUDE.md describes an AWS-shaped stack; this design intentionally differs (the
user's call, for cost). Flagged so the two sources don't silently disagree:

- **Object storage:** Cloudflare **R2** (S3-compatible) instead of AWS S3.
  `aioboto3` code is unchanged — only the endpoint/credentials differ.
- **Hosting:** **Fly.io** (compute) + **Cloudflare** (edge) + **Neon** (Postgres)
  instead of AWS-native.
- **Async results:** **polling** chosen over the "SSE or polling" CLAUDE.md left open.
- **New requirement:** freemium tiers + per-skill daily AI-grading quotas (§6).

**Action after spec approval:** offer to update `CLAUDE.md` (via the
claude-md-management skill) so the project memory matches this design.

---

## 10. Open items for implementation planning

- Pick the UUIDv7 library (`uuid-utils` vs `uuid6`).
- Confirm CORS posture: browser→Next→backend is same-origin (cookie proxy), so
  API CORS may be unnecessary; the CORS that matters is **R2 bucket CORS** for
  pre-signed browser uploads (Media phase).
- Decide Redis hosting: co-located Fly app vs Upstash (cost vs managed).

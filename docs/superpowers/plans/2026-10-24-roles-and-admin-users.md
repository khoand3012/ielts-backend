# Roles, Admin Users & Band Arithmetic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the three account roles (student / teacher / admin) with server-side enforcement, admin user management, a CLI to bootstrap the first admin, and the British Council band arithmetic — the parts of the human-grading design that do not depend on test sessions.

**Architecture:** `users.role` column (+ Alembic migration) checked on every request from the DB row `get_current_user` already loads; a `require_role` FastAPI dependency guards routers; a thin `admin` router calls a new `core/services/users.py`. Band arithmetic is a pure module `core/services/bands.py` used later by human and AI grading.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, pytest + testcontainers Postgres, `uv`.

**Spec:** `docs/superpowers/specs/2026-10-24-human-grading-design.md` (§3 roles, §6 band arithmetic, §7 admin *Users*, §11 plan changes). Read it alongside this plan.

## Global Constraints

- Follow `CLAUDE.md` conventions: async everywhere; Pydantic schemas never ORM objects in responses; thin routers; services raise `AppError` subclasses from `core/exceptions.py`, never `HTTPException`; settings only via `core/settings.py`; `structlog`, no leftover debug prints.
- Roles are exactly `student`, `teacher`, `admin`; default `student`. Plans are exactly `free`, `paid`.
- Sign-up always creates a `student`; `role` is never read from the registration body.
- Role is read from the `users` row on every request — never put a role claim in the JWT.
- Admins cannot change their own role away from `admin`.
- Criterion bands: 0–9 in 0.5 steps. Rounding: `floor(x × 2 + 0.5) / 2` with `Decimal`, never `float`.
- Writing overall = `round_band((T1 + 2 × T2) / 3)` using **unrounded** task means.
- Every endpoint has an OpenAPI tag and a stable `operation_id` of the form `<tag>_<verb>`.
- Schema changes only via Alembic migrations (tests use `create_all`, so verify migrations separately).
- The test database is shared across the whole test session and services commit — every test must use **unique emails** (e.g. `f"{prefix}-{uuid7()}@t.com"`) and never assert on global row counts.
- Gate after every task: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q` (Docker must be running).

## Review Focus

1. **Role change with an already-issued access token** — promoting or demoting a user must take effect on their very next request, without re-login. Test: Task 4, `test_role_change_applies_to_existing_token`.
2. **Registration body smuggling `"role": "admin"`** — must still create a student. Test: Task 2, `test_register_ignores_role_field`.
3. **Search text containing `%` or `_`** — must match literally, not as SQL wildcards. Test: Task 3, `test_list_users_search_is_literal`.
4. **Admin demoting themselves (the last-admin lockout)** — 403 and role unchanged; changing their own *plan* is still allowed. Tests: Task 3, `test_admin_cannot_demote_self`, `test_admin_can_change_own_plan`.
5. **Invalid role values from each entry point** — DB rejects (`CHECK`), API returns 422, CLI rejects with a usage error. Tests: Task 2 `test_role_check_constraint`, Task 4 `test_update_user_invalid_role_422`, Task 5 `test_cli_rejects_unknown_role`.

---

### Precondition: commit the pending monolith work

The working tree holds uncommitted, already-verified changes from earlier (single-process monolith: Redis/ARQ/worker removed; Whisper/OpenAI removed). Commit them on their own before Task 1 so each task's diff stays clean. **Do not stage `.gitignore`** (the user's own pending change).

- [ ] Run the gate (see Global Constraints). Expected: all green, 24 passed.
- [ ] Commit:

```bash
git add -u -- . ':!.gitignore'
git commit -m "refactor: single-process monolith; drop Redis/ARQ worker and Whisper/OpenAI"
```

- [ ] `git status --short` shows only ` M .gitignore`.

---

### Task 1: Apply the approved design to the living plan docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-06-23-backend-foundation-design.md` (tracked even though `docs/` is git-ignored)

**Interfaces:** none (docs only).

- [ ] **Step 1: Edit `CLAUDE.md`** with these changes (spec §11):
  - *Key domain concepts* — **User**: also has a `role` (`student` | `teacher` | `admin`). **TestSession**: add `grading_method` (`ai` | `human`, set at submission). **Answer**: AI-graded Writing points to an `AIAssessment`; human-graded sessions have a `HumanGrade`. **AIAssessment**: Writing only; students see only `being_graded → graded`. Add **HumanGrade** — one per human-graded session; `awaiting_grader → claimed → graded`; `origin` ∈ `speaking | student_request | ai_fallback`. **Entitlement**: per-user, per-skill daily counter for Writing and Speaking tests.
  - New section **Roles & human grading** (3–6 bullets): roles and who can do what; Speaking always teacher-graded; Writing AI unless `request_manual_grading`; one shared queue with atomic claim; AI failure after 3 attempts → teacher queue (`AI_FAILURE_FALLBACK_TO_TEACHER`, default on); bands in half steps with the British Council rounding rule; link to `docs/superpowers/specs/2026-10-24-human-grading-design.md`.
  - *AI grading* — state it covers Writing only; replace the final-failure bullet: row marked `failed` with error stored **and** handed to the teacher queue as a `HumanGrade(origin=ai_fallback)` (unless the setting is off); the startup sweep re-runs only `processing` rows, never `failed` ones.
  - *Security notes* — add: role checked from the DB row on every request (not a JWT claim); sign-up always creates a student; only admins change roles; the first admin is created with `python -m core.cli set-role <email> admin`.
  - *Commands* — add `python -m core.cli set-role <email> <role>`.
- [ ] **Step 2: Edit the foundation design spec:**
  - §5 *Retries* bullet: after the last attempt the row is `failed` **and** handed to the teacher queue (see the human-grading design §4.1).
  - §7 roadmap: Phase 4 is Writing only; add phases **Roles & admin users** (this plan) and **Human grading** (depends on Test content & sessions, Media, Entitlements, AI pipeline).
- [ ] **Step 3: Verify:** `grep -niE "whisper|openai|redis|arq" CLAUDE.md` shows only the "deferred worker (e.g. ARQ on Redis)" note.
- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-06-23-backend-foundation-design.md
git commit -m "docs: roles and human grading in project plan"
```

---

### Task 2: `Role`/`Plan` enums, `users.role` column, migration

**Files:**
- Modify: `core/models/user.py`
- Modify: `core/models/__init__.py` (export `Role`, `Plan`)
- Modify: `core/schemas/user.py` (`UserRead.role`)
- Create: `alembic/versions/0002_user_role.py`
- Create: `.env` from `.env.example` if missing (local only, never committed)
- Test: `tests/test_roles.py`

**Interfaces:**
- Produces:
  - `class Role(StrEnum)`: `STUDENT = "student"`, `TEACHER = "teacher"`, `ADMIN = "admin"` in `core/models/user.py`
  - `class Plan(StrEnum)`: `FREE = "free"`, `PAID = "paid"` in `core/models/user.py`
  - `User.role: Mapped[str]` — `String(16)`, `default=Role.STUDENT`, `server_default="student"`, plus `CheckConstraint("role IN ('student', 'teacher', 'admin')", name="role")` in `__table_args__` (naming convention makes it `ck_users_role`)
  - `UserRead.role: str`

- [ ] **Step 1: Write the failing tests** in `tests/test_roles.py`:

```python
async def test_new_user_is_student(db_session: AsyncSession) -> None:
    user = await auth.register(db_session, f"s-{uuid7()}@t.com", "password123")
    assert user.role == Role.STUDENT

async def test_role_check_constraint(db_session: AsyncSession) -> None:
    db_session.add(User(email=f"bad-{uuid7()}@t.com", password_hash="x", role="superuser"))
    with pytest.raises(IntegrityError):
        await db_session.flush()

async def test_register_returns_role(app_client: AsyncClient) -> None:
    resp = await app_client.post("/auth/register", json={"email": f"r-{uuid7()}@t.com", "password": "password123"})
    assert resp.status_code == 201
    assert resp.json()["role"] == "student"

async def test_register_ignores_role_field(app_client: AsyncClient) -> None:
    resp = await app_client.post(
        "/auth/register",
        json={"email": f"x-{uuid7()}@t.com", "password": "password123", "role": "admin"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "student"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_roles.py -v`. Expected: FAIL (`ImportError: Role`, then missing `role`).
- [ ] **Step 3: Implement** the model, enums, exports and `UserRead.role` per *Interfaces*. `RegisterRequest` stays unchanged (Pydantic ignores unknown fields by default — the test proves it).
- [ ] **Step 4: Write `alembic/versions/0002_user_role.py`** (`revision = "0002"`, `down_revision = "0001"`): `op.add_column("users", sa.Column("role", sa.String(length=16), server_default="student", nullable=False))` and `op.create_check_constraint(op.f("ck_users_role"), "users", "role IN ('student', 'teacher', 'admin')")`; downgrade drops both in reverse.
- [ ] **Step 5: Run** `uv run pytest tests/test_roles.py -v`. Expected: 4 PASS.
- [ ] **Step 6: Verify the migration** against the Compose Postgres:

```bash
cp -n .env.example .env
docker compose up -d postgres
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
uv run alembic check
```

Expected: no errors; `alembic check` prints `No new upgrade operations detected.` If it reports drift that predates this task (from `0001`), note it in the task report rather than fixing it here.

- [ ] **Step 7: Run the gate**, then commit:

```bash
git add core/models/user.py core/models/__init__.py core/schemas/user.py alembic/versions/0002_user_role.py tests/test_roles.py
git commit -m "feat: user roles (student/teacher/admin) with migration"
```

---

### Task 3: Users service + `require_role` dependency

**Files:**
- Create: `core/services/users.py`
- Modify: `api/deps.py`
- Test: `tests/test_users_service.py`, `tests/test_deps_roles.py`

**Interfaces:**
- Consumes: `Role`, `Plan`, `User` (Task 2); `NotFoundError`, `ForbiddenError` (`core/exceptions.py`); `CurrentUser` (`api/deps.py`).
- Produces (`core/services/users.py`):
  - `async def list_users(session: AsyncSession, *, q: str | None = None, role: Role | None = None, limit: int = 50, offset: int = 0) -> tuple[list[User], int]` — case-insensitive substring match on email with wildcards escaped (`User.email.icontains(q, autoescape=True)`); ordered by `created_at` desc then `id` desc; returns `(page, total_matching)`.
  - `async def update_user(session: AsyncSession, *, actor: User, user_id: uuid.UUID, role: Role | None = None, plan: Plan | None = None) -> User` — `NotFoundError` if missing; `ForbiddenError("admins cannot change their own role")` if `actor.id == user_id` and `role` is given and is not `Role.ADMIN`; commits and returns the refreshed user.
  - `async def set_role_by_email(session: AsyncSession, email: str, role: Role) -> User` — no actor check (trusted CLI path); `NotFoundError` if no such email; commits.
- Produces (`api/deps.py`):
  - `def require_role(*roles: Role) -> Callable[[User], Awaitable[User]]` — returned async dependency takes `user: CurrentUser`, returns it if `user.role in roles`, else raises `ForbiddenError("insufficient role")`.
  - `AdminUser = Annotated[User, Depends(require_role(Role.ADMIN))]`
  - `TeacherUser = Annotated[User, Depends(require_role(Role.TEACHER, Role.ADMIN))]` (used by the human-grading plan)

- [ ] **Step 1: Write the failing service tests** in `tests/test_users_service.py` (create users with `auth.register` + unique emails; set roles directly on the row and commit):

```python
async def test_update_user_changes_role_and_plan(db_session): ...
    # admin updates a student → role "teacher", plan "paid"; both persisted
async def test_update_user_missing_raises_not_found(db_session): ...
    # update_user(..., user_id=uuid7()) → NotFoundError
async def test_admin_cannot_demote_self(db_session): ...
    # update_user(actor=admin, user_id=admin.id, role=Role.TEACHER) → ForbiddenError; refreshed role == "admin"
async def test_admin_can_change_own_plan(db_session): ...
    # update_user(actor=admin, user_id=admin.id, plan=Plan.PAID) → plan == "paid", role still "admin"
async def test_list_users_filters_by_query_and_role(db_session): ...
    # 2 users with emails containing a unique token, one promoted to teacher;
    # list_users(q=token) → total 2; list_users(q=token, role=Role.TEACHER) → only the teacher
async def test_list_users_search_is_literal(db_session): ...
    # users f"{token}_a@t.com" and f"{token}xa@t.com"; list_users(q=f"{token}_") → only the first; q="%" + token → total 0
async def test_set_role_by_email(db_session): ...
    # → role "admin"; unknown email → NotFoundError
```

- [ ] **Step 2: Write the failing dependency tests** in `tests/test_deps_roles.py` (pure unit tests — call the returned dependency directly with an unsaved `User`):

```python
async def test_require_role_allows_listed_role() -> None:
    user = User(email="a@t.com", password_hash="x", role="teacher")
    assert await require_role(Role.TEACHER, Role.ADMIN)(user) is user

async def test_require_role_rejects_other_role() -> None:
    user = User(email="b@t.com", password_hash="x", role="student")
    with pytest.raises(ForbiddenError):
        await require_role(Role.ADMIN)(user)
```

- [ ] **Step 3: Run** `uv run pytest tests/test_users_service.py tests/test_deps_roles.py -v`. Expected: FAIL (`ImportError`).
- [ ] **Step 4: Implement** `core/services/users.py` and the `api/deps.py` additions per *Interfaces*.
- [ ] **Step 5: Run** the two test files. Expected: all PASS.
- [ ] **Step 6: Run the gate**, then commit:

```bash
git add core/services/users.py api/deps.py tests/test_users_service.py tests/test_deps_roles.py
git commit -m "feat: users service and require_role dependency"
```

---

### Task 4: Admin users API

**Files:**
- Create: `api/routers/admin.py`
- Modify: `api/main.py` (include router; add `{"name": "admin", "description": "Admin-only: users, results, grading oversight."}` to `OPENAPI_TAGS`)
- Modify: `core/schemas/user.py`
- Modify: `tests/conftest.py` (add `make_user` fixture)
- Modify: `README.md` (API table rows; one line on roles)
- Test: `tests/test_admin_router.py`

**Interfaces:**
- Consumes: `list_users`, `update_user` (Task 3); `AdminUser`, `DbSession` (`api/deps.py`); `Role`, `Plan` (Task 2).
- Produces:
  - `UserListResponse(BaseModel)`: `items: list[UserRead]`, `total: int`
  - `UserUpdateRequest(BaseModel)`: `role: Role | None = None`, `plan: Plan | None = None`; a `model_validator(mode="after")` raising `ValueError("nothing to update")` when both are `None` (→ 422)
  - `GET /admin/users` — query `q: str | None` (max_length 320), `role: Role | None`, `limit: int = 50` (1–100), `offset: int = 0` (≥0) → `UserListResponse`; `operation_id="admin_list_users"`
  - `PATCH /admin/users/{user_id}` — body `UserUpdateRequest` → `UserRead`; `operation_id="admin_update_user"`
  - Fixture `make_user` in `tests/conftest.py`: `async def _make(role: Role = Role.STUDENT) -> tuple[User, str]` — registers a unique-email user through `auth.register` on a session from `_engine`, sets `role`, commits, returns `(user, create_access_token(str(user.id)))`.

- [ ] **Step 1: Write the failing tests** in `tests/test_admin_router.py` (`h = {"Authorization": f"Bearer {token}"}`):

```python
async def test_admin_lists_users(app_client, make_user):
    admin, tok = await make_user(Role.ADMIN)
    target, _ = await make_user()
    resp = await app_client.get("/admin/users", params={"q": target.email}, headers=h(tok))
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["email"] == target.email

async def test_non_admin_forbidden(app_client, make_user):
    for role in (Role.STUDENT, Role.TEACHER):
        _, tok = await make_user(role)
        assert (await app_client.get("/admin/users", headers=h(tok))).status_code == 403

async def test_admin_users_requires_auth(app_client):
    assert (await app_client.get("/admin/users")).status_code == 401

async def test_admin_promotes_user(app_client, make_user):
    _, tok = await make_user(Role.ADMIN)
    target, _ = await make_user()
    resp = await app_client.patch(f"/admin/users/{target.id}", json={"role": "teacher"}, headers=h(tok))
    assert resp.status_code == 200
    assert resp.json()["role"] == "teacher"

async def test_role_change_applies_to_existing_token(app_client, make_user):
    _, admin_tok = await make_user(Role.ADMIN)
    user, user_tok = await make_user()
    assert (await app_client.get("/admin/users", headers=h(user_tok))).status_code == 403
    await app_client.patch(f"/admin/users/{user.id}", json={"role": "admin"}, headers=h(admin_tok))
    assert (await app_client.get("/admin/users", headers=h(user_tok))).status_code == 200
    await app_client.patch(f"/admin/users/{user.id}", json={"role": "student"}, headers=h(admin_tok))
    assert (await app_client.get("/admin/users", headers=h(user_tok))).status_code == 403

async def test_admin_cannot_demote_self_via_api(app_client, make_user):
    admin, tok = await make_user(Role.ADMIN)
    resp = await app_client.patch(f"/admin/users/{admin.id}", json={"role": "student"}, headers=h(tok))
    assert resp.status_code == 403

async def test_update_user_invalid_role_422(app_client, make_user):
    _, tok = await make_user(Role.ADMIN)
    target, _ = await make_user()
    resp = await app_client.patch(f"/admin/users/{target.id}", json={"role": "superuser"}, headers=h(tok))
    assert resp.status_code == 422

async def test_update_user_empty_body_422(app_client, make_user): ...   # json={} → 422
async def test_update_unknown_user_404(app_client, make_user): ...      # random uuid7 → 404
async def test_list_users_limit_over_100_422(app_client, make_user): ... # limit=101 → 422
```

- [ ] **Step 2: Run** `uv run pytest tests/test_admin_router.py -v`. Expected: FAIL (404s / missing fixture).
- [ ] **Step 3: Implement** the schemas, the `make_user` fixture, `api/routers/admin.py` (prefix `/admin`, tags `["admin"]`) and the `api/main.py` wiring per *Interfaces*.
- [ ] **Step 4: Run** `uv run pytest tests/test_admin_router.py -v`. Expected: all PASS.
- [ ] **Step 5: Update `README.md`** — add the two `/admin/users` rows to the API table, mention that `UserRead` now includes `role`, and add one line under API: roles are `student` (default at sign-up), `teacher`, `admin`; only admins change roles.
- [ ] **Step 6: Run the gate**, then commit:

```bash
git add api/routers/admin.py api/main.py core/schemas/user.py tests/conftest.py tests/test_admin_router.py README.md
git commit -m "feat: admin users API (list, change role/plan)"
```

---

### Task 5: First-admin bootstrap CLI

**Files:**
- Create: `core/cli.py`
- Modify: `README.md` (Getting started: create the first admin; Commands table row)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `set_role_by_email` (Task 3); `async_session_factory` (`core/db.py`); `Role` (Task 2).
- Produces (`core/cli.py`):
  - `def build_parser() -> argparse.ArgumentParser` — one subcommand `set-role` with positional `email` and `role` (`choices=[r.value for r in Role]`).
  - `async def run(argv: Sequence[str], session_factory: async_sessionmaker[AsyncSession] = async_session_factory) -> int` — on success writes `set role of <email> to <role>` to stdout and returns 0; unknown email writes `no user with email <email>` to stderr and returns 1.
  - `def main() -> None` — `raise SystemExit(asyncio.run(run(sys.argv[1:])))`; module ends with `if __name__ == "__main__": main()` so `python -m core.cli` works.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli.py` (build `factory = async_sessionmaker(_engine, expire_on_commit=False)`; create the user with `auth.register` in a session from it):

```python
async def test_cli_sets_role(_engine, capsys):
    # register user → await run(["set-role", email, "admin"], factory) == 0
    # stdout contains f"set role of {email} to admin"; reloaded user.role == "admin"
async def test_cli_unknown_email(_engine, capsys):
    # await run(["set-role", f"nobody-{uuid7()}@t.com", "admin"], factory) == 1; stderr contains "no user with email"
def test_cli_rejects_unknown_role():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["set-role", "a@t.com", "superuser"])
    assert exc.value.code == 2
```

- [ ] **Step 2: Run** `uv run pytest tests/test_cli.py -v`. Expected: FAIL (`ModuleNotFoundError: core.cli`).
- [ ] **Step 3: Implement** `core/cli.py` per *Interfaces* (argparse; stdout/stderr via `print(..., file=...)` is the CLI's user-facing output, not debug logging).
- [ ] **Step 4: Run** `uv run pytest tests/test_cli.py -v`. Expected: 3 PASS.
- [ ] **Step 5: Smoke-test against Compose Postgres:** with `uv run uvicorn api.main:app` running, register a user via `POST /auth/register`, then `uv run python -m core.cli set-role <that email> admin`. Expected: prints `set role of … to admin`, exit 0; `GET /auth/me` with that user's token shows `"role": "admin"`.
- [ ] **Step 6: Update `README.md`** — Getting started: after migrations, "create your first admin: register, then `uv run python -m core.cli set-role <email> admin`"; add the command to the Commands table.
- [ ] **Step 7: Run the gate**, then commit:

```bash
git add core/cli.py tests/test_cli.py README.md
git commit -m "feat: CLI to set a user's role (first-admin bootstrap)"
```

---

### Task 6: Band arithmetic (British Council rounding)

**Files:**
- Create: `core/services/bands.py`
- Test: `tests/test_bands.py`

**Interfaces:**
- Produces (`core/services/bands.py`, all `Decimal` in/out, results quantized to one decimal place, e.g. `Decimal("7.0")`):
  - `def validate_criterion_band(value: Decimal) -> Decimal` — returns value if `0 ≤ value ≤ 9` and a multiple of `0.5`, else `ValueError`.
  - `def round_band(value: Decimal) -> Decimal` — `floor(value × 2 + 0.5) / 2`.
  - `def task_band(criteria: Sequence[Decimal]) -> Decimal` — validates exactly 4 criteria; returns `round_band(mean)` (for display).
  - `def speaking_overall(criteria: Sequence[Decimal]) -> Decimal` — exactly 4 criteria (order: FC, LR, GRA, P); `round_band(mean)`.
  - `def writing_overall(task1: Sequence[Decimal], task2: Sequence[Decimal]) -> Decimal` — 4 criteria each; `round_band((mean(task1) + 2 × mean(task2)) / 3)` with **unrounded** means.
  - Any criteria list whose length is not 4 → `ValueError`.

- [ ] **Step 1: Write the failing tests** in `tests/test_bands.py` (use `D = Decimal`):

```python
@pytest.mark.parametrize(("raw", "expected"), [
    ("6.125", "6.0"), ("6.25", "6.5"), ("6.375", "6.5"), ("6.625", "6.5"),
    ("6.75", "7.0"), ("6.875", "7.0"), ("6.0", "6.0"), ("0", "0.0"), ("9", "9.0"),
])
def test_round_band(raw, expected):
    assert str(round_band(D(raw))) == expected

@pytest.mark.parametrize("bad", ["9.5", "-0.5", "6.25", "10"])
def test_validate_criterion_band_rejects(bad):
    with pytest.raises(ValueError):
        validate_criterion_band(D(bad))

def test_validate_criterion_band_accepts_half():
    assert validate_criterion_band(D("6.5")) == D("6.5")

def test_speaking_overall():
    assert speaking_overall([D("6"), D("6.5"), D("7"), D("7")]) == D("6.5")   # mean 6.625

def test_writing_overall_weights_task2_double():
    assert writing_overall([D("6")] * 4, [D("7")] * 4) == D("6.5")          # (6 + 14) / 3 = 6.67

def test_writing_overall_uses_unrounded_task_means():
    t1 = [D("5")] * 4
    t2 = [D("5"), D("5"), D("5.5"), D("5.5")]                                  # mean 5.25 → shown as 5.5
    assert task_band(t2) == D("5.5")
    assert writing_overall(t1, t2) == D("5.0")                                 # (5 + 10.5) / 3 = 5.17; rounding T2 first would give 5.5

def test_wrong_criteria_count():
    with pytest.raises(ValueError):
        speaking_overall([D("6")] * 3)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_bands.py -v`. Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `core/services/bands.py` per *Interfaces* (`Decimal.to_integral_value(rounding=ROUND_FLOOR)`, then `.quantize(Decimal("0.1"))`).
- [ ] **Step 4: Run** `uv run pytest tests/test_bands.py -v`. Expected: all PASS.
- [ ] **Step 5: Run the gate**, then commit:

```bash
git add core/services/bands.py tests/test_bands.py
git commit -m "feat: IELTS band arithmetic with British Council rounding"
```

---

## Not in this plan (next plan: Human grading)

The rest of the design — `HumanGrade` model, `test_sessions.grading_method`, the shared queue, atomic claim, drafts, submit, release/reassign, `TEACHER_MAX_ACTIVE_CLAIMS` / `STALE_CLAIM_HOURS` / `AI_FAILURE_FALLBACK_TO_TEACHER` settings, the Writing `request_manual_grading` toggle, the AI-failure fallback, the student result endpoint, admin results and overview (spec §4, §5, §7 except *Users*, §8, §9) — is a separate plan. It needs these phases built first: **Test content & sessions**, **Media** (Speaking recordings in R2), **Entitlements**, and the **AI pipeline** for Writing. It will reuse `TeacherUser`, `AdminUser` and `core/services/bands.py` from this plan.

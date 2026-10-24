# Tests & Sessions (Epic A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Versioned single-skill IELTS tests loaded from JSON files, and student sessions (practice/exam) with autosave, server-enforced deadlines, auto-submit and Writing/Speaking daily limits checked at start.

**Architecture:** Test content lives in `test_versions` as JSONB documents validated by strict Pydantic schemas: `content` (student-visible), `answer_key`, `staff_content` (Listening transcripts). One module knows the content shape (`core/services/content.py`: item index, asset keys, problems list). Sessions are rows tied to one immutable version; submission is a single conditional UPDATE so it happens exactly once; an in-process loop started by the app lifespan auto-submits expired exam sessions.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2, aioboto3 (R2 signing/upload), pytest + testcontainers Postgres.

**Spec:** `docs/superpowers/specs/2026-10-24-tests-and-sessions-design.md`

## Global Constraints

- Python 3.11; `ruff check .`, `ruff format --check .`, `mypy .` (strict) must pass at every commit.
- UUIDv7 PKs and `TimestampMixin` on every new table; all datetimes `TIMESTAMPTZ` UTC, never naive.
- Schema changes only via Alembic (`0003`); `create_all` only in tests.
- Services raise `AppError` subclasses, never `HTTPException`; routers are thin.
- Never return ORM objects from endpoints; never put `answer_key` or `staff_content` in a student-facing schema.
- Integration tests against real Postgres; mock only R2 (inject a fake uploader; URL signing needs no network).
- Tests create unique data (`uuid7()` in slugs/emails) and never assert global counts — the test DB is shared across the session.
- Settings only through `core/settings.py`; new settings get defaults so `.env` stays valid.
- Skills: `reading | listening | writing | speaking`. Counts: Reading 3 passages / questions 1–40, Listening 4 parts / 1–40, Writing tasks 1–2, Speaking 3 parts.
- Exam time limits (minutes): reading 60, listening 32, writing 60, speaking 20; grace 30 s.
- New OpenAPI tags `tests`, `sessions`; every route has a stable `operation_id`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. An answer key or Listening transcript reaching a student in any response (test detail, session start, session read, after submit) — must never happen.
2. Student submit racing auto-submit (or two submits) — the session is submitted once and `on_session_submitted` runs once.
3. A save at the deadline boundary — accepted within grace, 409 after it, and the late save leaves the session submitted (not rolled back).
4. Two simultaneous Writing starts competing for the last daily slot — exactly one succeeds.
5. Publishing a new version while a session runs on the old one — the running session keeps the old content; new starts get the new version.

Each has a named test in the task that owns the code: 1 → Task 9, 2 and 3 → Task 8, 4 → Task 5, 5 → Task 6.

---

### Task 1: Content schemas and the problems list

**Files:**
- Create: `core/models/enums.py` (only `Skill` here; Task 2 adds the rest)
- Create: `core/schemas/content.py`
- Create: `core/services/content.py`
- Create: `tests/content_factories.py` (valid content + answer-key dicts per skill, reused by later tasks)
- Test: `tests/test_content.py`

**Interfaces:**
- Produces:
  - `Skill(StrEnum)`: `READING="reading"`, `LISTENING="listening"`, `WRITING="writing"`, `SPEAKING="speaking"`.
  - Pydantic models (all `extra="forbid"`): `Option(key, text)`, `QuestionItem(numbers: list[int] (min 1), prompt: str | None, options: list[Option] | None)`, `QuestionGroup(type: Literal["multiple_choice","fill_blank","matching","short_answer"], variant: str | None, instructions: str, title: str | None, context: str | None, word_limit: str | None, options: list[Option] | None, questions: list[QuestionItem])`, `Paragraph(label: str | None, text)`, `Passage(number, title, paragraphs, question_groups)`, `ReadingContent(passages)`, `ListeningPart(number, title, audio: str | None, question_groups)`, `ListeningContent(parts)`, `WritingTask(number, prompt, min_words, image: str | None)`, `WritingContent(tasks)`, `SpeakingQuestion(number, text: str | None, audio: str | None)`, `SpeakingTopic(topic, questions)`, `SpeakingPart1(topics)`, `CueCard(task, you_should_say: list[str], and_explain: str | None)`, `SpeakingPart2(number, cue_card, audio: str | None, rounding_off: list[SpeakingQuestion])`, `SpeakingPart3(theme: str | None, questions)`, `SpeakingContent(part1: SpeakingPart1 | None, part2: SpeakingPart2 | None, part3: SpeakingPart3 | None)`, `TranscriptLine(speaker, text)`, `StaffContent(transcripts: dict[int, list[TranscriptLine]] = {})`.
  - `Content = ReadingContent | ListeningContent | WritingContent | SpeakingContent`; `AnswerKey = dict[int, list[str]]`.
  - `parse_content(skill: Skill, data: dict[str, Any]) -> Content` (raises `pydantic.ValidationError`).
  - `Problem(BaseModel)`: `code: str`, `message: str`, `severity: Literal["error","warning"]`, `section: int | None`, `question: int | None`.
  - `ItemSpec` (frozen dataclass): `key: int`, `numbers: tuple[int, ...]`, `kind: Literal["choice","single_choice","text","essay","recording"]`, `option_keys: frozenset[str]`, `max_select: int`.
  - `item_index(skill: Skill, content: Content) -> dict[int, ItemSpec]` — keyed by item key (first number). `multiple_choice` → `choice` with `max_select=len(numbers)`, options from the item or else the group; `matching` → `single_choice` with the group's options; `fill_blank`/`short_answer` → `text`; Writing tasks → `essay`; Speaking questions (Part 1/3 questions, Part 2 long turn by `part2.number`, rounding-off) → `recording`.
  - `asset_keys(skill: Skill, content: Content) -> list[str]` — every non-null `audio`/`image` value.
  - `validate_version(skill: Skill, content: Content, answer_key: AnswerKey) -> list[Problem]`.

**Problem codes (all `error`):** `wrong_section_count` (passages≠3, parts≠4, task numbers≠{1,2}, any Speaking part `None`), `bad_question_numbers` (Reading/Listening numbers across all items not exactly 1–40 in order without duplicates; Speaking numbers not 1…N in order across parts), `missing_answer` (Reading/Listening item key absent from `answer_key` or empty list), `wrong_answer_count` (`choice` item whose key list length ≠ `max_select`; `single_choice` item whose list length ≠ 1), `answer_not_an_option` (choice/single_choice answer not in `option_keys`), `unknown_answer_key` (answer-key number that is not an item key, or any key for Writing/Speaking), `missing_audio` (Listening part with `audio=None`), `missing_image` (Writing Task 1 `image=None`), `speaking_question_empty` (Speaking question or Part 2 with neither text — cue card task for Part 2 — nor audio), `missing_cue_card` (Part 2 `cue_card.task` blank). `text` items may list several accepted answers. No warnings are required.

- [ ] **Step 1: Write the failing tests** in `tests/test_content.py`. Put small dict factories in `tests/content_factories.py` (`reading_content()`, `listening_content()`, `writing_content()`, `speaking_content()`, matching `*_key()` functions, `valid_cases()`), all valid — Listening parts and Writing Task 1 carry asset keys, Listening has a choose-two item 21–22, a matching group and an item 11 multiple choice; each test mutates one thing:

```python
def test_valid_versions_have_no_problems() -> None:
    for skill, content, key in valid_cases():
        assert validate_version(skill, parse_content(skill, content), key) == []

def test_reading_needs_three_passages() -> None:
    c = reading_content(); c["passages"].pop()
    codes = {p.code for p in validate_version(Skill.READING, parse_content(Skill.READING, c), reading_key())}
    assert "wrong_section_count" in codes

def test_question_numbers_must_be_1_to_40() -> None: ...       # duplicate 7 → bad_question_numbers
def test_every_question_needs_an_answer() -> None: ...          # del key[12] → missing_answer, question=12
def test_choose_two_item_keyed_by_first_number() -> None:
    idx = item_index(Skill.LISTENING, parse_content(Skill.LISTENING, listening_content()))
    assert idx[21].numbers == (21, 22) and idx[21].max_select == 2 and 22 not in idx
def test_choose_two_key_needs_two_letters() -> None: ...        # key[21]=["B"] → wrong_answer_count
def test_answer_must_be_an_option() -> None: ...                # key[11]=["Z"] → answer_not_an_option
def test_unknown_answer_key() -> None: ...                      # key[22]=["D"] → unknown_answer_key
def test_listening_part_needs_audio() -> None: ...              # parts[0]["audio"]=None → missing_audio, section=1
def test_writing_task1_needs_image() -> None: ...               # → missing_image
def test_speaking_question_needs_text_or_audio() -> None: ...   # text=None, audio=None → speaking_question_empty
def test_speaking_missing_part() -> None: ...                   # part3=None → wrong_section_count
def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_content(Skill.WRITING, {**writing_content(), "answers": {}})
def test_asset_keys_lists_audio_and_images() -> None: ...
```

- [ ] **Step 2: Run** `uv run pytest tests/test_content.py -q`. Expected: FAIL (ImportError: `core.schemas.content`).
- [ ] **Step 3: Implement** `core/models/enums.py`, `core/schemas/content.py`, `core/services/content.py` with the interfaces above. `parse_content` dispatches on skill via a `dict[Skill, type[BaseModel]]`. Problems carry `section` (passage/part/task number) and `question` (item key) where they apply.
- [ ] **Step 4: Run** `uv run pytest tests/test_content.py -q && uv run mypy . && uv run ruff check .`. Expected: all pass.
- [ ] **Step 5: Commit** (include the spec, which is still untracked):

```bash
git add core/models/enums.py core/schemas/content.py core/services/content.py tests/test_content.py tests/content_factories.py docs/superpowers/specs/2026-10-24-tests-and-sessions-design.md docs/superpowers/plans/2026-10-24-tests-and-sessions.md
git commit -m "feat: test content schemas and problems list"
```

---

### Task 2: Models and migration

**Files:**
- Modify: `core/models/enums.py`, `core/models/__init__.py`
- Create: `core/models/test.py` (`Test`, `TestVersion`), `core/models/session.py` (`TestSession`, `Answer`), `core/models/daily_usage.py` (`DailyUsage`)
- Create: `alembic/versions/0003_tests_and_sessions.py`
- Test: `tests/test_test_models.py`

**Interfaces:**
- Produces enums (`StrEnum`): `VersionStatus{DRAFT,PUBLISHED,RETIRED}`, `SessionMode{PRACTICE,EXAM}`, `SessionStatus{IN_PROGRESS,SUBMITTED}`, `SubmitReason{STUDENT,TIMEOUT}`, `GradingMethod{AUTO,AI,HUMAN}` (lowercase values).
- `Test`: `skill: str` (check on 4 values), `slug: str` (String(100), unique), `title: str` (String(200)), `current_version_id: uuid | None` (FK `test_versions.id`, `use_alter=True`, `ondelete="SET NULL"`).
- `TestVersion`: `test_id` FK (cascade), `version: int`, `status: str` (check, default draft), `content: dict` (JSONB), `answer_key: dict` (JSONB, default `{}`), `staff_content: dict` (JSONB, default `{}`), `published_at: datetime | None`. `UniqueConstraint(test_id, version)`; partial unique index `uq_test_versions_one_draft` on `(test_id)` `WHERE status = 'draft'`.
- `TestSession` (`__tablename__="test_sessions"`): `user_id` FK users (cascade, index), `test_version_id` FK, `skill`, `mode`, `status` (default in_progress), `started_at`, `deadline: datetime | None`, `submitted_at | None`, `submit_reason: str | None`, `request_manual_grading: bool = False`, `grading_method: str | None`; check constraints on each enum column; indexes `(status, deadline)` and `(user_id, created_at)`.
- `Answer`: `test_session_id` FK (cascade), `question_number: int`, `value` JSONB; `UniqueConstraint(test_session_id, question_number)`.
- `DailyUsage` (`daily_usage`): composite PK `(user_id, skill, day: date)`, `count: int`; no UUID mixin (*ruling: the spec's natural key is the PK*); `updated_at` via `TimestampMixin`.

- [ ] **Step 1: Write failing tests** in `tests/test_test_models.py` (use `db_session`, commit with unique slugs):
  - `test_second_draft_for_same_test_rejected` — two `TestVersion(status="draft")` for one test → `IntegrityError` on flush.
  - `test_draft_plus_published_allowed` — one draft + one published for one test flushes fine.
  - `test_version_numbers_unique_per_test` — same `(test_id, version)` → `IntegrityError`.
  - `test_session_rejects_unknown_mode` — `mode="sprint"` → `IntegrityError`.
  - `test_answer_unique_per_question` — two answers, same session and number → `IntegrityError`.
- [ ] **Step 2: Run** `uv run pytest tests/test_test_models.py -q`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement** the models and export them (plus enums) from `core/models/__init__.py`.
- [ ] **Step 4: Run** `uv run pytest tests/test_test_models.py -q`. Expected: PASS.
- [ ] **Step 5: Write migration 0003** (`down_revision="0002"`) creating the five tables, constraints (use `op.f()` names matching `NAMING_CONVENTION`), the partial unique index and the deferred FK `tests.current_version_id`. Downgrade drops in reverse.
- [ ] **Step 6: Verify against Compose Postgres:** `docker compose up -d && uv run alembic upgrade head && uv run alembic downgrade 0002 && uv run alembic upgrade head && uv run alembic check`. Expected: no errors; `alembic check` prints "No new upgrade operations detected."
- [ ] **Step 7: Commit** `git add core/models alembic/versions/0003_tests_and_sessions.py tests/test_test_models.py && git commit -m "feat: test, version, session, answer and daily usage models"`

---

### Task 3: Storage and the test catalog

**Files:**
- Modify: `core/settings.py` (add `signed_url_ttl_seconds: int = 900`)
- Modify: `core/exceptions.py`
- Create: `core/services/storage.py`, `core/services/catalog.py`
- Test: `tests/test_storage.py`, `tests/test_catalog.py`

**Interfaces:**
- Consumes: Task 1 (`parse_content`, `validate_version`, `Skill`), Task 2 models.
- Produces:
  - `ContentProblemsError(ValidationAppError)`: `code="content_problems"`, `extra={"problems": [p.model_dump() ...]}`.
  - `Uploader = Callable[[str, Path, str], Awaitable[None]]` (key, local path, content type).
  - `storage.upload_file(key: str, path: Path, content_type: str) -> None` — aioboto3 `put_object` to `r2_bucket`.
  - `storage.signed_get_url(key: str) -> str` — aioboto3 `generate_presigned_url("get_object", ExpiresIn=signed_url_ttl_seconds)`.
  - `catalog.save_draft(db, *, slug: str, title: str, skill: Skill, content: Content, answer_key: AnswerKey, staff_content: StaffContent) -> TestVersion` — creates the test if the slug is new; replaces the existing draft's documents and title if one exists; else adds version `max+1` as draft. Skill of an existing slug can't change → `ConflictError`. Commits.
  - `catalog.publish(db, version_id: uuid) -> TestVersion` — `NotFoundError` if missing, `ConflictError` if not a draft, `ContentProblemsError` if `validate_version` has any `error`; otherwise sets `published`, `published_at`, retires the previous published version, points `tests.current_version_id` at it. Commits.
  - `catalog.list_published(db, *, skill: Skill | None, limit: int, offset: int) -> tuple[list[Test], int]` — tests with `current_version_id` set, newest first.
  - `catalog.get_published_test(db, test_id: uuid) -> Test` — `NotFoundError` if absent or unpublished.
  - `catalog.problems_for(version: TestVersion) -> list[Problem]`.

- [ ] **Step 1: Write failing tests.**
  - `tests/test_storage.py::test_signed_get_url_is_signed_for_the_key` — `url = await signed_get_url("tests/x/a.mp3")`; assert `"tests/x/a.mp3" in url` and `"X-Amz-Signature=" in url` (signing is local; no network).
  - `tests/test_catalog.py` (content from `tests/content_factories.py`):
    - `test_first_save_creates_version_1_draft`
    - `test_resave_replaces_the_draft` — version stays 1, title updated.
    - `test_save_after_publish_creates_version_2`
    - `test_publish_rejects_version_with_problems` — Listening with `audio=None` → `ContentProblemsError`, `extra["problems"][0]["code"] == "missing_audio"`, status still draft.
    - `test_publish_retires_previous_version` — publish v1, save + publish v2 → v1 `retired`, `current_version_id == v2.id`.
    - `test_unpublished_test_not_listed_or_fetchable` — `get_published_test` → `NotFoundError`; not in `list_published(skill=...)` filtered by its id.
    - `test_skill_change_for_slug_rejected` → `ConflictError`.
- [ ] **Step 2: Run** `uv run pytest tests/test_storage.py tests/test_catalog.py -q`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement.** If mypy reports missing stubs for `aioboto3`, add it to the `ignore_missing_imports` override in `pyproject.toml`.
- [ ] **Step 4: Run** the same tests plus `uv run mypy .`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: test catalog (drafts, publish, versions) and R2 storage helpers"` with the files above.

---

### Task 4: CLI `load-test` and per-skill seed files

**Files:**
- Modify: `core/cli.py`
- Create: `core/services/test_loader.py`
- Create: `seed/tests/practice-01-reading.json`, `practice-01-listening.json`, `practice-01-writing.json`, `practice-01-speaking.json`, `seed/tests/assets/practice-01-writing-task1.svg`
- Delete: `seed/tests/academic-practice-test-01.json`
- Test: `tests/test_test_loader.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 3 `save_draft`, `publish`, `problems_for`, `Uploader`, `storage.upload_file`.
- Produces:
  - `TestFile(BaseModel, extra="forbid")`: `slug` (pattern `^[a-z0-9-]+$`), `title`, `skill: Skill`, `content: dict[str, Any]`, `answer_key: dict[int, list[str]] = {}`, `staff_content: dict[str, Any] = {}`, `assets: dict[str, str] = {}` (R2 key → path relative to the file).
  - `LoadResult` (dataclass): `version: TestVersion`, `problems: list[Problem]`, `published: bool`.
  - `load_test_file(db, path: Path, *, publish: bool, upload: Uploader = storage.upload_file) -> LoadResult` — parse file → `parse_content` → every key from `asset_keys` must be in `assets` (else `ValidationAppError("asset <key> has no file")`) and each file must exist → upload each asset (content type from `mimetypes`) → `save_draft` → if `publish`, `catalog.publish`; a `ContentProblemsError` from publish is caught and returned as `published=False` with the problems.
  - CLI: `load-test <path> [--publish]`. Prints `loaded <slug> v<N> (draft|published)` then one line per problem `<severity> <code> section=<s> question=<q>: <message>`. Exit 0 if loaded (and published when asked); exit 1 if `--publish` was given and problems blocked it, or the file is invalid (message on stderr).

**Seed split** (do it with a throwaway script in the scratchpad, not committed): from the current combined file make four files using the new shape — slug `practice-01-<skill>`, title `Practice Test 1 — <Skill>`; items' `number` → `numbers: [n]` (keep `numbers` for 21–22); each item's `answers` moves into `answer_key`; group `select` dropped (implied by `numbers`); `time_limit_minutes`, `suggested_minutes`, `duration_minutes`, `prep_seconds`, `speak_seconds`, `module`, `version`, `source`, `notes` dropped; Listening `sections` → `parts` with `audio: null`, transcripts → `staff_content.transcripts`; Writing Task 1 `visual` → `image: "tests/practice-01-writing/task1.svg"` with `assets` mapping it to `assets/practice-01-writing-task1.svg`; Speaking questions become `{number, text, audio: null}` numbered 1…N across parts (Part 2 long turn gets its own number, rounding-off question the next). Hand-write the SVG as a grouped bar chart of the existing series (Car 62/54/41, Bus 18/20/22, Bicycle 5/9/17, Walking 15/17/20 for 2000/2010/2020) with title, axes and legend.

- [ ] **Step 1: Write failing tests** in `tests/test_test_loader.py` (write files to `tmp_path`; fake uploader records calls; unique slug per test):
  - `test_load_publishes_valid_reading` — `published is True`, version status published.
  - `test_load_uploads_assets_to_their_keys` — Writing file with one asset → uploader called once with that key and the resolved path.
  - `test_missing_asset_file_rejected` → `ValidationAppError`.
  - `test_load_with_problems_stays_draft` — Listening with `audio: null`, `publish=True` → `published is False`, `problems[0].code == "missing_audio"`.
  - `test_seed_files_load` — loop over the four committed seed files with the fake uploader and `publish=True`, but rewrite each `slug` to a unique one first: reading, writing and speaking publish with no problems; listening returns only `missing_audio` problems (4 of them).
  - In `tests/test_cli.py`: `test_cli_load_test_prints_problems` — Listening seed copy with unique slug, `run(["load-test", path, "--publish"], factory)` returns 1 and stdout contains `missing_audio`. Inject the fake uploader by monkeypatching `core.services.storage.upload_file`.
- [ ] **Step 2: Run** `uv run pytest tests/test_test_loader.py tests/test_cli.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the loader and CLI subcommand; write the seed files and SVG; delete the combined file.
- [ ] **Step 4: Run** the tests. Expected: PASS. Then by hand against Compose: `uv run python -m core.cli load-test seed/tests/practice-01-reading.json --publish` → `loaded practice-01-reading v1 (published)` (MinIO must have the bucket; if the command fails only on upload, note it in the ledger — Reading has no assets).
- [ ] **Step 5: Commit** `git add -A seed core/cli.py core/services/test_loader.py tests && git commit -m "feat: load-test CLI and per-skill practice test seed files"`

---

### Task 5: Daily limits

**Files:**
- Modify: `core/exceptions.py`
- Create: `core/services/entitlements.py`
- Test: `tests/test_entitlements.py`

**Interfaces:**
- Produces:
  - `QuotaExceededError(AppError)`: `status_code=429`, `code="quota_exceeded"`, `extra={"resets_at": <ISO-8601 UTC>}`.
  - `daily_limit(user: User, skill: Skill) -> int` — from `free_/paid_{writing,speaking}_daily`; `ValueError` for reading/listening.
  - `check_and_consume(db, user: User, skill: Skill, now: datetime) -> None` — one `INSERT … ON CONFLICT (user_id, skill, day) DO UPDATE SET count = count + 1 WHERE daily_usage.count < :limit RETURNING count`; no row → `QuotaExceededError` with `resets_at` = next UTC midnight after `now`. A limit ≤ 0 raises before the insert. Does **not** commit (runs inside the caller's transaction).

- [ ] **Step 1: Write failing tests:**
  - `test_free_user_gets_three_writing_per_day` — 3 calls (commit after each) pass, 4th raises; `exc.extra["resets_at"] == "<next day>T00:00:00+00:00"`.
  - `test_pools_are_separate` — 3 writing used, speaking still passes.
  - `test_new_day_resets` — 3 used on day D, passes with `now` on D+1.
  - `test_paid_limit_is_30`.
  - `test_failed_call_consumes_nothing` — after the 4th raises and the caller rolls back, the stored count is 3.
  - `test_concurrent_starts_take_the_last_slot_once` (Review Focus 4) — use 2 slots first, then `asyncio.gather` two calls on two separate `AsyncSession`s from `async_sessionmaker(_engine)`, each committing; exactly one raises.
- [ ] **Step 2: Run** `uv run pytest tests/test_entitlements.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** with `sqlalchemy.dialects.postgresql.insert`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: daily Writing/Speaking limits with atomic consume"`.

---

### Task 6: Sessions — start, read, list

**Files:**
- Modify: `core/settings.py` (`reading_time_limit_minutes=60`, `listening_time_limit_minutes=32`, `writing_time_limit_minutes=60`, `speaking_time_limit_minutes=20`, `session_grace_seconds=30`)
- Create: `core/services/sessions.py`
- Test: `tests/test_sessions_start.py`

**Interfaces:**
- Consumes: Task 3 `get_published_test`, Task 5 `check_and_consume`.
- Produces (all take `now: datetime | None = None`, defaulting to `datetime.now(UTC)`):
  - `exam_time_limit(skill: Skill) -> timedelta`.
  - `start_session(db, user: User, test_id: uuid, mode: SessionMode, now=None) -> TestSession` — loads the test's current published version; Speaking → `ConflictError("speaking tests can't be started until recording uploads exist")`; Writing/Speaking → `check_and_consume` in the same transaction; exam → `deadline = now + exam_time_limit`; practice → `None`. Commits.
  - `get_session(db, user: User, session_id: uuid, now=None) -> TestSession` — `NotFoundError` if missing or another user's; submits it first if expired (Task 8 fills that in — in this task, no expiry handling).
  - `list_sessions(db, user: User, *, limit: int, offset: int) -> tuple[list[TestSession], int]` — newest first.
  - `get_version(db, ts: TestSession) -> TestVersion`, `get_answers(db, ts: TestSession) -> dict[int, Any]`.

- [ ] **Step 1: Write failing tests** (helper `published_test(skill)` publishes a unique-slug version via `catalog`):
  - `test_exam_session_has_deadline` — reading exam → `deadline - started_at == timedelta(minutes=60)`; listening → 32.
  - `test_practice_session_has_no_deadline`.
  - `test_writing_start_consumes_daily_limit` — 4th writing start for a free user raises `QuotaExceededError` and creates no session.
  - `test_practice_counts_toward_limit` — practice writing starts count too.
  - `test_reading_start_is_unlimited` — 5 reading starts pass.
  - `test_speaking_cannot_start_yet` → `ConflictError`.
  - `test_unpublished_test_cannot_start` → `NotFoundError`.
  - `test_session_pinned_to_version` (Review Focus 5) — start on v1, publish v2, `get_version(old)` is v1 and a new start uses v2.
  - `test_other_users_session_is_404`.
- [ ] **Step 2: Run** `uv run pytest tests/test_sessions_start.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: start, read and list test sessions"`.

---

### Task 7: Saving answers and the manual-grading toggle

**Files:**
- Modify: `core/exceptions.py`, `core/services/sessions.py`, `core/settings.py` (`max_essay_chars: int = 20000`, `max_text_answer_chars: int = 200`)
- Test: `tests/test_sessions_answers.py`

**Interfaces:**
- Consumes: Task 1 `item_index`.
- Produces:
  - `SessionClosedError(ConflictError)`: `code="session_closed"`.
  - `AnswerInput(BaseModel)`: `question: int`, `value: str | list[str] | None` (in `core/schemas/session.py`, created here).
  - `save_answers(db, user, session_id, answers: list[AnswerInput], now=None) -> dict[int, Any]` — locks the session row (`with_for_update`); closed session → `SessionClosedError`; validates **all** inputs before writing any (`ValidationAppError` with `extra={"question": n}`): unknown item key → 422; `choice` → list of distinct keys from `option_keys`, length 1…`max_select`; `single_choice` → one key from `option_keys` as a string; `text` → string ≤ `max_text_answer_chars`; `essay` → string ≤ `max_essay_chars`; `recording` → 422 (Media phase). `None` deletes the answer. Upserts; commits; returns all saved answers.
  - `set_manual_grading(db, user, session_id, value: bool, now=None) -> TestSession` — Writing only (`ValidationAppError` otherwise); closed → `SessionClosedError`; locks the row.

- [ ] **Step 1: Write failing tests** on practice-mode sessions over tests published from `tests/content_factories.py`:
  - `test_answers_are_upserted` — save 1 then save 1 again → latest value kept.
  - `test_null_clears_an_answer`.
  - `test_choose_two_saved_under_first_number` — `{"question": 21, "value": ["B","D"]}` ok; question 22 → 422; `["B","B"]` → 422; `["A","B","C"]` → 422.
  - `test_matching_takes_one_option_key` — `"C"` ok, `"Z"` → 422, `["C"]` → 422.
  - `test_text_and_essay_lengths` — over-long text → 422; essay of 1000 words ok.
  - `test_bad_input_writes_nothing` — batch with one valid and one invalid → 422 and the valid one isn't stored.
  - `test_save_to_submitted_session_is_409` (set status submitted directly).
  - `test_manual_grading_toggle_writing_only` — reading → 422; writing → stored true then false.
- [ ] **Step 2: Run** `uv run pytest tests/test_sessions_answers.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: autosave answers with per-question validation; manual grading toggle"`.

---

### Task 8: Submitting, deadlines and auto-submit

**Files:**
- Modify: `core/services/sessions.py`
- Test: `tests/test_sessions_submit.py`

**Interfaces:**
- Produces:
  - `on_session_submitted(db: AsyncSession, ts: TestSession) -> None` — module-level no-op hook; called **by name lookup** so later phases (and tests) can replace it; runs before the commit.
  - `submit_session(db, user, session_id, now=None) -> TestSession` — student submit; already submitted → `SessionClosedError`.
  - `auto_submit_expired(db, now=None) -> int` — submits every `in_progress` session with `deadline + grace < now` with reason `timeout`; returns how many.
  - Internal `_submit(db, session_ids, reason, now) -> list[TestSession]`: one `UPDATE test_sessions SET status='submitted', submitted_at=:now, submit_reason=:reason, grading_method=CASE … END WHERE id IN … AND status='in_progress' RETURNING …`; `grading_method` CASE: reading/listening → `auto`; speaking → `human`; writing → `human` if `request_manual_grading` else `ai`. Calls the hook per returned row, then commits.
  - Expiry in reads/saves: `get_session` submits an expired session (reason `timeout`) before returning it; `save_answers` and `set_manual_grading` on an expired session submit it, **commit**, then raise `SessionClosedError`. Practice sessions never expire.

- [ ] **Step 1: Write failing tests** (monkeypatch `core.services.sessions.on_session_submitted` with a counter):
  - `test_student_submit_sets_fields` — reading → `grading_method == "auto"`, `submit_reason == "student"`.
  - `test_writing_grading_method_follows_toggle` — toggle on → `human`; off → `ai`.
  - `test_double_submit_is_409_and_hook_runs_once`.
  - `test_submit_races_auto_submit` (Review Focus 2) — expired writing session; `asyncio.gather(submit_session(...), auto_submit_expired(...))` on two separate sessions from the factory; exactly one submission recorded, hook count 1 (the losing call raises `SessionClosedError` or returns 0).
  - `test_save_within_grace_accepted` (Review Focus 3) — `now = deadline + 20s` → saved.
  - `test_save_after_grace_submits_and_409` (Review Focus 3) — `now = deadline + 31s` → `SessionClosedError`; reloading in a fresh session shows `submitted`, reason `timeout`, earlier answers kept.
  - `test_auto_submit_skips_practice_and_unexpired` — practice session and an exam session with time left stay `in_progress`.
  - `test_get_session_submits_expired` — reading an expired exam session returns it `submitted`.
- [ ] **Step 2: Run** `uv run pytest tests/test_sessions_submit.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** this file and `tests/test_sessions_*.py`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: submit-once sessions, deadline grace and auto-submit"`.

---

### Task 9: HTTP API — `/tests` and `/sessions`

**Files:**
- Modify: `core/schemas/session.py`, `api/main.py` (tags + routers)
- Create: `core/schemas/test.py`, `api/routers/tests.py`, `api/routers/sessions.py`
- Modify: `core/services/sessions.py` — add `student_content(skill, content) -> dict[str, Any]` that returns a copy of `content` with every asset key replaced by `await storage.signed_get_url(key)` (async; walk via the parsed model, then `model_dump(mode="json")`)
- Test: `tests/test_tests_router.py`, `tests/test_sessions_router.py`

**Interfaces:**
- Produces schemas:
  - `TestSummary(id, skill, title, exam_time_limit_minutes: int)`; `TestListResponse(items, total)`.
  - `SessionStartRequest(test_id: uuid, mode: SessionMode)`; `SessionUpdateRequest(request_manual_grading: bool)`; `AnswersRequest(answers: list[AnswerInput])` (min 1, max 100 items); `AnswersResponse(answers: dict[int, Any])`.
  - `SessionRead(id, test_id, test_version_id, title, skill, mode, status, started_at, deadline, submitted_at, submit_reason, request_manual_grading, grading_method, server_time)`; `SessionDetail(SessionRead)` + `content: dict[str, Any]`, `answers: dict[int, Any]`; `SessionListResponse(items: list[SessionRead], total)`.
- Routes (tags `tests` / `sessions`, all require `CurrentUser`):

| Route | operation_id | Status |
|---|---|---|
| `GET /tests?skill=&limit=&offset=` | `list_tests` | 200 |
| `GET /tests/{test_id}` | `get_test` | 200 / 404 |
| `POST /sessions` | `start_session` | 201 `SessionDetail` / 404 / 409 / 429 |
| `GET /sessions?limit=&offset=` | `list_sessions` | 200 |
| `GET /sessions/{id}` | `get_session` | 200 `SessionDetail` / 404 |
| `PUT /sessions/{id}/answers` | `save_answers` | 200 / 409 / 422 |
| `PATCH /sessions/{id}` | `update_session` | 200 `SessionRead` / 409 / 422 |
| `POST /sessions/{id}/submit` | `submit_session` | 200 `SessionRead` / 409 |

- [ ] **Step 1: Write failing tests** (publish tests via `catalog`; monkeypatch `core.services.storage.signed_get_url` to return `f"https://signed/{key}"`):
  - `test_list_and_get_published_tests` — includes `exam_time_limit_minutes`; no `content` key in either response.
  - `test_draft_test_is_404`.
  - `test_start_returns_content_without_answer_key` (Review Focus 1) — for reading and listening (listening factory content includes transcripts in `staff_content`): helper `assert_no_secrets(body, version)` walks the JSON and asserts no key named `answer_key`/`staff_content`/`transcripts`, and no transcript line text appears anywhere in `json.dumps(body)`. Apply it to `POST /sessions`, `GET /sessions/{id}` before and after submit, and `GET /tests/{id}`.
  - `test_assets_are_signed_urls` — writing session content's Task 1 `image` starts with `https://signed/`.
  - `test_resume_returns_saved_answers` — save, then `GET /sessions/{id}` includes them.
  - `test_quota_exceeded_is_429_with_resets_at`.
  - `test_session_endpoints_require_auth` (401) and `test_other_users_session_404`.
  - `test_submit_then_save_is_409` with `error.code == "session_closed"`.
  - `test_openapi_has_tags_and_operation_ids` — `/openapi.json` lists tags `tests`, `sessions` and each operation_id above.
- [ ] **Step 2: Run** `uv run pytest tests/test_tests_router.py tests/test_sessions_router.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** routers (thin: parse → service → schema) and register them with the new tags in `OPENAPI_TAGS`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: /tests and /sessions endpoints"`.

---

### Task 10: Auto-submit loop in the app lifespan, and docs

**Files:**
- Modify: `core/settings.py` (`auto_submit_enabled: bool = True`, `auto_submit_interval_seconds: int = 60`), `core/services/sessions.py`, `api/main.py`, `tests/conftest.py` (`os.environ.setdefault("AUTO_SUBMIT_ENABLED", "false")`), `.env.example`
- Modify docs: `CLAUDE.md`, `README.md`, `docs/superpowers/specs/2026-06-23-backend-foundation-design.md`, `docs/superpowers/specs/2026-10-24-human-grading-design.md`, and the spec's status line
- Test: `tests/test_auto_submit_loop.py`

**Interfaces:**
- Produces: `run_auto_submit_loop(factory: async_sessionmaker[AsyncSession], interval_seconds: float) -> None` — forever: open a session, `auto_submit_expired`, log `auto_submitted` with the count when > 0, catch and `log.exception` any error (the loop must survive a DB blip), `asyncio.sleep(interval)`. `create_app()` gets a lifespan that starts it as a task when `auto_submit_enabled` and cancels it on shutdown.

- [ ] **Step 1: Write failing tests:**
  - `test_loop_submits_expired_sessions` — create an expired exam session; start `run_auto_submit_loop(factory, 0.05)` as a task; poll up to 2 s until the session is `submitted`; cancel the task.
  - `test_loop_survives_errors` — monkeypatch `auto_submit_expired` to raise once then work; the session still gets submitted.
  - `test_lifespan_starts_and_stops_loop` — `monkeypatch.setattr(get_settings(), "auto_submit_enabled", True)`, replace `api.main.run_auto_submit_loop` with a fake that sets an `asyncio.Event` and waits forever; inside `LifespanManager(create_app())` the event is set; after exit the fake's task was cancelled.
- [ ] **Step 2: Run** `uv run pytest tests/test_auto_submit_loop.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the loop and the lifespan.
- [ ] **Step 4: Run the whole suite and checks:** `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy .`. Expected: all green.
- [ ] **Step 5: Update the docs** (spec §13):
  - Foundation spec §7 roadmap → 1 Foundation *(done)*, 2 Roles *(done)*, 3 Tests & sessions — Epic A, includes Entitlements *(this plan)*, 4 Auto-grading, 5 AI pipeline (Writing), 6 Media (student recording uploads; turns Speaking sessions on), 7 AI test import — Epic B, 8 Human grading (needs 3, 5, 6), 9 Progress dashboard, 10 Stripe, 11 Deploy, 12 Scale out. §6: limit checked at session start.
  - Human grading spec §4 and §11: quota consumed at session start; toggle set on the session during the test; `grading_method` fixed at submission, `auto` for Reading/Listening.
  - CLAUDE.md: test types without "full"; Test has versions (draft/published/retired); TestSession has `mode` (`practice | exam`) and auto-submit; entitlements enforced at **start**; `load-test` command.
  - README: status, API table (the eight routes), `load-test` in commands, new settings in the configuration table.
  - Spec status → "Approved 2026-10-24; implemented (plan `2026-10-24-tests-and-sessions.md`)".
- [ ] **Step 6: Commit** `git commit -m "feat: auto-submit loop in app lifespan; docs for tests & sessions"`.

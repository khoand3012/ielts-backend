# Auto-grading (Reading & Listening) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score every Reading/Listening session when it is submitted, store the raw mark and band on the session, and give students a result endpoint plus a band in their session list.

**Architecture:** Marking is one pure module (`core/services/auto_grading.py`: no DB, no ORM) driven by the existing `item_index` and the version's answer key. `core/services/sessions.py` loads a session's version and answers, calls it from the `on_session_submitted` hook inside the submitting transaction, and also hosts the startup sweep and the `regrade-auto` loop, next to the auto-submit loop. Raw-mark→band tables live in `core/services/bands.py`. Reading versions carry a `module` (`academic | general_training`) that picks the table.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2, pytest + testcontainers Postgres.

**Spec:** `docs/superpowers/specs/2026-10-26-auto-grading-design.md`

## Global Constraints

- Work on branch `feat/auto-grading`, not `main`.
- Python 3.11; `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .` (strict) pass at every commit.
- Schema changes only via Alembic migration `0004`; `create_all` only in tests.
- Services raise `AppError` subclasses, never `HTTPException`; routers stay thin; never return ORM objects.
- The answer key appears **only** in `GET /sessions/{id}/result`, and only for a submitted session of the caller. Never in `GET /sessions/{id}`, start, list, submit, update, or `/tests`.
- Text comparison: strip, `str.lower()`, collapse internal whitespace runs to one space, map curly `’ ‘` → `'` and `“ ”` → `"` — nothing else (hyphens, punctuation, spelling compared as-is). Applied to both sides at comparison time only; stored answers stay as typed.
- Marks: `text` and `single_choice` items 1 or 0; `choice` items `len(set(answer) ∩ set(key))`; an item's max marks = `len(numbers)`; unanswered = 0.
- Band only for a practice session in which every question number is answered (a "Choose N" item counts as answered only with N picks); exam sessions always get a band.
- `module` is required for Reading versions and null for every other skill; values `academic | general_training`.
- Scoring failure (an exception in marking, after data is loaded) is logged as `auto_grading_failed` with `session_id` and never blocks submission. DB errors are not caught.
- Bands are `Decimal` in Python (never float arithmetic), `Numeric(2, 1)` in Postgres, and serialized as a JSON number (`7.0`) by the API.
- Tests create unique data (`uuid7()` slugs/emails) and never assert global counts or global sweep results: the test DB is shared across the run.
- New routes get a stable `operation_id` and the `sessions`/`tests` tag.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A session taken on an old version after the test is republished** (new key, or the module switched to General Training): re-scoring must use the session's own version's key and module, never the test's current version. → Task 6 `test_rescore_uses_the_sessions_own_version`.
2. **One session in an auto-submit batch whose marking throws**: every session in the batch is still submitted, the others are scored, and the failing one has a null score. → Task 5 `test_batch_auto_submit_with_one_failure`.
3. **Text typed on phones** (non-breaking spaces, tabs, trailing spaces, capitals, curly apostrophes) and **answer keys written with odd case or spacing** (`"Dry Stone "`): these must match; `"dry-stone"` vs `"dry stone"` must not. → Task 2 `test_text_matching_whitespace_and_case`.
4. **Empty submissions**: an exam with no answers is 0 / band 0.0; a practice with no answers is 0 / no band; the result lists every item with `answer: null`, and `correct_choices: []` for choice items. → Task 2 `test_score_empty_answers`, Task 8 `test_result_of_empty_practice`.
5. **Opening the result of an exam whose deadline has passed but that the loop hasn't submitted yet**: it is submitted and scored on read and the result comes back `graded`, not 409. → Task 8 `test_result_submits_expired_exam`.

---

### Task 1: Module enum and raw-mark band tables

**Files:**
- Modify: `core/models/enums.py`, `core/models/__init__.py` (export `Module`)
- Modify: `core/services/bands.py`
- Test: `tests/test_bands.py`

**Interfaces:**
- Produces:
  - `Module(StrEnum)`: `ACADEMIC = "academic"`, `GENERAL_TRAINING = "general_training"`.
  - `raw_to_band(skill: Skill, module: Module | None, raw: int) -> Decimal` — e.g. `Decimal("7.0")` (one decimal place). Listening ignores `module`. Raises `ValueError` for `raw` outside 0–40, for Reading with `module is None`, and for Writing/Speaking.

- [ ] **Step 1: Write the failing tests** in `tests/test_bands.py`. The tables are the spec's §4.2 values, as `(lowest raw mark, band)` from the top:

```python
LISTENING = [(39, "9.0"), (37, "8.5"), (35, "8.0"), (32, "7.5"), (30, "7.0"), (26, "6.5"),
             (23, "6.0"), (18, "5.5"), (16, "5.0"), (13, "4.5"), (10, "4.0"), (8, "3.5"),
             (6, "3.0"), (4, "2.5"), (2, "2.0"), (1, "1.0"), (0, "0.0")]
ACADEMIC = [(39, "9.0"), (37, "8.5"), (35, "8.0"), (33, "7.5"), (30, "7.0"), (27, "6.5"),
            (23, "6.0"), (19, "5.5"), (15, "5.0"), (13, "4.5"), (10, "4.0"), (8, "3.5"),
            (6, "3.0"), (4, "2.5"), (2, "2.0"), (1, "1.0"), (0, "0.0")]
GENERAL = [(40, "9.0"), (39, "8.5"), (37, "8.0"), (36, "7.5"), (35, "7.0"), (32, "6.5"),
           (30, "6.0"), (27, "5.5"), (23, "5.0"), (19, "4.5"), (15, "4.0"), (12, "3.5"),
           (9, "3.0"), (6, "2.5"), (3, "2.0"), (1, "1.0"), (0, "0.0")]
```

  - `test_raw_to_band_every_mark` — parametrized over `(Skill.LISTENING, None, LISTENING)`, `(Skill.READING, Module.ACADEMIC, ACADEMIC)`, `(Skill.READING, Module.GENERAL_TRAINING, GENERAL)`: for every `raw` in 0–40, `raw_to_band(...) == D(first band whose low <= raw)`, and `str(result)` has one decimal place. This covers every row boundary.
  - `test_same_mark_differs_by_module` — `raw_to_band(READING, ACADEMIC, 30) == D("7.0")`, `raw_to_band(READING, GENERAL_TRAINING, 30) == D("6.0")`.
  - `test_raw_to_band_errors` — `ValueError` for raw `-1` and `41`, for `(READING, None, 20)`, and for `(WRITING, None, 20)`.
- [ ] **Step 2: Run** `uv run pytest tests/test_bands.py -q`. Expected: FAIL (`ImportError`: `Module` / `raw_to_band`).
- [ ] **Step 3: Implement** `Module` and `raw_to_band`. Store the tables as data next to the existing band arithmetic, keyed by `(Skill, Module | None)`, highest row first; pick the first row with `low <= raw`. Update the module docstring to mention raw-mark conversion.
- [ ] **Step 4: Run** `uv run pytest tests/test_bands.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: raw-mark band tables for Listening and Academic/GT Reading"`.

---

### Task 2: Pure marking module

**Files:**
- Create: `core/services/auto_grading.py`
- Modify: `tests/helpers.py` (add `correct_answers`)
- Test: `tests/test_auto_grading.py`

**Interfaces:**
- Consumes: `ItemSpec`, `item_index` (`core/services/content.py`); `AnswerKey`; `raw_to_band`, `Module` (Task 1); `SessionMode`, `Skill`.
- Produces (no DB or ORM imports in this module):
  - `AnswerValue = str | list[str]`
  - `normalize(text: str) -> str`
  - `@dataclass(frozen=True) class MarkedItem`: `spec: ItemSpec`, `answer: AnswerValue | None`, `accepted: list[str]` (the key's entries, as stored), `correct_choices: list[str] | None` (choice items: the student's picks that are in the key, in the student's order, `[]` if unanswered; `None` for other kinds), `marks: int`, `answered: int` (question numbers this item counts as answered: `len(picks)` for choice, else 1 if answered, else 0); property `max_marks -> int` = `len(spec.numbers)`.
  - `@dataclass(frozen=True) class Score`: `items: list[MarkedItem]` (by question number), `raw_score: int`, `max_score: int` (sum of `max_marks`, 40 for valid content), `answered: int`, `band: Decimal | None`.
  - `mark_item(spec: ItemSpec, accepted: list[str], answer: AnswerValue | None) -> MarkedItem` — raises `ValueError` if the answer's type doesn't fit the kind (a list for a text item, a string for a choice item) or the kind isn't `text`/`single_choice`/`choice`.
  - `score(skill: Skill, module: Module | None, mode: SessionMode, index: dict[int, ItemSpec], key: AnswerKey, answers: dict[int, Any]) -> Score` — marks every item in `index` (answers looked up by item key; answer numbers that aren't item keys are ignored); `band = None` if `mode == PRACTICE and answered < max_score`, else `raw_to_band(skill, module, raw_score)`.
  - `tests/helpers.py`: `correct_answers(skill: Skill) -> dict[int, str | list[str]]` — full marks on the factory test (`reading_content`/`listening_content` with their keys): choice items → the key list, other items → the key's first entry.

- [ ] **Step 1: Write the failing tests** (unit, no DB; build `index` with `item_index(skill, parse_content(skill, listening_content()))` etc.):
  - `test_normalize` — `normalize("  Dry \t Stone ") == "dry stone"`; `normalize("Dry-Stone") == "dry-stone"`; `normalize("Farmer’s “best”") == "farmer's \"best\""`.
  - `test_curly_apostrophes_match` — accepted `["farmer's"]`: `"farmer’s"` and `"Farmer‘s"` score 1; `"farmers"` scores 0.
  - `test_text_matching_whitespace_and_case` (Review Focus 3) — text spec, accepted `["Dry Stone "]`: answers `"dry  stone"`, `"DRY STONE"`, `"dry stone "` score 1; `"dry-stone"` and `"drystone"` score 0.
  - `test_text_alternatives` — accepted `["eleven", "11"]`: `"11"` and `" Eleven"` score 1.
  - `test_single_choice` — key `["B"]`: `"B"` → 1, `"C"` → 0; `correct_choices is None`.
  - `test_choose_two_partial_and_order` — choice spec numbers `(21, 22)`, key `["B", "D"]`: `["D", "B"]` → marks 2, `correct_choices == ["D", "B"]`; `["B", "C"]` → marks 1, `correct_choices == ["B"]`, `answered == 2`; `["B"]` → marks 1, `answered == 1`; `max_marks == 2`.
  - `test_unanswered_items` — `None` on a text item → marks 0, `answered == 0`; on a choice item → marks 0, `correct_choices == []`.
  - `test_wrong_answer_type_raises` — a list on a text item and a string on a choice item raise `ValueError`.
  - `test_score_full_marks` — Listening, exam, `correct_answers(LISTENING)` → `raw_score == 40`, `max_score == 40`, `answered == 40`, `band == D("9.0")`, items in ascending `spec.key` order and `sum(i.marks) == raw_score`.
  - `test_score_practice_band_needs_every_answer` — Listening, practice, correct answers with item 21 reduced to `["B"]` → `raw_score == 39`, `answered == 39`, `band is None`; same answers in exam mode → `band == D("9.0")`; practice with all 40 answered (one text answer wrong) → band set.
  - `test_score_empty_answers` (Review Focus 4) — Reading (`Module.ACADEMIC`) `{}`: exam → `(0, 0, D("0.0"))` for `(raw_score, answered, band)`; practice → band `None`; every item has `answer is None`.
  - `test_score_ignores_unknown_answer_numbers` — Listening answers include `22: ["A"]` (inside the 21–22 item, not a key) → same result as without it.
  - `test_score_reading_module` — Reading, exam, first 30 items correct and the rest missing → `raw_score == 30`; `Module.ACADEMIC` → `7.0`, `Module.GENERAL_TRAINING` → `6.0`.
- [ ] **Step 2: Run** `uv run pytest tests/test_auto_grading.py -q`. Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `core/services/auto_grading.py` and `correct_answers`. One comparison helper serves all kinds: compare `normalize(answer)` against the set of normalized accepted entries. Module docstring: pure marking rules; spec §3.
- [ ] **Step 4: Run** `uv run pytest tests/test_auto_grading.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: pure marking rules for Reading and Listening"`.

---

### Task 3: Data model and migration `0004`

**Files:**
- Modify: `core/models/test.py`, `core/models/session.py`
- Create: `alembic/versions/0004_auto_grading.py`
- Test: `tests/test_test_models.py`

**Interfaces:**
- Produces:
  - `TestVersion.module: Mapped[str | None]` — `String(32)`, default `None`, `CheckConstraint(check_in("module", Module), name="module")`.
  - `TestSession.raw_score: Mapped[int | None]` — `SmallInteger`, default `None`, `CheckConstraint("raw_score BETWEEN 0 AND 40", name="raw_score")`.
  - `TestSession.band: Mapped[Decimal | None]` — `Numeric(2, 1)`, default `None`.
  - Migration `0004` (revises `0003`): adds the three columns and both check constraints (names via `op.f("ck_test_versions_module")`, `op.f("ck_test_sessions_raw_score")`); backfills `UPDATE test_versions SET module = 'academic' WHERE test_id IN (SELECT id FROM tests WHERE skill = 'reading')`; downgrade drops the constraints and columns.

- [ ] **Step 1: Write the failing tests**, building rows the way the existing tests in this file do:
  - `test_version_module_check` — a version with `module="ielts"` fails on commit with `IntegrityError`; `"general_training"` and `None` are accepted.
  - `test_session_raw_score_check` — `raw_score=41` fails with `IntegrityError`; `0` and `40` are accepted.
  - `test_session_band_round_trips` — `band=Decimal("6.5")` reads back as `Decimal("6.5")` from a fresh session.
- [ ] **Step 2: Run** `uv run pytest tests/test_test_models.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the model columns and the migration.
- [ ] **Step 4: Run** `uv run pytest tests/test_test_models.py -q`. Expected: PASS.
- [ ] **Step 5: Check the migration against the Compose Postgres:** `docker compose up -d postgres` (only that service; a plain `up` also runs the `migrate` and `api` services), then `uv run alembic upgrade head`, `uv run alembic downgrade -1`, `uv run alembic upgrade head`. Expected: all three succeed. If a Reading version already exists locally, `SELECT module FROM test_versions` shows `academic` for it.
- [ ] **Step 6: Commit** `git commit -m "feat: version module and session score columns (migration 0004)"`.

---

### Task 4: Module through the content pipeline, and the single-number check

**Files:**
- Modify: `core/services/catalog.py`, `core/services/test_loader.py`, `core/services/content.py`
- Modify: `seed/tests/practice-01-reading.json` (top-level `"module": "academic"` after `"skill"`)
- Modify test helpers that create Reading versions: `tests/helpers.py` (`publish_test`), `tests/test_catalog.py` (`save`), `tests/test_test_loader.py` (`make_file`), `tests/test_cli.py` (Reading file in `test_cli_load_test_publishes`)
- Test: `tests/test_test_loader.py`, `tests/test_catalog.py`, `tests/test_content.py`

**Interfaces:**
- Consumes: `Module` (Task 1), `TestVersion.module` (Task 3).
- Produces:
  - `catalog.check_module(skill: Skill, module: Module | None) -> None` — raises `ValidationAppError("reading tests need a module: academic or general_training")` for Reading without one, and `ValidationAppError("only reading tests have a module")` for any other skill with one.
  - `catalog.save_draft(..., module: Module | None = None)` — calls `check_module` first; stores `module` on the new or replaced draft.
  - `TestFile.module: Module | None = None`; `load_test_file` calls `check_module` right after parsing, **before any upload**, and passes `module` to `save_draft`.
  - `validate_version` gains the problem `code="multi_number_item"`, error, with section and question: for an item whose kind isn't `choice` and whose `len(numbers) != 1`, message `f"question {key} covers {n} numbers; only multiple-choice items may cover several"`.
  - `publish_test(db, skill, *, slug=None, content=None, key=None, module=None)` — passes `module or Module.ACADEMIC` for Reading, `None` otherwise. The other test helpers do the same: Reading gets `academic` unless the test overrides it.

- [ ] **Step 1: Write the failing tests:**
  - `test_load_reading_without_module_rejected` — Reading file without `module` → `ValidationAppError`; the `FakeUploader` recorded no calls.
  - `test_load_writing_with_module_rejected` — Writing file with `"module": "academic"` → `ValidationAppError`.
  - `test_load_stores_module` — Reading file with `"module": "general_training"` → `result.version.module == "general_training"`.
  - `test_seed_reading_has_module` — loading `seed/tests/practice-01-reading.json` with `publish=True` publishes it with `module == "academic"` and no problems.
  - `test_save_draft_checks_module` (test_catalog) — `save_draft` for Reading with `module=None` raises `ValidationAppError`.
  - `test_multi_number_text_item_is_an_error` (test_content) — Reading factory content with the fill-blank items 8 and 9 merged into one item `numbers: [8, 9]` (key `{8: ["word8"]}`, 9 removed from the key) → problems include exactly one `multi_number_item` for question 8. The valid factory cases (Listening has a 21–22 choose-two) still have no problems.
- [ ] **Step 2: Run** `uv run pytest tests/test_test_loader.py tests/test_catalog.py tests/test_content.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**, then update the helpers and the seed file listed above.
- [ ] **Step 4: Run the whole suite** `uv run pytest -q`. Expected: PASS. Existing Reading tests pass through the updated helpers.
- [ ] **Step 5: Commit** `git commit -m "feat: Reading module on test versions; single-number check for text items"`.

---

### Task 5: Scoring at submission

**Files:**
- Modify: `core/services/sessions.py`
- Test: `tests/test_auto_grading_submit.py` (new); the existing `tests/test_sessions_submit.py` keeps its `hook_calls` monkeypatch unchanged

**Interfaces:**
- Consumes: `auto_grading.score`, `Score` (Task 2); `TestVersion.module`, `TestSession.raw_score`/`band` (Task 3); existing `get_version`, `get_answers`, `catalog.load_content`, `catalog.load_answer_key`, `item_index`.
- Produces in `core/services/sessions.py`:
  - `mark_session(ts: TestSession, version: TestVersion, answers: dict[int, Any]) -> auto_grading.Score` — parses content and key, builds the index, calls `auto_grading.score(Skill(ts.skill), Module(version.module) if version.module else None, SessionMode(ts.mode), ...)`. No DB access.
  - `on_session_submitted(db, ts)` — for `ts.grading_method == GradingMethod.AUTO`: load the version and answers (DB errors propagate); `try: result = mark_session(...)` `except Exception: log.exception("auto_grading_failed", session_id=str(ts.id)); return`; then set `ts.raw_score`, `ts.band`. `_submit` commits them with the status change (unchanged).

- [ ] **Step 1: Write the failing tests** (helpers: start a session with `publish_test` + `sessions.start_session` at a fixed `NOW`; save answers with `sessions.save_answers(db, user, ts.id, [AnswerInput(question=q, value=v) ...], NOW)`; reload rows from a fresh session of the factory before asserting):
  - `test_submit_scores_reading_and_listening` — full `correct_answers` → `raw_score == 40`, `band == D("9.0")` for both skills (exam).
  - `test_module_changes_band` — two Reading exams (one `publish_test(..., module=Module.GENERAL_TRAINING)`), first 30 items correct → bands `7.0` (academic) and `6.0` (GT).
  - `test_practice_partial_has_no_band` — practice Listening with one answer missing → `raw_score == 39`, `band is None`.
  - `test_empty_submissions` — exam with no answers → `(0, D("0.0"))`; practice with no answers → `(0, None)`.
  - `test_timeout_paths_score` — an expired exam submitted by `auto_submit_expired(db, after_grace)`, and another by `get_session(db, user, id, after_grace)` → both have `raw_score` set.
  - `test_writing_is_not_scored` — submitted Writing session keeps `raw_score is None` and `band is None`.
  - `test_scoring_failure_still_submits` — monkeypatch `core.services.sessions.mark_session` to raise `RuntimeError` → `submit_session` returns `submitted`, `raw_score is None`, and the log has `auto_grading_failed` (use `structlog.testing.capture_logs`).
  - `test_batch_auto_submit_with_one_failure` (Review Focus 2) — two expired Reading exams; patched `mark_session` raises only for the first session's id and otherwise delegates to the real one → `auto_submit_expired` returns ≥ 2; both `submitted`; first has null score, second `raw_score == 0`.
- [ ] **Step 2: Run** `uv run pytest tests/test_auto_grading_submit.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `mark_session` and the hook body; update the hook's docstring to say Reading/Listening scoring runs here.
- [ ] **Step 4: Run** `uv run pytest tests/test_auto_grading_submit.py tests/test_sessions_submit.py tests/test_auto_submit_loop.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: score Reading/Listening sessions at submission"`.

---

### Task 6: Startup sweep and `regrade-auto`

**Files:**
- Modify: `core/services/sessions.py`, `core/settings.py`, `api/main.py`, `core/cli.py`, `tests/conftest.py`, `.env.example`
- Modify docs: `CLAUDE.md` (Commands: `regrade-auto`; Architecture: the startup scoring sweep), `README.md` (Commands row, Configuration row `STARTUP_SCORING_ENABLED`)
- Test: `tests/test_auto_grading_sweep.py` (new), `tests/test_cli.py`

**Interfaces:**
- Consumes: `mark_session` (Task 5).
- Produces:
  - `Settings.startup_scoring_enabled: bool = True`; `tests/conftest.py` adds `os.environ.setdefault("STARTUP_SCORING_ENABLED", "false")` next to the auto-submit line (same reason: the placeholder engine); `.env.example` adds `# STARTUP_SCORING_ENABLED=true`.
  - `@dataclass(frozen=True) class RescoreCounts: scored: int; failed: int` in `sessions.py`.
  - `rescore_sessions(factory: async_sessionmaker[AsyncSession], *, everything: bool = False) -> RescoreCounts` — selects ids of `status = submitted`, `grading_method = auto` sessions (plus `raw_score IS NULL` unless `everything`), ordered by id. Each one gets its own session and transaction: load the row, version and answers, `mark_session`, set `raw_score`/`band`, commit. Any exception → `log.exception("auto_grading_failed", session_id=...)`, count as failed, continue.
  - `run_startup_scoring(factory: async_sessionmaker[AsyncSession]) -> None` — `rescore_sessions(factory)`, logs `startup_scoring_done` with both counts; catches and logs any exception as `startup_scoring_failed` (it must never crash the app).
  - `api/main.py` lifespan: when `startup_scoring_enabled`, start `run_startup_scoring(async_session_factory)` as a task. Startup does not wait for it; on shutdown cancel it if still running, as with the auto-submit task.
  - CLI `regrade-auto [--all]` → `rescore_sessions(factory, everything=args.all)`; prints `scored {n} sessions`; if `failed`, prints `failed to score {m} sessions (see log)` to stderr and returns 1, else 0. Module docstring lists the command.

- [ ] **Step 1: Write the failing tests:**
  - `test_rescore_fills_unscored_only` — session A submitted while `mark_session` was patched to fail (null score); session B scored normally, then its row manually set to `raw_score=1, band=D("1.0")`. `rescore_sessions(factory)` → A now `raw_score == 40` (full answers), B still `1`.
  - `test_rescore_everything_overwrites` — same B, `everything=True` → B back to `40`.
  - `test_rescore_counts_failures` — patch `mark_session` to raise for one unscored session's id → `counts.failed >= 1`, and that session stays null.
  - `test_rescore_uses_the_sessions_own_version` (Review Focus 1) — Reading exam on v1 (academic, first 30 items correct → `7.0`); republish the same slug as v2 with `module=Module.GENERAL_TRAINING` and a different key for question 1; `rescore_sessions(factory, everything=True)` → the session still has `raw_score == 30`, `band == D("7.0")`.
  - `test_startup_scoring_survives_errors` — patch `rescore_sessions` to raise; `await run_startup_scoring(factory)` returns normally.
  - `test_lifespan_runs_startup_scoring` / `test_lifespan_skips_startup_scoring_when_disabled` — mirror the two lifespan tests in `tests/test_auto_submit_loop.py`: patch `api.main.run_startup_scoring` with a fake and toggle `get_settings().startup_scoring_enabled`.
  - `test_cli_regrade_auto` (test_cli) — an unscored session (patched failure at submit) → `run(["regrade-auto"], factory) == 0`, output contains `scored`, and the row is scored. `test_cli_regrade_auto_failure_exit_code` — patch `core.cli.rescore_sessions` to return `RescoreCounts(scored=2, failed=1)` → exit 1, stderr contains `failed to score 1 sessions`.
- [ ] **Step 2: Run** `uv run pytest tests/test_auto_grading_sweep.py tests/test_cli.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the setting, functions, lifespan, CLI and the doc rows listed above.
- [ ] **Step 4: Run** `uv run pytest tests/test_auto_grading_sweep.py tests/test_cli.py tests/test_auto_submit_loop.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: startup scoring sweep and regrade-auto command"`.

---

### Task 7: Module, mark and band on sessions and tests

**Files:**
- Modify: `core/schemas/session.py`, `core/schemas/test.py`, `core/services/sessions.py` (`tests_for`), `core/services/catalog.py`, `api/routers/sessions.py`, `api/routers/tests.py`
- Test: `tests/test_sessions_router.py`, `tests/test_tests_router.py`, `tests/test_catalog.py`

**Interfaces:**
- Consumes: Task 3 columns; `Module`.
- Produces:
  - `Band = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]` in `core/schemas/session.py` (JSON number, schema `number`).
  - `SessionRead` gains `module: Module | None`, `raw_score: int | None`, `band: Band | None` (inherited by `SessionDetail`).
  - `sessions.tests_for(db, sessions) -> dict[uuid.UUID, SessionTest]`, with `@dataclass(frozen=True) class SessionTest: test: Test; module: str | None` (the module of the session's own version, from the same query). Router `_read(ts, info: SessionTest)`.
  - `TestSummary` gains `module: Module | None` (from the test's current published version).
  - `catalog.list_published(db, *, skill=None, module: Module | None = None, limit, offset)` — with `module`, only tests whose current version has it. Return type unchanged.
  - `catalog.modules_for(db, tests: list[Test]) -> dict[uuid.UUID, str | None]` — current version's module per test id, one query. Used by `list_tests` and `get_test`.
  - `GET /tests` gains an optional query parameter `module: Module | None`.

- [ ] **Step 1: Write the failing tests:**
  - `test_session_list_carries_band` — submit a Reading exam over HTTP with full answers (`PUT /sessions/{id}/answers` with `correct_answers`); the `POST /sessions/{id}/submit` response itself already has `raw_score == 40` and `band == 9.0`, so the frontend never has to wait; the `GET /sessions` item has `module == "academic"`, `raw_score == 40`, `band == 9.0` (a JSON number); a Writing session item has all three `null`.
  - `test_in_progress_session_has_null_score` — `GET /sessions/{id}` right after start → `raw_score is None`, `band is None`, `module == "academic"` for Reading.
  - `test_list_tests_module_filter` — publish an academic and a GT Reading test; `GET /tests?skill=reading&module=general_training&limit=100` contains the GT id and not the academic one; each item's `module` matches; Listening items have `module: null`.
  - Update `test_list_and_get_published_tests`: the expected dict gains `"module": None`.
  - `test_list_published_by_module` (test_catalog) — the same filter at service level.
  - The existing `assert_no_secrets` checks on `GET /sessions/{id}` after submit still pass (answer key still absent).
- [ ] **Step 2: Run** `uv run pytest tests/test_sessions_router.py tests/test_tests_router.py tests/test_catalog.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: module, raw score and band on sessions and tests"`.

---

### Task 8: Result endpoint, and docs

**Files:**
- Modify: `core/schemas/session.py`, `core/services/sessions.py`, `api/routers/sessions.py`
- Modify docs: `README.md` (API row, status), `CLAUDE.md` (result endpoint shapes under Key domain concepts if needed), `docs/superpowers/specs/2026-06-23-backend-foundation-design.md` §7 item 4 (spec + plan links, *(done)*), the auto-grading spec's status line → `Approved 2026-10-26; implemented (plan 2026-10-26-auto-grading.md)`
- Test: `tests/test_session_result.py` (new)

**Interfaces:**
- Consumes: `mark_session`, `Score`, `MarkedItem` (Tasks 2, 5); `SessionTest`, `Band` (Task 7).
- Produces:
  - `sessions.time_spent_seconds(ts: TestSession) -> int` — `submitted_at − started_at`, using `min(submitted_at, deadline)` when there is a deadline; whole seconds.
  - `sessions.get_result(db, user, session_id, now=None) -> tuple[TestSession, auto_grading.Score | None]` — `get_session` (submits an expired exam first; non-owner → `NotFoundError`); still `in_progress` → `ConflictError("session is not submitted yet")`; returns a `Score` recomputed with `mark_session` only when `grading_method == auto` and `raw_score is not None`, else `None`.
  - Schemas: `ResultQuestion(number: int, numbers: list[int], kind: Literal["text", "single_choice", "choice"], answer: str | list[str] | None, accepted: list[str], correct_choices: list[str] | None, marks: int, max_marks: int)`; `SessionResult(session_id, test_id, title, skill, module: Module | None, mode, submit_reason: SubmitReason | None, grading_method: GradingMethod | None, started_at, submitted_at: datetime | None, time_spent_seconds: int, status: Literal["graded", "being_graded"], raw_score: int | None, max_score: int | None, band: Band | None, answered: int | None, questions: list[ResultQuestion] | None)`.
  - Route `GET /sessions/{session_id}/result`, tag `sessions`, `operation_id="get_session_result"`, 200 / 404 / 409. Graded (a `Score` exists) → `status="graded"`; `raw_score`/`band` come from the **stored columns**; `max_score`, `answered`, `questions` from the `Score`. Otherwise (Writing, Speaking, failed scoring) → `status="being_graded"` and those six fields `null`.

- [ ] **Step 1: Write the failing tests:**
  - `test_result_graded_listening` — exam with full answers except item 21 = `["B", "C"]` → `status == "graded"`, `raw_score == 39`, `max_score == 40`, `answered == 40`, `band == 9.0` (Listening 39 → 9.0); `sum(q["marks"]) == raw_score`; the 21 row has `numbers == [21, 22]`, `kind == "choice"`, `correct_choices == ["B"]`, `marks == 1`, `max_marks == 2`, `accepted == ["B", "D"]`; a text row shows `answer` exactly as saved (e.g. `" Word31 "`) and `accepted == ["word31", "alt31"]`; rows are in ascending `number` order.
  - `test_result_partial_practice` — practice Reading with 10 answers → `status == "graded"`, `band is None`, `answered == 10`, `raw_score` set.
  - `test_result_of_empty_practice` (Review Focus 4) — practice Listening, no answers → `raw_score == 0`, `band is None`, every row `answer is None`, the 21 row `correct_choices == []`.
  - `test_result_in_progress_is_409` — practice session not submitted → 409 with `error.code == "conflict"`.
  - `test_result_other_user_404` and `test_result_requires_auth` (401).
  - `test_result_submits_expired_exam` (Review Focus 5) — Reading exam started 2 hours before the real clock (`start_session(..., now=datetime.now(UTC) - timedelta(hours=2))`) and never submitted → 200 `graded`, `submit_reason == "timeout"`, `time_spent_seconds == 3600` (capped at the 60-minute deadline).
  - `test_result_time_spent_practice` — practice started at a fixed time, submitted 125 s later via `submit_session(..., now=...)` → `time_spent_seconds == 125`.
  - `test_result_writing_being_graded` — submitted Writing → `status == "being_graded"`, `raw_score`, `band`, `questions` all `null`.
  - `test_result_failed_scoring_being_graded` — Reading submitted with `mark_session` patched to fail → `being_graded`.
  - `test_openapi_has_result_route` — `/openapi.json` has `get_session_result` under tag `sessions`.
- [ ] **Step 2: Run** `uv run pytest tests/test_session_result.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the service function, schemas and route (thin router: `_result(ts, info, score)` builds `SessionResult`).
- [ ] **Step 4: Run everything:** `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy .`. Expected: all green.
- [ ] **Step 5: Update the docs** listed under Files. README API table gets `GET /sessions/{id}/result` — "Result: mark, band and per-question answers with the answer key (submitted sessions only)", and the session list row mentions the band.
- [ ] **Step 6: Commit** `git commit -m "feat: GET /sessions/{id}/result; docs for auto-grading"`.

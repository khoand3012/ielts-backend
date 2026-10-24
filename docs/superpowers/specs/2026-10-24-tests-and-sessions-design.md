# Tests & Sessions (Epic A) — Design

**Date:** 2026-10-24
**Status:** Draft, awaiting approval
**Builds on:** `2026-06-23-backend-foundation-design.md` (single-process monolith),
`2026-10-24-human-grading-design.md` (roles, grading methods)

---

## 1. Intent

Students need tests to take. This epic adds the test content itself (stored in
versions, with draft and published states) and the way a student takes a test:
a **session** in **practice** or **exam** mode, answers saved as they go, a
server-enforced timer and auto-submit when time runs out.

Admins will eventually upload tests from their own PDF / Word / audio files and
let AI convert them (Epic B, §2). Until then, tests are loaded from JSON files
with a CLI command, so the whole student flow can be built and tested first.

## 2. Epics

Test content work is split into two epics, built in this order:

- **Epic A — Tests & sessions** (this document): content model, versions,
  draft/published, content validation (the "problems list"), file loader,
  student browsing, sessions, modes, autosave, timers, auto-submit, daily
  limits at start, the manual-grading toggle, and serving test assets
  (images/audio) through signed URLs.
- **Epic B — AI test import** (own spec later): admin uploads a PDF/Word file
  per skill (plus 4 audio files for Listening), AI converts it into a draft,
  the Writing Task 1 image is extracted unchanged from the file, the API
  returns the draft with its problems list, the admin reviews and edits it,
  then publishes; editing a test creates a new draft version.

Decisions already made for Epic B are recorded in §12 so they are not lost.

## 3. Goals, non-goals, anti-goals

**Goals**
- Four kinds of single-skill test: `reading`, `listening`, `writing`,
  `speaking`. A student takes one skill at a time.
- Tests are versioned. A version a student has taken never changes.
- Students only ever see published tests, and never see answer keys.
- Practice mode (no time limit) and exam mode (real time limits).
- Answers are saved as the student goes; closing the tab loses nothing.
- In exam mode the server submits the test when time runs out, even if the
  student never comes back.
- Writing and Speaking daily limits are checked and used up when the test
  **starts**.

**Non-goals (Epic A)**
- Scoring anything: Reading/Listening scoring is the Auto-grading phase,
  Writing AI grading the AI pipeline phase, teacher grading the Human grading
  phase. This epic ends at "submitted".
- Results pages, correct answers shown to students, band conversion of raw
  scores.
- Admin HTTP endpoints for content (upload, review, edit, publish) — Epic B.
  In this epic, content changes happen through the CLI loader only.
- Student audio recording and upload — the Media phase. Speaking sessions
  cannot be started until then (§8.7).
- "Full" tests covering all four skills in one sitting — dropped.
- IELTS General Training — Academic only for now.

**Anti-goals** — would be a failure even if it "works":
- A student receiving an answer key in any response.
- A student seeing a draft, or a version changing under a student mid-test.
- Reading a Writing or Speaking prompt without using up a daily test (content
  is only delivered inside a session — §7).
- A submitted session being submitted twice (e.g. the student's submit and the
  auto-submit racing), which would later mean double grading.
- An answer arriving after the deadline being accepted in exam mode.
- An exam-mode session left open forever because the student walked away.

## 4. Decisions from the product owner

- Tests are uploaded per skill; there are no full tests.
- IELTS structure is fixed, and any other count is a problem: Listening 4
  parts / 40 questions, Reading 3 passages / 40 questions, Writing 2 tasks,
  Speaking 3 parts.
- A new test starts as a draft until it is published.
- Editing a test creates a new version on top of it, saved as a draft.
- A published version can't be edited once a student has taken it.
- Students take one skill at a time.
- Answers are saved as the student goes; the test auto-submits when the timer
  ends.
- The Writing / Speaking daily limit is checked when the student **starts** a
  test (it used to be at submission).
- Two modes: **practice** has no time limit; **exam** has the real limits.
- Speaking: one recording per question. In both modes the student can replay
  the question and their own answer, and re-record.
- A Speaking question has audio, text, or both — at least one.

## 5. Test content

### 5.1 Tests and versions

- A **test** is the stable identity: its skill and title. Students pick tests.
- A **test version** holds the content. Status `draft → published → retired`.
  - A test has **at most one draft** at a time.
  - Publishing a draft makes it the test's live version; the previously
    published version becomes `retired`. Sessions already running on a
    retired version continue on it untouched.
  - A published or retired version is **immutable** — no code path updates
    its content. *(my call)* Every change creates a new version, even if no
    student has taken the current one; one rule is simpler than two.
  - Versions are numbered 1, 2, 3… per test.
- A test with no published version is invisible to students.

### 5.2 Content and answer key are stored apart

Each version has two JSONB documents *(my call)*:

- `content` — everything a student may see: passages, instructions, question
  groups, questions, options, word limits, Writing prompts, Speaking questions,
  asset references. (Not the Listening transcript — §5.3.)
- `answer_key` — `{question_number: [accepted answers]}` for Reading and
  Listening; empty for Writing and Speaking.

Keeping the key in its own column means the student-facing serializer has
nothing to strip, which makes leaking it much harder than filtering one big
document. Both documents are validated by strict Pydantic schemas (one per
skill) on every write.

Why JSONB rather than tables per section and question *(my call)*: a version is
written once as a whole (by the loader now, by the AI import and review screen
in Epic B), read as a whole, copied as a whole when a new version is made, and
never changed after publishing. Answers refer to questions by number within a
version, which is stable because versions are immutable.

### 5.3 Content shape

The shape follows the practice test already written
(`seed/tests/academic-practice-test-01.json`), split into one file per skill:

- **Reading** — 3 passages, each with a title and paragraphs (optionally
  labelled A, B, C… for heading-matching), each with question groups.
- **Listening** — 4 parts, each with an audio asset and question groups. The
  transcript is stored for admins and later grading/review features but is
  **not student-visible** during a session *(my call: it would give the
  answers away)* — it lives in a third, admin-only field alongside
  `answer_key`, or inside it; builder's choice.
- **Writing** — Task 1 (prompt, image asset, minimum 150 words) and Task 2
  (prompt, minimum 250 words).
- **Speaking** — Part 1 (topics, each with questions), Part 2 (cue card text,
  bullet points, optional rounding-off question), Part 3 (questions). Every
  Speaking question has `text`, `audio` (asset), or both.

**Question groups** have a type (`multiple_choice`, `fill_blank`, `matching`,
`short_answer`), instructions, optional shared options (matching boxes,
TRUE/FALSE/NOT GIVEN), an optional word limit, an optional context (summary
text with gaps) and questions.

**Question numbering:**
- Reading and Listening questions are numbered 1–40.
- A question item covers one number, or several when it asks for several
  answers (e.g. "Choose TWO letters" covers 21 and 22). The item lists its
  `numbers`; its **key** is its first number.
- **Multi-number items** *(my call)*: everything about the item is keyed by its
  first number. The answer key has one entry, `21: ["B", "D"]` (the set of
  correct letters, exactly N of them). The student saves one value under 21: a
  list of up to N **distinct** option keys, in any order. Saving under 22 → 422
  (not an item key). Auto-grading later awards one mark per correct letter, up
  to N, so the item is still worth N of the 40 marks and one of two right earns
  one mark.
- Writing tasks are numbered 1–2. Speaking questions are numbered 1…N in order
  across the three parts. The Part 2 long turn is one number, and each
  rounding-off question gets its own. So every answer in every skill is keyed
  by `(session, item key)`.

### 5.4 Assets

Images (Writing Task 1) and audio (Listening parts, Speaking questions) are
files in R2. `content` refers to them by R2 object key, never by URL. When a
session's content is returned, each key is turned into a **signed GET URL**
(lifetime builder's choice; suggest 15 min, and a fresh one on every read). The
loader uploads local asset files referenced by the JSON file to R2 (MinIO
locally).

### 5.5 Problems list (content validation)

One function, `validate_version(skill, content, answer_key) -> list[Problem]`,
checks a version and returns every problem found. Each problem has a `code`, a
human-readable `message`, a location (part/passage/task number and question
number where relevant) and a severity: `error` blocks publishing, `warning`
does not.

Errors at minimum:
- Wrong number of passages / parts / tasks for the skill (§4 counts).
- Reading/Listening question numbers not exactly 1–40 (gaps, duplicates).
- A Reading/Listening question with no accepted answer.
- A multiple-choice or matching answer that is not one of the options; an item
  asking for N answers whose key doesn't have N.
- A Listening part without an audio asset.
- Writing Task 1 without an image asset.
- A Speaking question with neither text nor audio; a Part 2 without cue card
  text.

Warnings are the builder's choice (e.g. an accepted answer longer than the word
limit). Epic A uses it in the loader; Epic B returns it to the review screen.

## 6. Loading tests (CLI)

`python -m core.cli load-test <path> [--publish]`, alongside the existing
`set-role`:

- The file holds one skill's test: a `slug` (stable identity), title, skill,
  content, answer key and paths to any local asset files.
- First load of a slug creates the test and version 1 as a draft. Loading the
  same slug again creates the next version as a draft — or replaces the
  existing draft if one exists *(my call: matches "at most one draft")*.
- `--publish` publishes the draft if `validate_version` returns no errors;
  otherwise it prints the problems and exits non-zero, leaving the draft.
- Problems (errors and warnings) are always printed.
- The existing practice test is split into four per-skill files under
  `seed/tests/`. Reading can be published straight away. Writing needs a Task 1
  chart image (generate a PNG from the chart data once and commit it).
  Listening stays a draft until real audio exists. Speaking can be published,
  but no one can start it until Media (§8.7).

## 7. Student browsing

- `GET /tests?skill=` — published tests: id, skill, title, time limits per
  mode. Paginated like `/admin/users`.
- `GET /tests/{test_id}` — the same summary for one test.
- Neither returns content. **Content is only delivered inside a session**, so a
  Writing or Speaking prompt can't be read without starting a test and using up
  a daily test. Drafts and tests with no published version return 404.

Any role can take tests (decided in the human grading design).

## 8. Sessions

### 8.1 Starting

`POST /sessions {test_id, mode}`:
- Uses the test's **current published version**; the session is tied to that
  version for its whole life.
- Writing/Speaking: checks and uses up one test from the daily limit (§9).
  Over the limit → `QuotaExceededError` (HTTP 429, `resets_at` in the error's
  `extra`), and no session is created.
- Exam mode: `deadline = started_at + time limit for the skill`. Practice
  mode: no deadline.
- Returns the session with its content (answer key never included, asset keys
  turned into signed URLs).
- Retakes are unlimited; each start is a new session. A student may have
  several sessions open at once *(my call)*.
- Abandoned tests still count toward the daily limit, and so do practice-mode
  tests (grading them costs the same).

### 8.2 Time limits

Fixed per skill, held in settings *(my call: IELTS timing is fixed, matching
"simplify the rules")*:

| Skill | Exam-mode limit |
|---|---|
| Reading | 60 min |
| Listening | 32 min *(my call: 30 min of audio + 2 min to check, as in computer-delivered IELTS)* |
| Writing | 60 min |
| Speaking | 20 min *(my call: the real test is 11–14 min; extra room for re-recording)* |

These settings are the only source of time limits; test files don't carry
their own (the practice test's `time_limit_minutes` fields are dropped when it
is split). A **grace period** (setting, default 30 s) after the deadline still accepts
answers, so a save sent in the last second counts.

### 8.3 Reading a session

`GET /sessions/{id}` returns the session (mode, status, started_at, deadline,
server time *(my call: so the frontend can correct its clock)*, the
manual-grading toggle), the content and the saved answers — so a student who
closes the tab and comes back resumes where they were. `GET /sessions` lists
the student's own sessions, newest first. Another user's session → 404.

### 8.4 Saving answers

The frontend sends answers as the student fills them in; the server keeps the
latest value per question (upsert). Batch or single-answer endpoint is the
builder's choice.

- Every value is validated against the question it answers: an unknown
  question number → 422; multiple choice → option keys from that item, no more
  than it asks for; matching → one option key; fill-blank / short answer →
  text; Writing → essay text (max length builder's choice). Speaking values
  are recording references, defined in the Media phase.
- Saving to a submitted session, or in exam mode after deadline + grace →
  409. The check and the write happen in one transaction.
- Empty value clears the answer.

### 8.5 Submitting

- `POST /sessions/{id}/submit` — the student submits early (both modes).
- **Auto-submit** — exam-mode sessions past deadline + grace are submitted
  with whatever answers are saved, two ways:
  - an in-process loop started with the app checks every minute *(my call:
    needed so Writing grading can start even if the student never returns;
    one machine is already assumed for the grading sweep)*;
  - any read or save of an expired session submits it first. The submit is
    committed before the save's 409 is returned, so the error doesn't roll it
    back.
- Submitting is a single conditional update (`status = in_progress` →
  `submitted`); whoever loses the race does nothing. So a session is
  submitted exactly once.
- The session records `submitted_at` and `submit_reason` (`student` |
  `timeout`), and fixes `grading_method` (§8.6).
- After submitting, one hook runs: `on_session_submitted(session)`. In this
  epic it does nothing; the Auto-grading, AI pipeline and Human grading phases
  fill it in. It runs inside the same transaction as the status change, so a
  later phase can insert its grading rows atomically with submission.

### 8.6 Manual-grading toggle (Writing)

- `request_manual_grading` lives on the Writing session and can be changed any
  time before submission (`PATCH /sessions/{id}`).
- At submission `grading_method` is fixed: Writing → `human` if the toggle is
  on, else `ai`; Speaking → `human`; Reading/Listening → `auto`.
- Auto-submit uses the toggle's value at that moment.

### 8.7 Speaking sessions

Speaking tests can be loaded and published, but `POST /sessions` for a
Speaking test returns an error until the Media phase adds recording uploads.
The rules below are decided now and built then:
- One recording per question. Question audio and/or text shown; Part 2 shows
  the cue card with 1 minute of preparation, then up to 2 minutes of speaking.
- Both modes: the student can replay the question and their answer, and
  re-record; the last recording is kept.
- Exam mode: per-answer maximum length (settings; suggest 60 s Part 1, 120 s
  Part 3) and the overall deadline. Practice mode: neither.

### 8.8 What the server does and doesn't enforce

The server enforces time (deadline, grace, auto-submit), ownership, and answer
shapes. Playing Listening audio only once in exam mode, the Speaking
preparation minute and recording lengths are enforced by the frontend; the
session response tells it the mode and the limits.

## 9. Daily limits (folded in from the Entitlements phase)

Because the limit is now checked at the start of a session, and starting a
session is in this epic, the Entitlements phase is built here *(my call)*.

- Table `daily_usage (user_id, skill, day, count)`, primary key
  `(user_id, skill, day)`, `day` = UTC date of the start.
- `core/services/entitlements.check_and_consume(session, user, skill)` does a
  single atomic statement: insert or increment **only while** `count < limit`;
  no row returned → over the limit. Two simultaneous starts can't both pass
  the last slot.
- Limits come from the existing settings (`free_writing_daily` etc.) by the
  user's plan. `resets_at` = next midnight UTC.
- It runs in the same transaction as creating the session, so a failed start
  uses nothing.

## 10. Data model

All tables: UUIDv7 PK and timestamps (existing mixins), `TIMESTAMPTZ`, Alembic
migration.

- `tests` — `skill` (check: four values), `slug` unique, `title`,
  `current_version_id` FK nullable (the published one).
- `test_versions` — `test_id` FK, `version` int (unique with `test_id`),
  `status` (`draft | published | retired`), `content` JSONB, `answer_key`
  JSONB, `published_at` nullable; partial unique index allowing one draft per
  test.
- `test_sessions` — `user_id` FK, `test_version_id` FK, `skill` (copied for
  queries), `mode` (`practice | exam`), `status` (`in_progress | submitted`),
  `started_at`, `deadline` nullable, `submitted_at` nullable, `submit_reason`
  nullable, `request_manual_grading` bool default false, `grading_method`
  nullable (`auto | ai | human`). Index on `(status, deadline)` for the
  auto-submit loop; on `(user_id, created_at)` for "my sessions".
- `answers` — `test_session_id` FK, `question_number`, `value` JSONB; unique
  `(test_session_id, question_number)`.
- `daily_usage` — §9.

## 11. API sketch

New OpenAPI tags `tests` and `sessions`; stable `operation_id`s.

| Method & path | Purpose |
|---|---|
| `GET /tests?skill=` | published tests |
| `GET /tests/{test_id}` | one test's summary |
| `POST /sessions` | start (`test_id`, `mode`); 429 over daily limit |
| `GET /sessions` | my sessions |
| `GET /sessions/{id}` | session + content + saved answers |
| `PUT /sessions/{id}/answers` (or per question) | save answers |
| `PATCH /sessions/{id}` | Writing: `request_manual_grading` |
| `POST /sessions/{id}/submit` | submit early |

New typed errors: `QuotaExceededError` (429, code `quota_exceeded`) and a 409
for writes to a closed session (code e.g. `session_closed`).

## 12. Decided for Epic B (recorded, not built here)

- Admin uploads one PDF or Word file per test, for one skill. AI converts it
  into a draft version.
- Listening: the admin also uploads **exactly 4 audio files**, one per part;
  any other count is rejected with an error.
- Writing Task 1: the image is extracted from the file unchanged; the AI never
  alters or redraws it.
- Speaking question audio: the admin may attach an audio file to any question;
  each question needs text, audio or both.
- The API returns the draft with its problems list (§5.5); the frontend shows
  it. The admin reviews, corrects and publishes.
- Editing a test creates a new draft version on top of it.
- Admin audio uploads need upload links from the Media phase, so **Media comes
  before Epic B**.

## 13. Changes to existing docs

- **Roadmap** (foundation spec §7): phase 3 becomes this epic and absorbs
  Entitlements; Media moves before a new "AI test import (Epic B)" phase.
- **Entitlements** — CLAUDE.md "Freemium", foundation spec §6, human grading
  spec §4/§11: `check_and_consume` runs at **session start**, not submission.
  Human grading spec §4 keeps its table but the quota column moves to start.
- **Manual-grading toggle** — human grading spec §4: the toggle is set on the
  session during the test, not only in the submit body; `grading_method` is
  still fixed at submission.
- **CLAUDE.md** domain concepts: drop "full" from test types; Test has versions
  (draft / published / retired); TestSession has `mode`; Listening divisions
  are "parts" to students.

## 14. Left to the builder

Pagination style, exact response shapes, batch vs per-question answer saving,
essay max length, signed-URL lifetime, where the Listening transcript lives
(§5.3), warning-level problems, the auto-submit loop's shutdown handling,
module names — following CLAUDE.md conventions (thin routers, typed errors,
integration tests on real Postgres, R2 mocked).

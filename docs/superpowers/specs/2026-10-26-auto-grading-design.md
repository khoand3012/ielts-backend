# Auto-grading (Reading & Listening) — Design

**Date:** 2026-10-26
**Status:** Approved 2026-10-26 (open questions answered by the product owner:
band tables, answer matching incl. curly apostrophes, recovery after a scoring
failure, bands for partial practice). Plan: `plans/2026-10-26-auto-grading.md`.
**Roadmap:** phase 4 of `specs/2026-06-23-backend-foundation-design.md` §7.
**Builds on:** `specs/2026-10-24-tests-and-sessions-design.md` (Epic A) and the
result endpoint planned in `specs/2026-10-24-human-grading-design.md` §9.

Decisions marked *(my call)* were made by the designer and can be overridden; the
rest come from the product owner.

## 1. Why

Students can take and submit Reading and Listening tests, but nothing scores them:
`on_session_submitted` is an empty hook, so a submitted test has no mark, no band
and no way to see which answers were right. This phase scores every Reading and
Listening session when it is submitted and gives the student a result page and a
band in their list of past tests.

## 2. Goals, non-goals, anti-goals

**Goals**
- Score a Reading/Listening session at the moment it is submitted (by the student
  or by the deadline), in the same transaction. Students never see a "being
  graded" wait for these skills.
- Store the raw mark (0–40) and the IELTS band on the session. A practice
  session gets a band only when every question was answered (§4.3).
- A result endpoint showing: when the test was taken, time spent, raw mark and
  band, mode and submit reason, and for every question the student's answer, the
  accepted answers and the marks earned.
- The band of every past Reading/Listening attempt in the session list.
- Reading tests are either **Academic** or **General Training**, each with its
  own band table. Listening has one table.
- A one-off command that scores sessions submitted before this phase existed.

**Non-goals**
- Score per passage/part and per question type (product owner: not needed).
- Evidence / explanations per answer, Listening transcript links — later.
- Admins or teachers viewing other students' results — the Human grading phase
  (`GET /admin/results`).
- Writing and Speaking results — the AI pipeline and Human grading phases.
- General Training *Writing* (letter task). `module` is only required for Reading
  in this phase.
- Progress dashboard (phase 9) — but the stored band is what it will query.

**Anti-goals** (would make this a failure even if it technically works)
- The ticks shown per question disagreeing with the total. The backend is the only
  place right/wrong is decided; the frontend never re-implements the comparison.
- A student failing to submit, or the auto-submit loop failing, because scoring
  broke.
- The answer key leaking anywhere before a session is submitted, or to anyone but
  the session's owner.

## 3. Marking rules

All marking lives in one pure module, `core/services/auto_grading.py` *(my call
for the name)*: no database access, so it can be unit-tested table-driven and
reused by the CLI.

### 3.1 Comparing a text answer

Both the student's answer and every accepted answer are **normalized** before
comparing (product owner: trim + lowercase):

1. Strip leading and trailing whitespace.
2. Lowercase (`str.lower()`).
3. *(my call)* Collapse runs of internal whitespace to one space, so
   `"dry  stone"` matches `"dry stone"`.
4. (Product owner.) Curly apostrophes and quotes become straight ones: `’ ‘` →
   `'`, `“ ”` → `"`. Phones type the curly forms by default, so `farmer’s` matches
   a key written `farmer's`. It's the same mark in another typographic form, not a
   different answer.

Nothing else (product owner: strict): punctuation, hyphens and spelling are
compared as-is, so `"dry-stone"` ≠ `"dry stone"` unless the key lists both. IELTS marks
misspellings wrong; a test author who wants to accept a variant lists it in the
key (the key already allows alternatives, e.g. `24: ["eleven", "11"]`).

Normalization happens only at comparison time. Answers stay stored exactly as the
student typed them, so the result shows what they wrote *(my call)*.

Word limits ("NO MORE THAN TWO WORDS") need no separate check: an answer with
extra words won't equal any accepted answer, which matches the IELTS rule that
over-limit answers are wrong.

### 3.2 Marks per item

Items are the answerable units from `core.services.content.item_index` (keyed by
their first question number, Epic A §5.3). An item's maximum marks are
`len(numbers)`, so a full test is always worth 40.

| Item kind (`ItemSpec.kind`) | Key holds | Marks |
|---|---|---|
| `text` (fill_blank, short_answer) | accepted alternatives | 1 if the normalized answer equals any normalized alternative, else 0 |
| `single_choice` (matching) | exactly one option key | 1 if the answer equals it, else 0 |
| `choice` (multiple_choice, "Choose N") | exactly N option keys, the correct set | one mark per chosen key that is in the set — `len(set(answer) ∩ set(key))`, at most N |

Option keys are already validated against the item's options when saved, so
choices compare exactly. *(my call)* They go through the same normalization
anyway, so one comparison function serves all kinds.

A question with no saved answer scores 0.

### 3.3 New content check

*(my call)* `validate_version` gains one error: a `fill_blank`, `short_answer` or
`matching` item must cover exactly one question number (only multiple-choice
items may cover several). Otherwise a text item spanning two numbers could only
ever earn one mark and the test would be worth less than 40. Both seed files
already satisfy this.

## 4. Bands

### 4.1 Reading module

A Reading test is **Academic** or **General Training** (product owner). New
column `test_versions.module` — `academic | general_training`, **required for
Reading, null for every other skill**. *(My call: "module" is the IELTS term, and
later General Training Writing can reuse the column.)*

*(My call)* It lives on the **version**, not the test, because a version is
immutable once published: a session is always scored with the module of the
version it was taken on, a draft never changes what students currently see, and
`regrade-auto --all` re-scores with the same module the session was taken under.
Changing a test's module means loading a new version and publishing it, like any
other content change.

- The test JSON file gets a top-level `module` field, stored on the draft
  version. The loader rejects a Reading file without it and a non-Reading file
  with it (`ValidationAppError`).
- `validate_version` doesn't need it — the loader check and the DB constraint
  cover it.
- The migration sets `module = 'academic'` on every existing Reading version, and
  `seed/tests/practice-01-reading.json` gets `"module": "academic"`.
- What students browse is the module of the test's **current published version**.

### 4.2 Conversion tables

Raw mark → band, three tables, stored as data in `core/services/bands.py` next to
the existing band arithmetic (one place to edit). Each row is "lowest raw mark for
this band".

These are the commonly published Cambridge/IELTS tables. Official cut-offs move
slightly between real test papers and secondary sources disagree on a few rows.
Where they disagree on General Training band 7.0 (34 vs 35), the product owner
chose **35**. The rest of the tables are used as written below; the code treats them
as editable data.

| Band | Listening | Academic Reading | General Training Reading |
|---|---|---|---|
| 9.0 | 39 | 39 | 40 |
| 8.5 | 37 | 37 | 39 |
| 8.0 | 35 | 35 | 37 |
| 7.5 | 32 | 33 | 36 |
| 7.0 | 30 | 30 | 35 |
| 6.5 | 26 | 27 | 32 |
| 6.0 | 23 | 23 | 30 |
| 5.5 | 18 | 19 | 27 |
| 5.0 | 16 | 15 | 23 |
| 4.5 | 13 | 13 | 19 |
| 4.0 | 10 | 10 | 15 |
| 3.5 | 8 | 8 | 12 |
| 3.0 | 6 | 6 | 9 |
| 2.5 | 4 | 4 | 6 |
| 2.0 | 2 | 2 | 3 |
| 1.0 | 1 | 1 | 1 |
| 0.0 | 0 | 0 | 0 |

The published tables stop at band 4.0. Rows below that are *(my call)*: a smooth extension so a very low mark
still gets a band, with 0 correct → 0.0.

Function: `raw_to_band(skill, module, raw) -> Decimal` (e.g. `Decimal("7.0")`),
raising `ValueError` for a raw mark outside 0–40 or a missing module on Reading.
Table-driven unit test covering every row boundary.

### 4.3 Partial practice attempts

A practice session can be submitted after only part of the test (e.g. one
passage). Scaling that mark to a band out of 40 would show a misleadingly low band,
so (product owner) **a practice session gets a band only if every question was
answered**. Otherwise `raw_score` is stored and `band` stays null.

- "Every question answered": each text and single-choice item has a saved answer,
  and each "Choose N" item has N picks. Empty answers are never stored (saving an
  empty answer clears it), so a saved answer counts as answered.
- **Exam** sessions always get a band. An unanswered question scores 0, as in the
  real test.
- The rule lives in the same pure scoring function, so submission, the startup
  sweep and `regrade-auto` all apply it the same way.
- The progress dashboard (phase 9) reads `band`, so partial practice attempts
  stay out of band trends without any extra filter.

## 5. Data model

Migration `0004`:

- `test_versions.module` — `String(32)`, nullable; check `module IN ('academic',
  'general_training')`. Backfill: versions of existing Reading tests →
  `academic`. ("Required for Reading" is enforced by the loader, since the skill
  lives on `tests`, not on the version.)
- `test_sessions.raw_score` — `SmallInteger`, nullable, check `0 ≤ raw_score ≤ 40`.
- `test_sessions.band` — `Numeric(2, 1)`, nullable. It can be null while
  `raw_score` is set: a partial practice attempt (§4.3).

Both new session columns stay null for Writing/Speaking in this phase. *(My call,
for later phases: the AI and Human grading phases could also write their overall
band to `test_sessions.band`, so the session list and the progress dashboard read
one column for every skill. That decision belongs to those phases.)*

Per-question correctness is **not stored** (product owner): the student's answer
is already in `answers` and the accepted answers in the version's `answer_key`;
marks are computed when the result is read.

## 6. Scoring at submission

`on_session_submitted(db, ts)` (Epic A §8.5) is filled in. For a session with
`grading_method = auto`:

1. Load the session's version (content + answer key) and its saved answers.
2. Mark every item (§3), sum to `raw_score`, convert to `band` (§4) using the
   version's `module`. A practice session with unanswered questions gets no band
   (§4.3).
3. Set `ts.raw_score` and `ts.band`. `_submit` commits them in the same
   transaction as the status change.

This runs for every path through `_submit`: the student's submit, a submit
triggered on read/save after the deadline, and the every-minute auto-submit loop.

**If scoring fails** *(my call)*: the marking step (pure Python, after the data is
loaded) is wrapped so an exception is logged (`auto_grading_failed`, with session
id) and the session is still submitted with `raw_score`/`band` left null. A
scoring bug must never block submission or stall the auto-submit loop.
Such sessions are filled in automatically by the startup sweep (§6.1) once the bug is fixed.
Database errors are not caught — they abort the submit as today.

An exam session submitted with no answers scores 0 / band 0.0. A practice
session submitted with no answers scores 0 with no band (§4.3).

### 6.1 Startup sweep

(Product owner.) When the app starts, it scores every submitted
`grading_method = auto` session with `raw_score IS NULL`. A scoring bug can only be
fixed by a deploy, and a deploy restarts the app, so the fix and the re-score
arrive together without anyone running a command. The first deploy of this phase
also scores every session submitted before scoring existed.

- It runs as a background task started in the app lifespan, next to the
  auto-submit loop. Startup and health checks never wait for it.
- It uses the same scoring function as §6, with one transaction per session. A
  session that still fails is logged (`auto_grading_failed`) and skipped. It is
  retried on the next restart, not in a loop.
- Like the AI-grading startup sweep, it assumes a single machine (see
  `CLAUDE.md` › Hosting). Two machines would at worst score the same session twice
  with the same result, since scoring is deterministic.

## 7. API

### 7.1 Result — `GET /sessions/{id}/result`

The endpoint the human-grading spec already plans for all skills. Tag `sessions`,
`operation_id="get_session_result"` *(my call: it is the student's own session)*.

- Only the session's owner; anyone else → 404 (as other session routes).
- Like `GET /sessions/{id}`, an exam session past its deadline is submitted first.
- Still in progress → 409 (`ConflictError`, "session is not submitted yet").

Response `SessionResult` *(field names are the builder's to refine)*:

```
session_id, test_id, title, skill, module (null unless reading),
mode, submit_reason, grading_method,
started_at, submitted_at, time_spent_seconds,
status: "graded" | "being_graded",
raw_score (int | null), max_score (40 | null), band (decimal | null),
answered,               # questions answered (0–40); auto skills only, else null
questions: [            # null unless graded and auto
  {
    number,             # the item key (first number)
    numbers,            # e.g. [21, 22] for "Choose TWO"
    kind,               # "text" | "single_choice" | "choice"
    answer,             # what the student saved, as stored; null if unanswered
    accepted,           # text: any of these; choice: this exact set
    correct_choices,    # choice only: the student's picks that are right; else null
    marks, max_marks,
  }, ... in question order
]
```

- **"Choose N" items** *(my call)*: one row per item (as answers are saved), with
  `correct_choices` computed by the backend so the frontend can tick each letter
  without comparing sets itself. `marks = len(correct_choices)`.

- **`status`**: Reading/Listening with a stored score → `graded`. Writing and
  Speaking (not graded until later phases) and an auto session whose scoring
  failed → `being_graded` with score fields null *(my call: keeps the contract the
  human-grading spec planned, so the frontend handles one shape for every skill)*.
- **Partial practice** (§4.3): `status` is `graded` with `raw_score` set and
  `band` null. `answered` lets the frontend explain why ("answer all 40 questions
  to get a band").
- **`time_spent_seconds`** = `submitted_at − started_at`, but for a session with a
  deadline it uses `min(submitted_at, deadline)` *(my call: the loop can submit up
  to a minute plus grace late; a 60-minute exam shouldn't show 61:30)*.
- **`raw_score`/`band`** come from the stored columns, so the result always matches
  the session list. `questions[].marks` are computed on read with the same
  function; a test asserts they sum to the stored `raw_score`.
- **`mode`** and **`submit_reason`** are the flags the frontend uses to label
  practice vs exam and "submitted by you" vs "time ran out" (product owner).
- **Answer key** *(product owner)*: the full set of accepted answers is shown as
  soon as the session is submitted, in both practice and exam mode. It is never
  in `GET /sessions/{id}` or any other endpoint.

### 7.2 Session list and detail

`SessionRead` (used by `GET /sessions`, `GET /sessions/{id}`, start, submit,
update) gains `module`, `raw_score` and `band` — null until scored, and always null
for Writing/Speaking in this phase. The list shows the band of every past attempt
(product owner); a partial practice attempt shows its raw mark instead (§4.3).

### 7.3 Browsing tests

`TestSummary` (`GET /tests`, `GET /tests/{id}`) gains `module`, read from the
current published version. `GET /tests` gains an optional `module` filter *(my
call: the frontend will want separate Academic and General Training lists)*.

## 8. Scoring old sessions — `regrade-auto`

`uv run python -m core.cli regrade-auto [--all]`

- Default: scores every submitted session with `grading_method = auto` and
  `raw_score IS NULL`, the same set as the startup sweep (§6.1). Usually not
  needed, since the sweep runs on every deploy. It's useful locally, or to retry
  without a restart.
- `--all` *(my call)*: re-scores every auto session, overwriting stored scores —
  for after a marking-rule or band-table change. Each session uses its own
  version's content, key and module.
- Prints how many sessions were scored; a session that fails is logged and
  skipped, and the command exits non-zero if any failed.
- Uses the same scoring function as §6, one transaction per session *(my call:
  one bad session doesn't roll back the rest)*. It shares its query and loop with
  the startup sweep.

## 9. Testing

- **Unit** (no DB): normalization; marks for each kind including partial "Choose
  TWO", alternatives, unanswered, extra whitespace and case; `raw_to_band` at
  every table boundary for all three tables and the out-of-range errors; the
  §4.3 rule (practice partial → no band, practice complete → band, exam partial
  → band; a "Choose TWO" with one pick counts as unanswered).
- **Integration** (real Postgres):
  - submitting a Reading and a Listening session stores `raw_score`/`band`;
    the auto-submit loop and submit-on-read also score
  - Academic vs General Training give different bands for the same raw mark
  - result endpoint: owner only (404 otherwise), 409 while in progress,
    per-question marks sum to `raw_score`, `time_spent_seconds` capped at the
    deadline, Writing returns `being_graded`
  - the answer key is absent from `GET /sessions/{id}` before and after submit
  - a scoring exception still submits the session with null score
  - the startup sweep scores unscored auto sessions and skips (logs) one that
    still fails
  - `regrade-auto` scores only unscored sessions; `--all` re-scores
  - a partial practice session: `raw_score` set, `band` null, result `graded`
    with `answered` < 40
  - loader: Reading without `module` and Writing with `module` are rejected;
    the new single-number content check
  - the session list carries `band`

## 10. Left to the builder

- Exact Pydantic names and module layout beyond what is named above.
- How the result's `questions` are ordered internally (output is by question
  number).
- Whether `raw_to_band` lives as a dict-of-lists or another table shape.
- Updating `CLAUDE.md` (Commands: `regrade-auto`) and the foundation roadmap
  (§7: phase 4 → spec link, done when built).

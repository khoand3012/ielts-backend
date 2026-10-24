# Human Grading (Speaking + opt-in Writing) & Account Roles — Design

**Date:** 2026-10-24
**Status:** Approved 2026-10-24. Roles, admin users and band arithmetic implemented
(plan `2026-10-24-roles-and-admin-users.md`); the grading flow is not built yet.
**Builds on:** `2026-06-23-backend-foundation-design.md` (single-process monolith)

---

## 1. Intent

Some IELTS answers are graded by **human teachers** instead of AI:

- **Speaking** is *always* graded by a teacher (no AI).
- **Writing** stays AI-graded by default, but the student can tick **"Request
  manual grading"** when submitting. Then AI is skipped and a teacher grades it.
- If **AI grading of a Writing test fails all 3 attempts**, the test drops into
  the teacher queue automatically, so the student gets a teacher's grade
  instead of a "failed" message.

To support that, the platform gets **three kinds of accounts** with different
access, one shared **grading queue** for teachers, and an **admin overview** of
grading activity.

## 2. Goals, non-goals, anti-goals

**Goals**
- Three roles — student, teacher, admin — enforced server-side.
- Any teacher can grade any student's Speaking test or manually-requested
  Writing test.
- Teachers score the official IELTS criteria in half bands plus written feedback;
  overall bands are computed, never typed.
- No two teachers ever grade the same test.
- Admins see every graded test, manage roles, unstick abandoned claims, and see
  an overview of grading activity.
- Writing and Speaking tests show the student only two states: **Being
  graded** → **Graded** — whether AI or a teacher grades it.
- A student never sees an AI grading failure; a failed AI job becomes a
  teacher's job.

**Non-goals (v1)**
- Transcripts of Speaking recordings (explicitly deferred).
- AI assistance for human grading of any kind (no AI draft, no AI "second opinion").
- Requesting manual grading *after* submission, or re-grading an AI result by a
  human.
- Notifications (email/push when a grade is ready) — later.
- Classes / teacher-student assignment — any teacher sees all tests.
- Teacher payment, scheduling, or SLAs.
- Editing a grade after it is submitted (*my call*).

**Anti-goals** — would be a failure even if it "works":
- Two teachers grading the same test (wasted teacher time, conflicting scores).
- A manually-requested Writing test *also* being sent to the AI model (double cost,
  confusing double result).
- A student seeing a half-finished draft grade.
- A student being able to make themselves a teacher.
- A test getting stuck in a claim forever with no way to recover it.
- An AI failure producing two queue entries, or AI still retrying after the test
  has been handed to a teacher (double grading, double cost).

## 3. Roles & permissions

`User.role` ∈ {`student`, `teacher`, `admin`}, default `student`.

| Capability | Student | Teacher | Admin |
|---|:-:|:-:|:-:|
| Take tests, see own results | ✓ | ✓ ¹ | ✓ ¹ |
| See grading queue, claim, grade | | ✓ | ✓ ² |
| Release own claim | | ✓ | ✓ |
| See all graded tests (any student, any skill) | | | ✓ |
| Change any user's role / plan | | | ✓ |
| Release / reassign *any* claim | | | ✓ |
| Admin overview | | | ✓ |

¹ *My call:* any role may take tests (handy for staff to try the product). A
teacher can **never claim their own test**.
² *My call:* admin is a superset of teacher, so a small team works.

- **Sign-up always creates a `student`.** Only an admin can change a role, via
  the admin API. `role` is never accepted from the registration body.
- **First admin bootstrap** *(my call):* a CLI command,
  `python -m core.cli set-role <email> admin`, run once against the database
  (locally or via `fly ssh console`). No HTTP path can create the first admin.
- **Admins cannot demote themselves** *(my call — avoids locking out the last
  admin)*.
- **Enforcement:** role is read from the `users` row that `get_current_user`
  already loads on every request — **not** from a JWT claim — so a role change
  takes effect on the next request. A FastAPI dependency
  `require_role("teacher", "admin")` guards routers; services raise the existing
  `ForbiddenError` for ownership checks.

## 4. How a test reaches a grader

At submission, in **one transaction**:

| Skill | Student choice | Quota consumed | Created | Then |
|---|---|---|---|---|
| Speaking | — (always human) | `speaking` | `HumanGrade(skill=speaking, awaiting_grader)` | teacher queue |
| Writing | toggle **off** (default) | `writing` | `AIAssessment(processing)` | in-process AI task (foundation spec §5) |
| Writing | toggle **on** | `writing` | `HumanGrade(skill=writing, awaiting_grader)` | teacher queue; **no AI task** |

- The Writing submit body gets `request_manual_grading: bool = false`. The choice
  is stored on the `TestSession` as `grading_method` ∈ {`ai`, `human`} and is
  fixed from then on.
- **Daily limits are unchanged and shared** (product owner decision): a manual
  Writing request counts toward the same Writing limit as AI grading (free
  3/day, paid 30/day). Speaking has its own limit (free 3/day, paid 30/day).
  `entitlements.check_and_consume(user, skill)` runs before anything is created;
  over limit → `QuotaExceededError` with `resets_at`, nothing created.
- Reading/Listening are untouched (auto-graded, unlimited).

### 4.1 AI failure → teacher queue

When the AI Writing task fails its **last of 3 attempts** (any cause: API error,
timeout, invalid structured output), it does the following in **one
transaction**:

1. Mark the `AIAssessment` row `failed`, keeping the error and any raw model
   response (still needed for debugging).
2. Insert `HumanGrade(skill=writing, status=awaiting_grader, origin=ai_fallback)`.
   `human_grades.test_session_id` is unique, so the insert is
   `ON CONFLICT DO NOTHING` — a retry or a restart can never create a second
   queue entry.

Rules:
- **No second quota charge** — the Writing quota was consumed at submission.
- The student's status stays **Being graded** throughout; the switch is
  invisible to them *(my call)*.
- The test keeps its **original submission time**, so it lands in the queue at
  the position it would have had — it is not pushed to the back *(my call)*.
- Once handed over, AI never touches that test again. The startup sweep only
  re-runs `AIAssessment` rows still in `processing`, never `failed` ones.
- A setting `AI_FAILURE_FALLBACK_TO_TEACHER` (default `true`) can turn this off,
  e.g. during a long OpenRouter / model outage that would flood the queue; when off, the
  row is just `failed` as in the foundation spec *(my call)*.

## 5. Human grading flow (both skills)

### 5.1 States

One `HumanGrade` row per human-graded `TestSession` (1:1).

| Internal status | Meaning | Student sees |
|---|---|---|
| `awaiting_grader` | in the queue, unclaimed | **Being graded** |
| `claimed` | a teacher has claimed it (draft may exist) | **Being graded** |
| `graded` | teacher submitted the grade | **Graded** |

```
submit (human) ─────────┐
                        ▼
AI fails 3× ──────► awaiting_grader ──claim──► claimed ──submit grade──► graded
                        ▲                         │
                        └──── release / admin ────┘
                              (reassign = move claim to another teacher)
```

### 5.2 Step by step

1. **Queue** — teachers see one queue: all `awaiting_grader` rows, **oldest
   submission first**, paginated, filterable by skill (`speaking` / `writing`).
   Each row: skill, test name, submitted-at, student email *(my call — teachers
   know whom they grade; revisit if anonymity matters)*, and an **"AI failed"**
   label for `origin = ai_fallback` rows *(my call — tells the teacher why a
   Writing test that didn't request manual grading is there)*.
2. **Claim** — atomic conditional update:
   `UPDATE … SET status='claimed', claimed_by=:me, claimed_at=now()
   WHERE id=:id AND status='awaiting_grader'`. 0 rows → `409 Conflict`
   ("already claimed"). A teacher holds at most `TEACHER_MAX_ACTIVE_CLAIMS`
   claims across both skills (setting, default 3) *(my call — stops hoarding)*.
   Claiming your own test → `403`.
3. **Grade** — the detail view depends on the skill:
   - **Speaking:** each question (Part 1/2/3 prompt text) paired with a
     short-lived **pre-signed R2 GET URL** for that answer's recording.
   - **Writing:** Task 1 prompt (text + a pre-signed URL for the chart/image)
     and Task 2 prompt, each with the student's answer text and its **word
     count** (computed server-side, *my call* — under-length is a scoring factor).
   - Plus any saved draft.
4. **Save draft** any number of times (`PUT …/draft`, partial allowed). Drafts are
   visible only to the claiming teacher and admins.
5. **Submit grade** — every criterion band and the feedback fields are required.
   The server computes the band(s) (§6), sets `status=graded`, `graded_by`,
   `graded_at`. Only the claiming teacher may submit. The grade is final.
6. **Student** sees **Graded** with criterion bands, comments, feedback and the
   overall band. Teacher identity is not shown *(my call)*.
7. **Unsticking** — the claiming teacher can *release* their own claim (draft is
   discarded *(my call)*). An admin can *release* any claim, or *reassign* it to a
   named teacher: the draft moves with it, the target's claim limit is ignored,
   and the target must be teacher/admin and not the test's own student
   *(my calls)*. No automatic timeout in v1 — the overview flags stale claims.

## 6. What teachers score, and band arithmetic

All criterion bands: **0–9 in half-band steps** (product owner decision). Each
criterion takes an optional short comment.

**Speaking** — four criteria: Fluency & Coherence, Lexical Resource, Grammatical
Range & Accuracy, Pronunciation. Plus overall feedback.
Speaking band = `round_band(mean of the four)`.

**Writing** — scored per task, four criteria each *(following the official IELTS
Writing criteria)*:
- Task 1: Task Achievement, Coherence & Cohesion, Lexical Resource, Grammatical
  Range & Accuracy.
- Task 2: Task Response, Coherence & Cohesion, Lexical Resource, Grammatical
  Range & Accuracy.
- Feedback per task.
- Writing band = `round_band((T1 + 2 × T2) / 3)`, where T1/T2 are the
  *unrounded* means of each task's criteria — Task 2 counts twice as much as
  Task 1 (official IELTS weighting). Each task's band is shown to the student as
  `round_band(T)`. *(My call: rounding only once for the final band avoids
  double-rounding drift.)*
- The AI Writing path must use the same `round_band` and weighting, so a manual
  and an AI result are comparable.

**`round_band`** — the British Council rule (product owner decision): round to
the nearest half band; a value ending in **.25 rounds up to the next half
band**, **.75 rounds up to the next whole band**. The BC publishes this rule for
the overall IELTS score; we apply it to criteria means too. In general: nearest
0.5, ties round up — `floor(x × 2 + 0.5) / 2`, using `Decimal`, never float.
Examples: 6.125 → 6.0, 6.25 → 6.5, 6.375 → 6.5, 6.625 → 6.5, 6.75 → 7.0,
6.875 → 7.0. One function in `core/services/` with a table-driven unit test.

## 7. Admin features

- **All graded tests** — every skill: Speaking (human), Writing (AI, teacher by
  request, or teacher after AI failure — labelled which), Reading/Listening
  (auto). Filters: skill, grading method,
  student, teacher, date range. Open any result.
- **Users** — list/search; change `role` and `plan`.
- **Claims** — release or reassign any claim.
- **Overview** (computed live with SQL, no caching; window 7/30/90 days, default
  30), each metric **total and per skill**:
  - tests **waiting** (`awaiting_grader`) and **in progress** (`claimed`) now
  - **stale claims**: claimed longer than `STALE_CLAIM_HOURS` (setting, default
    24), with teacher and age, each with release/reassign actions
  - **turnaround** submitted → graded: average and median *(my call — one slow
    test skews the mean)*
  - **per teacher**: tests graded in the window, average claim → grade time
  - *(my call)* share of Writing submissions that requested manual grading
  - **AI failures handed to teachers** in the window — a spike means the AI model is
    struggling and teacher workload is rising

## 8. Data model (new / changed)

- `users.role` — `String(16)`, default/server_default `student`, check constraint
  on the three values.
- `test_sessions.grading_method` — `ai | human`, set at submission (Writing:
  from the toggle; Speaking: always `human`; Reading/Listening: null or `auto`,
  builder's choice).
- `human_grades` — UUIDv7 PK, timestamps (mixins), and:
  - `test_session_id` FK, **unique**
  - `skill` (`speaking` | `writing`), `status`
  - `origin` — why it is human-graded: `speaking` | `student_request` |
    `ai_fallback`
  - `claimed_by` FK users nullable, `claimed_at` nullable
  - `scores` JSONB nullable — per-skill shape (§6) validated by a Pydantic
    schema per skill; criteria, comments and feedback live here *(my call: the
    two skills have different shapes, so JSONB + strict schemas beats ~20
    nullable columns)*
  - `overall_band` `numeric(2,1)` nullable — a real column so the overview and
    progress dashboard can query it
  - `graded_by` FK users nullable, `graded_at` nullable
  - index on `(status, created_at)` for the queue; on `(graded_by, graded_at)`
    for per-teacher stats
- Speaking recordings: Speaking `Answer` rows carry their R2 object key (from the
  Sessions + Media phases). Writing answers are text on `Answer`.

## 9. API sketch

New OpenAPI tags `grading` and `admin`; stable `operation_id`s.

| Who | Method & path | Purpose |
|---|---|---|
| student | `POST /sessions/{id}/submit` | Writing body adds `request_manual_grading` |
| student | `GET /sessions/{id}/result` | status `being_graded \| graded` + result, for AI and human alike *(my call: AI `processing` shows as `being_graded`, `ready` as `graded`; a `failed` AI job is never shown — it became a human grade — unless `AI_FAILURE_FALLBACK_TO_TEACHER` is off, then status `failed`)*. If a `HumanGrade` exists for the session, it is the result |
| teacher | `GET /grading/queue?skill=` | unclaimed, oldest first |
| teacher | `GET /grading/mine` | my active claims |
| teacher | `POST /grading/{grade_id}/claim` | 409 if taken, 409 over claim limit, 403 own test |
| teacher | `POST /grading/{grade_id}/release` | release my claim |
| teacher | `GET /grading/{grade_id}` | prompts + answers/audio URLs + draft |
| teacher | `PUT /grading/{grade_id}/draft` | save partial |
| teacher | `POST /grading/{grade_id}/submit` | finalize |
| admin | `GET /admin/results` | all graded tests, filters |
| admin | `GET /admin/users`, `PATCH /admin/users/{id}` | list; change role/plan |
| admin | `POST /admin/grading/{grade_id}/release` | force release |
| admin | `POST /admin/grading/{grade_id}/reassign` | body `{teacher_id}` |
| admin | `GET /admin/overview?window=30d` | §7 metrics |

## 10. Decisions from the product owner

- Speaking is graded by human teachers; three roles (student / teacher / admin).
- Any teacher can grade any student's test.
- Writing is AI-graded unless the student requests manual grading at submission;
  then it goes to the teacher queue *instead of* AI.
- A manual Writing request counts toward the same Writing daily limit (3/day free).
- Speaking keeps its own daily limit (3/day free, 30/day paid).
- Students see only **Being graded** and **Graded** for human grading.
- Admin overview is in v1. No transcript yet.
- Half bands allowed per criterion.
- Bands follow the British Council rounding rule (§6).
- If AI grading of a Writing test fails all 3 attempts, it drops into the teacher
  queue instead of showing the student "failed" (§4.1).

## 11. Changes to the existing plan

- **Speaking leaves the AI pipeline** — no Speaking prompts. `AIAssessment`, the
  in-process background task and the startup sweep serve **Writing only**, and
  only for `grading_method = ai`.
- **AI Writing** must use the shared `round_band` and Task 2 double weighting (§6).
- **AI failure handling** (foundation spec §5, CLAUDE.md "AI grading"): on final
  failure the row is marked `failed` **and handed to the teacher queue** (§4.1),
  instead of `failed` being the end state shown to the student.
- **Entitlements** unchanged in shape; consumed at submission for both paths.
- **Roadmap** (foundation spec §7) gains two phases:
  - **Roles & admin users** — `users.role`, `require_role`, CLI bootstrap, admin
    user management. Depends only on Foundation. *(done)*
  - **Human grading** — §4–§9 of this doc. Depends on Test content & sessions,
    Media (Speaking recordings in R2), Entitlements; the Writing toggle also
    touches the AI pipeline phase.
- **CLAUDE.md**: domain concepts (`role`, `grading_method`, `HumanGrade`), AI
  grading section (Writing only, opt-out via toggle, failure → teacher queue),
  Security notes (role enforcement).

## 12. Left to the builder

Pagination style, exact response shapes, pre-signed URL lifetime (suggest 15
min), module names, how Reading/Listening fill `grading_method`, and test
structure — following CLAUDE.md conventions (thin routers, typed errors,
integration tests on real Postgres, R2 mocked).

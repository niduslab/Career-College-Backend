# 26 — Course Q&A / Discussion

A per-course discussion board where **enrolled learners** ask questions, discuss,
and get replies, and the course's **instructors** answer. Access is restricted to
people who belong to the course — no public/guest surface.

Modeled after the **review system** (file layout, service-layer gate, denormalized
counters, `*Error(http_status)` exception) and the **messaging system** (enrolled-
learner gate resolved in the service, two-level threading, soft delete).

## What it is

- A learner posts a **question** (title + body), optionally anchored to a specific
  content item (a lecture/quiz/assignment/coding slot).
- Anyone with course access posts **replies** (flat, chronological — two-level
  threading: question → replies).
- Questions and replies can be **upvoted** (counter-only — see below).
- Instructors can **pin** important questions and **delete** anything; authors can
  delete their own posts. Deletes are **soft** (`is_deleted`).

## Data model — `courses/all_models/discussion_models.py`

| Model | Purpose | Key fields |
|---|---|---|
| `CourseQuestion` | A thread | `course`, `author`, `related_content` (nullable FK → `SectionContent`, `SET_NULL`), `title`, `body`, `is_pinned`, `is_deleted`, `reply_count`, `upvote_count` |
| `QuestionReply` | A reply in a thread | `question`, `author`, `body`, `is_instructor_reply` (denormalized badge), `is_deleted`, `upvote_count` |

- **`related_content` → `SectionContent`**, not `Lecture`. `SectionContent` is the
  project's single content-ordering abstraction (GFK to Lecture/Quiz/Assignment/
  Coding), so a question can anchor to any content type uniformly. `SET_NULL` keeps
  the discussion alive if the content is later removed. Null = general course-level
  question.
- **Upvotes are counter-only.** `upvote_count` is a plain denormalized integer on
  each of `CourseQuestion` / `QuestionReply`; there is **no** per-user vote table.
  `POST .../upvote/` is an atomic `F('upvote_count') + 1`. Consequence — accepted
  as a deliberate simplification: **no dedup** (a user can upvote repeatedly), **no
  un-upvote** (increment-only), and **no `viewer_upvoted` flag** (nothing records
  who upvoted). If those become requirements, reintroduce a per-(voter, target)
  vote table (mirror `ReviewVote` / the nullable-FK `payments.Order` pattern) and
  swap the increment for a toggle. Because nothing dedups and `upvote_count`
  drives the `?ordering=-upvote_count` sort, the two upvote endpoints are
  rate-limited per user (`DiscussionUpvoteThrottle`, `DISCUSSION_UPVOTE_RATE_LIMIT`,
  default `30/min`) — the only brake on one caller inflating a thread's ranking.
  The throttle is a mitigation, not a fix; the vote table is the real fix.
- `reply_count` / `upvote_count` are denormalized so list rows never aggregate.

## Access control — service layer only

There is **no `IsEnrolled` DRF permission**. A pure enrolled-only gate would wrongly
lock out instructors, who must also participate. Views carry only
`[IsAuthenticated, IsEmailVerified]`; the real gate is
`discussion_service._assert_access(user, course)`, which reuses
`learner_service.resolve_course_access` and grants access to:

- an **active enrolled learner** (`Enrollment.is_active=True`), OR
- a **course instructor** (on `course.instructors`, or `created_by`), OR
- a **platform admin**.

`_assert_access` returns `is_instructor` (bool) so the service can additionally gate
instructor-only actions (pin, delete-any) and set `is_instructor_reply`.

### Access-denied status (project 403-vs-404 rule)

- Slug entry points (`<slug>/questions/` list + create) → **403** on no access.
- Numeric-ID entry points (question/reply detail, replies, votes, pin) → **404**
  (IDs are not public-enumerable; a 404 doesn't confirm existence).

## Endpoints (`/api/v1/courses/`)

| Method | Path | Action | Who |
|---|---|---|---|
| GET | `<slug>/questions/` | List questions (paginated) | enrolled / instructor |
| POST | `<slug>/questions/` | Ask a question | enrolled / instructor |
| GET | `questions/<id>/` | Question + replies | enrolled / instructor |
| DELETE | `questions/<id>/` | Soft-delete question | author / instructor |
| POST | `questions/<id>/replies/` | Post a reply | enrolled / instructor |
| POST | `questions/<id>/pin/` | Toggle pin | instructor |
| POST | `questions/<id>/upvote/` | Increment upvote counter | enrolled / instructor |
| DELETE | `replies/<id>/` | Soft-delete reply | author / instructor |
| POST | `replies/<id>/upvote/` | Increment upvote counter | enrolled / instructor |

List filters (validated against an allow-list, service layer): `?content_id=`
(questions anchored to one content item), `?ordering=` (`-created_at` | `created_at`
| `-upvote_count` | `-reply_count`). Pinned questions always sort first regardless of
`ordering`. Pagination is `StandardResultsSetPagination` wrapped in the standard
`{success, data}` envelope.

## Concurrency

- `reply_count` bumped with `F('reply_count') + 1` on reply create, decremented
  (floored at 0) on soft delete.
- `upvote_count` bumped with `F('upvote_count') + 1` — a single atomic `UPDATE`, no
  read-modify-write, so concurrent upvotes never lose an increment.

## Access-gate cost

`_assert_access()` resolves platform admins and the course creator from columns
already loaded on the `NidusCourse` row, so neither pays for a lookup. Everyone
else falls through to `learner_service.resolve_course_access()`, which evaluates
`course.instructors` and (for non-instructors) runs one enrollment query. **Do not
add a second `course.instructors.filter(...).exists()` on top of it** — the roster
check is already done there.

## Notifications

In-app only (`skip_email=True`, like reviews) — dispatched via
`transaction.on_commit`:

- **`QUESTION_POSTED`** → course instructors (excluding the asker) when a question is
  created.
- **`QUESTION_REPLIED`** → the question author + prior thread participants (excluding
  the replier) when a reply is posted.

Wiring: `NotificationEventType` (+2), builders `_question_posted` / `_question_replied`
+ `_BUILDERS` registration, `EVENT_TO_CATEGORY` (both → `COURSE_ACTIVITY`). No email
template is needed because dispatch is `skip_email=True`.

## Files

- Models — `courses/all_models/discussion_models.py`
- Serializers — `courses/all_serializers/discussion_serializers.py`
- Service — `courses/services/discussion_service.py` (`DiscussionError`)
- Views — `courses/all_views/discussion_views.py`
- URLs — `courses/urls.py`
- Tests — `courses/all_tests/test_discussion.py`
- Migration — `courses/migrations/0026_*`, `notifications/migrations/0012_*`
- Manual-test guide — `docs/api-testing/postman-discussion.md`

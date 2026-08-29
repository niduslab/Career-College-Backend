# 32 — AI Course Outline Generator

LLM-drafted course outlines for the course builder. An instructor (or partner
institution) supplies the course metadata they have already typed, and gets back a
module-by-module outline they can edit and turn into real `CourseSection` rows.

**No new model, no migration, nothing auto-saved.** The endpoint is a suggestion
generator, exactly like `AssignmentRubricPreviewAPIView` — generate, return, let the
human decide.

## Why it exists

Two hand-typed surfaces in the builder:

- `NidusCourse.course_outline` — a plain `TextField` that is **required at submission
  for `delivery_mode=scheduled`** (`course_models.py` → `clean()`), because a cohort
  course has no fully-authored curriculum and the written outline is what the admin
  reviews.
- The curriculum itself — one `CourseSection` created at a time.

This feature drafts both from metadata the instructor has already entered.

## Topology

```
Next.js  ──JWT cookie──▶  Django (this repo)  ──X-Service-Key──▶  AI services  ──▶  Groq
```

- **The frontend never calls the AI service.** It only talks to this backend, like
  every other feature.
- **The AI service never touches the frontend or the database.** Metadata in, outline
  JSON out.
- **Django ↔ AI service is server-to-server**, authenticated with a shared secret
  header, not JWT — no end-user identity crosses that boundary because Django has
  already authorized the caller.
- **The provider key (`GROQ_API_KEY`) never exists in this repo.** It lives only in
  the AI services project's environment.

The AI service is the `Career-College-AI-Services` FastAPI project. Its
`app/course_outline_generator/` module implements this contract; see that repo's
`STRUCTURE_README.md` for the layout and `REQUEST_VALIDATION_README.md` for exactly
how it authenticates this backend — including the limits of that check (the shared
secret *is* the identity; there is no mTLS, IP allowlist, or replay protection).

## Endpoint

`POST /api/v1/courses/ai/outline-preview/` → `CourseOutlinePreviewAPIView`
(`courses/all_views/ai_views.py`), route name `courses:ai-outline-preview`.

**Permissions:** `[IsAuthenticated, IsEmailVerified, IsCourseCreator]` — the same
triple as `CourseCreateAPIView`. `IsCourseCreator`, not `IsVerifiedCourseCreator`:
authoring has to work before identity verification completes, and it must cover
partner institutions as well as individual instructors. (Note this differs from the
rubric preview, which is `IsInstructorUser` and therefore closed to institutions.)

**Throttle:** `AIOutlineThrottle` (`scope='ai_outline'`,
`rate=AI_OUTLINE_RATE_LIMIT`, default `10/min`), defined in the view module like
every other custom throttle in this project. This is the only throttle in the
codebase that guards **spend** rather than data integrity — every call is a paid LLM
request taking several seconds, so a held-down button must not bill the platform.

Request body — `CourseOutlineRequestSerializer`
(`courses/all_serializers/ai_serializers.py`):

| Field | Required | Maps to |
|---|---|---|
| `title` | yes | `NidusCourse.title` |
| `description` | yes | `NidusCourse.description` (plain text — the caller strips markup) |
| `audience` | yes | `NidusCourse.audiences` (one per line) |
| `prerequisites` | no | `NidusCourse.prerequisites` (one per line) |
| `level` | no | `NidusCourse.CourseLevel` choices |
| `language` | no, default `English` | `NidusCourse.language` |
| `duration_minutes` | no | `NidusCourse.duration_minutes` — a hint, not a target |
| `category` | no | free-text hint, deliberately **not** a `CourseCategory` id |
| `extra_instructions` | no | free-text steer; also what makes a regenerate differ |

Response `200`:

```json
{
  "success": true,
  "message": "Outline generated.",
  "data": {
    "modules": [
      {
        "title": "Foundations of Machine Learning",
        "summary": "Core vocabulary and the supervised/unsupervised split.",
        "learning_outcomes": ["Explain the difference between supervised and unsupervised learning"],
        "topics": ["What is ML?", "Types of learning"],
        "estimated_duration_minutes": 90
      }
    ],
    "outline_text": "Module 1: Foundations of Machine Learning (90 min)\n..."
  }
}
```

`modules` drives the editable preview cards. `outline_text` is the same content
flattened, ready to drop into `course_outline` with no client-side formatting.

| Case | Status |
|---|---|
| ok | `200` |
| serializer invalid | `400` + `errors` |
| unauthenticated | `401` (via `envelope_exception_handler`) |
| learner, or unverified email | `403` |
| over throttle | `429` (via `envelope_exception_handler`) |
| AI service unreachable / non-200 / malformed JSON | `503`, friendly message |
| anything else | `500`, generic message |

**403 is the only permission failure** — the URL carries no resource id, so the
403-vs-404 rule has nothing to protect against (see CLAUDE.md → *403 vs. 404
Access-Denied Policy*).

## Service client — `courses/services/ai_outline_service.py`

Pure HTTP I/O, no business logic — the same shape as
`payments/services/sslcommerz_service.py`:

- `REQUEST_TIMEOUT = (5, 45)` as a module constant. The read leg is long because
  generation is an LLM call; the AI service's own `LLM_TIMEOUT_SECONDS` is set
  **below** 45 s so it gives up first and returns a real status instead of leaving
  Django to time out.
- `AIOutlineError(message, http_status=503)` — the `ScheduleError` / `ReviewError` /
  `PaymentError` pattern, defined in the service module (the `courses` convention;
  only `payments` keeps a separate `exceptions.py`).
- **Every failure collapses to one 503 with one generic message.** Network error,
  `401` from a mismatched service key, `502` from the provider, unparseable body —
  all identical to the end user. The real reason is logged; the upstream detail must
  never reach the browser.
- Blank `level` / `category` are sent as `null`, not `''` — the AI service types them
  as optional and `''` fails its schema.

## What the caller does with the result

Nothing here writes to the database. Two consumers, both using endpoints that
already existed:

1. **Curriculum** (the frontend's primary path): one `CourseSection` per accepted
   module via `POST /api/v1/courses/<pk>/sections/create/`, sequentially —
   `position` is server-ordered, so a concurrent burst would race for slots.
2. **Course outline text:** the edited `outline_text` goes into `course_outline` on
   the normal `POST /api/v1/courses/create/` or `PATCH /api/v1/courses/<pk>/`.

### A second apply updates, it does not stack

Regenerating and applying again would otherwise leave two batches of sections on the
course. The frontend records the section ids its last apply wrote and, on the next
one, `PATCH`es those rows (`PATCH /api/v1/courses/sections/<id>/`) instead of
creating new ones; only modules beyond that count create fresh sections.

**Nothing is ever deleted.** A reused row keeps its id, its position and any lessons
already inside it — so no authored content is lost and the change is reversible by
editing. If the new outline has *fewer* modules than the last one, the leftover
sections are left in place and reported, never removed: the user deletes them
deliberately through the normal module delete. Sections the instructor created by
hand are never touched, and an id that has since been deleted is dropped from the
tracked list rather than resurrected.

### Why provenance is stored, not queried

`GET .../sections/` exposes `created_by`, `last_edited_by`, `created_at` — and none
of them distinguish an AI-applied section from a hand-made one, because the AI
service never writes to the database: Django stamps `request.user` on both paths via
`save_authored`. The only queryable signal is `created_at` clustering (an apply
writes N rows within a second or two), which is a heuristic that would eventually
overwrite a section the instructor wrote by hand. That is the one outcome this design
refuses.

So the record is kept client-side, in `localStorage` per course
(`src/lib/ai-outline-store.ts`, key `cc_ai_outline_sections:<courseId>`), with the
same status as the login flag in `src/lib/session.ts`: **a UI hint, not a source of
truth.** Every access is wrapped in try/catch — storage throws in private mode and
when a browser blocks site data, and a failed read must never break the curriculum
builder.

**When there is no record but the course has sections** — another browser, cleared
storage, a colleague's machine — the modal does not guess. It asks: *leave them and
add these after* (the default) or *overwrite the first N in place*, stating plainly
that the second option can hit hand-made sections and that nothing is deleted either
way. The user's answer supplies the provenance the client lacks.

Making this authoritative would mean a provenance column on `CourseSection` plus a
migration — deliberately not done, since the feature is otherwise no-new-model and
the ask-when-unknown path covers the gap without guessing.

### `outline_text` must not contain blank lines

`NidusCourseCreateUpdateSerializer._normalize_multiline` strips empty lines on save.
The AI service therefore renders `outline_text` as `Module N: <title> (<n> min)`
headers with `- ` bullets and **no blank-line separators**, so what the instructor
previews is byte-identical to what gets persisted. There is a regression test for
this on the AI-service side (`test_rendered_text_has_no_blank_lines`).

### `outline_text` is rendered, not generated

The model is asked for `modules` only; the AI service flattens them to
`outline_text` in Python. The two halves of the response therefore cannot disagree,
and output tokens drop. This is a deliberate deviation from
`AI_COURSE_OUTLINE_GENERATOR.md` §3, which had the model produce both.

## Environment

| Var | Default | Purpose |
|---|---|---|
| `AI_SERVICES_BASE_URL` | `http://localhost:8001` | Base URL of the FastAPI project |
| `AI_SERVICES_KEY` | `''` | Shared secret; must equal that project's `SERVICE_API_KEY` |
| `AI_OUTLINE_RATE_LIMIT` | `10/min` | Per-user cap on this endpoint |

The first two cover **every** AI feature, not just this one. Adding the next one
(quiz generation, assignment feedback) needs no new Django env var — only a new path
segment. All three carry defaults, so CI needs no change.

The AI service must be reachable **only** from this backend's network, never from the
public internet.

## Tests — `courses/all_tests/test_ai_outline.py`

Two classes, no network:

- `CourseOutlinePreviewAPITests` patches `generate_course_outline` **at its import
  site in the view module** (the house convention) — happy path for instructor and
  institution, field defaulting and forwarding, "nothing persisted", `400` on each
  missing required field and on a bad `level`/`duration_minutes`, `401`, `403` for a
  learner and for an unverified email, `429` via
  `patch.object(AIOutlineThrottle, 'rate', '2/min')`, `503`, and `500` without
  leaking the exception text. Several assert `generate_course_outline` was **not**
  called, because a malformed request must never cost a paid call.
- `AIOutlineServiceClientTests` patches `requests.post` — asserts the URL, the
  `X-Service-Key` header and the `(5, 45)` timeout actually go out, and that
  connection error / timeout / non-200 / malformed JSON each raise
  `AIOutlineError(503)` without echoing the upstream message.

Throttle counters live in the default cache, so `setUp` calls `cache.clear()`.

## Deliberately not built

- **No caching.** Identical requests re-generate. The instructor is iterating, and a
  cached outline would make "Regenerate" a no-op.
- **No streaming.** The frontend waits with a spinner. Streaming would mean an SSE
  surface on both hops for a call that takes a few seconds.
- **No auto-creation of lectures/quizzes inside the generated sections.** Sections
  carry a title and a summary; content authoring stays manual.
- **No per-institution prompt customization.** Would need a prompt template stored on
  `PartnerInstitutionProfile`.
- **`learning_outcomes` are not written to `NidusCourse.learning_objectives`.** They
  are shown in the preview as authoring guidance only. Wiring them to that field
  would silently overwrite something the instructor typed.

## Files

| File | Role |
|---|---|
| `courses/services/ai_outline_service.py` | HTTP client + `AIOutlineError` |
| `courses/all_serializers/ai_serializers.py` | `CourseOutlineRequestSerializer` |
| `courses/all_views/ai_views.py` | `AIOutlineThrottle`, `CourseOutlinePreviewAPIView` |
| `courses/urls.py` | `ai/outline-preview/` — literal-prefixed, declared above the `<slug:slug>/` and `<int:pk>/` routes so neither shadows it |
| `career_college_backend/settings.py` | the three env vars |
| `courses/all_tests/test_ai_outline.py` | tests |

Manual walkthrough: `docs/api-testing/postman-ai-course-outline.md`. Full
three-repo setup: `AI_COURSE_OUTLINE_GENERATOR_SETUP_AND_TESTING.md` at the workspace
root.

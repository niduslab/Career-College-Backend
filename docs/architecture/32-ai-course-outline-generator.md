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
        "estimated_duration_minutes": 90,
        "content_plan": [
          {
            "item_type": "lecture",
            "title": "What machine learning is",
            "description": "Introduces the vocabulary and the two broad families.",
            "estimated_duration_minutes": 20,
            "language": null
          },
          {
            "item_type": "coding",
            "title": "Implement gradient descent",
            "description": "Write the update step for a single-variable model.",
            "estimated_duration_minutes": 30,
            "language": "python"
          }
        ]
      }
    ],
    "outline_text": "Module 1: Foundations of Machine Learning (90 min)\n..."
  }
}
```

`modules` drives the editable preview cards. `outline_text` is the same content
flattened, ready to drop into `course_outline` with no client-side formatting.

## `content_plan` — the items inside a module

Each module carries 1–`MAX_PLAN_ITEMS` (10) planned content items. `item_type` is a
`Literal` of the four types a `CourseSection` can hold, so a model that invents
`"video"` or `"reading"` fails schema validation, gets the corrective retry, and
then a clean `502` — an unknown type can never reach the frontend. `language` is
likewise a `Literal` mirroring `CodingExercise.Language`, and is nulled on any
non-coding item.

**The apply creates these as real but deliberately empty rows** — a lecture with no
video, a quiz with no questions, a coding exercise with no evaluation script, an
assignment with no questions. That is safe *because* each of those blocks
`_validate_course_completeness`: the plan is a to-do list the platform enforces,
not a way to publish a hollow course. Nothing about a generated row is trusted;
the same gates that catch hand-authored gaps catch these.

A planned lecture's `description` has nowhere to land — `Lecture` has no
description column — so it survives only in the preview and in `outline_text`. The
other three types keep it on their own `description` field.

**Token budget — set by the Groq account tier, not the model.** Groq charges
`max_completion_tokens` against the account's tokens-per-minute allowance **up
front**, before generating anything. Ask for more than the tier allows and every
request fails instantly with `413 rate_limit_exceeded`, whatever the model's own
output ceiling is (gpt-oss-120b advertises 65536). The free `on_demand` tier gives
8000 TPM, and the prompt costs ~800, so `LLM_MAX_OUTPUT_TOKENS` is **6500**.

`MAX_MODULES` (10) and `MAX_PLAN_ITEMS` (10) are a **runaway guard, not a promise
the budget covers them**. A pathological 10 × 10 response would need roughly twice
the 6500-token allowance and would truncate mid-object → retry → 502, spending TPM
on the way. What keeps generations inside the budget is the prompt asking for 4–8
items across a sensible number of modules; the schema ceiling only stops a
runaway.

**`LLM_REASONING_EFFORT` is `low`, and that is a budget decision, not a quality
one.** gpt-oss is a reasoning model and its reasoning tokens are billed against
the *same* output budget as the JSON. At `medium` they consumed ~1600 of ~4700
completion tokens while producing an outline of identical shape and quality;
dropping to `low` cut completion tokens to ~2800 and halved latency. Raising it
back re-introduces the truncation failure below on larger courses.

Measured on a 10-hour course at `low`: 5 modules × 6–7 items, 893 prompt + 2812
completion tokens, ~6 s. Roughly 2.3× headroom under the 6500 cap — but the
margin is the model's judgment, not a hard guarantee. A paid tier is what would
make the worst case safe.

### Reading a 503: the response time says which failure it was

Django collapses every upstream failure into one friendly 503, so the timing is
the diagnostic. Confirm against `docker logs career-college-ai-services`.

| Time | Cause | Upstream |
|---|---|---|
| ~0.3 s | The reservation (`prompt + max_completion_tokens`) exceeds the account's **whole** TPM allowance. Misconfiguration, not load — it fails identically on an idle account. | Groq `413`, **not retried** |
| ~10–15 s | Output cap too small for the plan: the model is cut off mid-object and Groq's JSON-mode validator rejects it. | Groq `400 json_validate_failed` |
| +11 s per occurrence | Temporarily over TPM, e.g. a second generation inside the same minute. **The Groq SDK retries this itself** using `Retry-After`, so it self-heals — it costs latency, not a failure. | Groq `429`, retried by the SDK |
| ~2× generation time | Valid JSON that fails the Pydantic schema on both attempts. | `InvalidLLMOutputError` |
| fast | Service down, wrong `AI_SERVICES_BASE_URL`, or key mismatch. | — |

**A truncation gets no corrective retry.** `generate_structured` retries only
`ValueError`/`ValidationError`; a `BadRequestError` is an `APIStatusError`, which
propagates straight out of the loop. So the token headroom above is the only
defence against `json_validate_failed` — there is no second chance.

**The plan must look like a real course, not one item per type.** The prompt
requires *most* items to be short 5–15 minute lectures — one per distinct idea —
with at most one quiz per module and assignment/coding only where the material
calls for it. An earlier version simply enumerated the four types as roles, and
the model dutifully emitted exactly one of each per module, which is not how any
real curriculum is shaped. If the prompt is ever retuned, keep the
lectures-dominate rule and the explicit "do NOT include one of every type".

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
  generation is an LLM call; the AI service's own `LLM_TIMEOUT_SECONDS` (40 s) is
  set **below** 45 s so it gives up first and returns a real status instead of
  leaving Django to time out. 45 s is enough because the account's TPM limit caps
  output at 6500 tokens, so a generation cannot run away.

  > **Deployment:** gunicorn's default `--timeout` is **30 s**, below this 45 s
  > read leg, so it must be raised (60 s is plenty) wherever this runs.
  > `docker-compos..deploy.yml` already passes `--timeout 120`; the command in
  > `Career_College_Backend_AWS_Production_Deployment.md` sets none. An ALB's
  > default 60 s idle timeout is already above 45 s and needs no change.
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
   module via `POST /api/v1/courses/<pk>/sections/create/`, then one content row
   per kept `content_plan` item via `POST /api/v1/courses/sections/<id>/contents/`.
   All sequential — `position` is server-ordered, so a concurrent burst would race
   for slots.
2. **Course outline text:** the edited `outline_text` goes into `course_outline` on
   the normal `POST /api/v1/courses/create/` or `PATCH /api/v1/courses/<pk>/`.

### Content items are best-effort; sections are not

A failed section write stops the run and narrows the draft to the modules that did
not land, so a retry cannot duplicate a saved row. A failed **item** write is
counted and reported instead, and the run continues.

The asymmetry is deliberate. Aborting midway through one module's items would leave
a section that a retry then skips — it is no longer empty — stranding the rest with
no way to resume. Since nothing is ever deleted, the worst case is a partially
built module the instructor finishes by hand, which the toast says out loud.

### Re-applying replaces unfilled shells, never authored work

A reused section is rebuilt against the new plan, but only its **shells** are
cleared: rows whose `is_awaiting_content` is true hold nothing the instructor
made — a lecture with no video, a quiz or assignment with no questions, a coding
exercise with no code. Those are deleted and recreated from the new plan.
Anything with real content stays exactly where it is, whoever created it, and the
toast reports both counts.

That rule is what lets the curriculum track a regenerated outline without
doubling on every apply. The first design skipped reused sections entirely, which
was safe but meant a second apply silently changed only section titles while the
lessons underneath went stale.

`is_awaiting_content` is deliberately **narrower than "incomplete"** for the
non-lecture types. An assignment with one ungradable question still blocks
submission, but it is authored work, so it is *not* awaiting content and is never
deleted. Likewise a coding exercise with starter or solution code but no
evaluation script. The flag answers "would replacing this lose anything?", not
"is this finished?" — conflating the two would destroy work.

The flag is computed by a model property on each of the four types and exposed on
`SectionContentSerializer`. The `/contents/` list prefetches `questions` for
quizzes and assignments so it stays at a constant six queries; a regression test
asserts the count does not move as rows are added.

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
  `AIOutlineError(503)` without echoing the upstream message. The timeout literal
  is pinned on purpose: it must stay above the AI service's own LLM timeout, and
  gunicorn's default is below it.

Throttle counters live in the default cache, so `setUp` calls `cache.clear()`.

## Deliberately not built

- **No caching.** Identical requests re-generate. The instructor is iterating, and a
  cached outline would make "Regenerate" a no-op.
- **No streaming.** The frontend waits with a spinner. Streaming would mean an SSE
  surface on both hops for a call that takes a few seconds.
- **No auto-*authoring* of content.** The apply creates the content rows, but every
  one of them is empty: no AI-written article text, quiz questions, model answers,
  rubrics or evaluation scripts. Each of those must be *complete* to be safe — a
  quiz needs one correct answer per question, a coding exercise needs a script that
  actually runs — and each is its own feature (`AI_SERVICES_PROJECT_STRUCTURE.md`
  reserves `quiz_generation` and `assignment_feedback` folders). Video lectures can
  never be AI-created at all: they need a real uploaded file that finishes
  transcoding.
- **No reordering or renaming of existing content from a regenerated plan.** The
  plan describes work not yet done; it must never touch what is already authored.
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

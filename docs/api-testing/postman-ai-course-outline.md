# Postman Guide — AI Course Outline Generator

Manual API testing for `POST /api/v1/courses/ai/outline-preview/`. The endpoint takes
course metadata and returns an LLM-drafted module-by-module outline. It is a
**suggestion generator**: it writes nothing to the database, so every "did it save?"
check in this guide expects *no* change.

Covers: the happy path, field defaulting, the `IsCourseCreator` gate (instructor
**and** partner institution pass; learner does not), validation failures, the spend
throttle, the AI-service-down path, and the two ways the draft is applied afterwards.

Design reference: `docs/architecture/32-ai-course-outline-generator.md`.
Three-repo setup: `AI_COURSE_OUTLINE_GENERATOR_SETUP_AND_TESTING.md` at the
workspace root.

**Access-denied convention** (project-wide 403-vs-404 rule): the URL carries **no
resource id**, so the only permission failure is **403**. A 404 from this endpoint
means the route is wrong, not that access was denied.

---

## Environment Variables

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `instructor_token` | `Bearer eyJ...` | JWT for an instructor with a **verified email** |
| `institution_token` | `Bearer eyJ...` | JWT for a `partner_institution` user — both roles must pass |
| `learner_token` | `Bearer eyJ...` | JWT for a learner — denial check |
| `unverified_token` | `Bearer eyJ...` | Instructor with `is_email_verified=False` — denial check |
| `course_id` | `42` | PK of a draft course owned by the instructor (apply-the-draft tests) |
| `ai_base_url` | `http://localhost:8001` | The FastAPI AI service, for the direct calls in §2 |
| `ai_service_key` | `dev-shared-secret` | Must equal `AI_SERVICES_KEY` in the backend `.env` |

---

## Prerequisites

1. The AI service is running and reachable from the backend
   (`AI_SERVICES_BASE_URL`), with a working `GROQ_API_KEY`.
2. `AI_SERVICES_KEY` in the backend `.env` equals `SERVICE_API_KEY` in the AI
   service's `.env`. **A mismatch surfaces as a 503 from Django, not a 401** — the
   backend deliberately never forwards the upstream reason.
3. The instructor account's email is verified (`IsEmailVerified` gates the endpoint).
4. Generation takes several seconds. Raise Postman's request timeout if needed; the
   backend's own read timeout is 45 s.

> **Every call costs money.** Each request is a paid LLM call. Work through this
> guide once rather than re-running it in a loop — and note the throttle in §6 will
> stop you anyway.

---

## 1. Happy path

`POST {{base_url}}/courses/ai/outline-preview/`
Header: `Authorization: {{instructor_token}}`

```json
{
  "title": "Introduction to Machine Learning",
  "description": "A hands-on course covering supervised and unsupervised learning.",
  "audience": "Undergraduate CS students\nWorking developers switching into ML",
  "prerequisites": "Python basics\nHigh-school linear algebra",
  "level": "beginner",
  "language": "English",
  "duration_minutes": 600,
  "category": "Data Science"
}
```

Expect `200`:

```json
{
  "success": true,
  "message": "Outline generated.",
  "data": {
    "modules": [
      {
        "title": "...",
        "summary": "...",
        "learning_outcomes": ["..."],
        "topics": ["..."],
        "estimated_duration_minutes": 90
      }
    ],
    "outline_text": "Module 1: ... (90 min)\n..."
  }
}
```

Check:
- `data.modules` has between 1 and 20 entries, each with all five keys.
- `data.outline_text` mentions every module title.
- **`outline_text` contains no blank line.** `NidusCourseCreateUpdateSerializer`
  strips blank lines on save, so a preview containing them would not match what gets
  persisted.
- The outline is written in the requested `language`.

### 1.1 Only the three required fields

Same call with just `title`, `description`, `audience` → `200`. The rest default
(`language` → `English`, `level`/`prerequisites`/`category` → blank,
`duration_minutes` → null).

### 1.2 Steering and regenerating

Add `"extra_instructions": "Focus on hands-on labs, use PyTorch not TensorFlow."` →
`200`, and the modules reflect it. Repeat the identical request → a **different**
outline: nothing is cached, which is what makes the UI's "Regenerate" work.

### 1.3 Partner institutions pass too

Same body with `Authorization: {{institution_token}}` → `200`. The gate is
`IsCourseCreator`, not `IsInstructorUser` — institutions author courses. (The older
rubric-preview endpoint is instructor-only; this one is not.)

---

## 2. The AI service, called directly

Bypasses Django. Useful for isolating "is the model working?" from "is the wiring
working?".

`POST {{ai_base_url}}/v1/course-outline/`
Headers: `Content-Type: application/json`, `X-Service-Key: {{ai_service_key}}`

```json
{
  "title": "Introduction to Machine Learning",
  "description": "A hands-on course covering supervised and unsupervised learning.",
  "audience": "Undergraduate CS students"
}
```

| Call | Expect |
|---|---|
| Correct key | `200`, bare `{modules, outline_text}` — no `success` envelope; that is Django's shape, not the AI service's |
| Wrong key | `401` `{"detail": "Invalid service key."}` |
| No `X-Service-Key` header at all | `401` |
| Missing `title` | `422` (FastAPI/Pydantic validation) |
| `GET {{ai_base_url}}/health` | `200` `{"status":"ok"}`, no key needed |
| `GET {{ai_base_url}}/v1/course-outline/health` | `200`, no key needed |

If the direct call works and the Django call returns 503, the problem is
`AI_SERVICES_BASE_URL` or a key mismatch — not the model.

---

## 3. Validation — `400`

All with `{{instructor_token}}`. Each returns
`{"success": false, "message": "Validation failed.", "errors": {...}}` and, by design,
**never reaches the AI service** — a malformed request must not cost a paid call.

| Body change | `errors` key |
|---|---|
| omit `title` | `title` |
| omit `description` | `description` |
| omit `audience` | `audience` |
| `"level": "wizard"` | `level` |
| `"duration_minutes": -1` | `duration_minutes` |
| `"title"` longer than 255 chars | `title` |

---

## 4. Authentication — `401`

Same body, no `Authorization` header → `401`. The envelope comes from
`core/exception_handlers.py`, so it carries `success`/`message` alongside DRF's
`detail`.

---

## 5. Permissions — `403`

| Token | Expect |
|---|---|
| `{{learner_token}}` | `403` — "Only instructors or partner institutions can perform this action." |
| `{{unverified_token}}` | `403` — "Your email must be verified before accessing this resource." |

Both are `403`, not `404`: no resource id in the URL means there is no existence to
leak.

---

## 6. Throttle — `429`

`AI_OUTLINE_RATE_LIMIT` defaults to `10/min` per user. Send the happy-path request 11
times in a minute; the 11th returns `429`.

To test it cheaply, set `AI_OUTLINE_RATE_LIMIT=2/min` in `.env` and restart the
backend — then the third call trips it without burning 10 LLM calls.

Throttle counters live in the Django cache. Restarting the backend does **not**
necessarily clear them if a shared cache backend is configured.

---

## 7. AI service down — `503`

Stop the AI service (or point `AI_SERVICES_BASE_URL` at a dead port) and repeat the
happy path.

Expect `503`:

```json
{
  "success": false,
  "message": "Outline generation is temporarily unavailable. Please try again."
}
```

Check:
- **No stack trace, and no upstream detail.** A wrong service key, a provider
  outage, a 502 from the model and a malformed body all produce this same message.
  The real cause is in the backend log, not the response.
- The connect leg fails in ~5 s (not 45) when the port is closed.

---

## 8. Applying the draft

Nothing above saved anything. Two ways to apply it, both using endpoints that
already existed.

### 8.1 As curriculum sections (what the frontend does)

For each module you want, `POST {{base_url}}/courses/{{course_id}}/sections/create/`:

```json
{ "title": "<module.title>", "description": "<module.summary>", "position": 1 }
```

Send them **one at a time**, incrementing `position` — `position` is server-ordered
and a concurrent burst races for slots. Then
`GET {{base_url}}/courses/{{course_id}}/sections/` → the sections appear in order.

Applying a *second* outline reuses those rows rather than adding a second batch —
`PATCH {{base_url}}/courses/sections/<section_id>/` with `{"title": ..., "description": ...}`
per already-created section, then `POST .../sections/create/` only for modules beyond
that count. Nothing is deleted, so a reused section keeps its lessons; leftovers from
a shorter outline stay until someone removes them explicitly. The frontend does this
bookkeeping client-side (see `docs/architecture/32-ai-course-outline-generator.md`);
the API itself has no notion of "AI-created".

### 8.2 As the course outline text

`PATCH {{base_url}}/courses/{{course_id}}/`

```json
{ "course_outline": "<edited outline_text>" }
```

Then `GET {{base_url}}/courses/{{course_id}}/` and confirm `course_outline` matches
what you sent. If blank lines come back missing, that is
`_normalize_multiline` doing its job — see §1.

For a `delivery_mode=scheduled` course this field is required before
`POST .../submit/` succeeds; with it filled, submission passes the outline check.

---

## 9. Checklist

- [ ] `200` with all fields, and with only the three required ones
- [ ] `outline_text` covers every module and has no blank lines
- [ ] Repeat request returns a different outline (no caching)
- [ ] `extra_instructions` visibly changes the result
- [ ] Institution token also `200`
- [ ] Direct AI-service call: `200` with the key, `401` without, `422` on bad body
- [ ] `400` for each missing/invalid field
- [ ] `401` unauthenticated
- [ ] `403` learner, `403` unverified email
- [ ] `429` past the throttle
- [ ] `503` with the AI service stopped — friendly message, no stack trace
- [ ] Course count unchanged throughout (nothing auto-saved)
- [ ] Sections created from modules appear via `GET .../sections/`
- [ ] `course_outline` round-trips through `PATCH` + `GET`

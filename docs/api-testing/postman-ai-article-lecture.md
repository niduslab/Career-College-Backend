# Postman Guide — AI Article Lecture Generator

Manual API testing for `POST /api/v1/courses/ai/article-lecture-preview/`. The
endpoint takes one lesson's title plus its surrounding context and returns an
LLM-drafted article body as editor-ready HTML. It is a **suggestion generator**:
it writes nothing to the database, so every "did it save?" check here expects
*no* change.

Covers: the happy path, field defaulting, the `IsCourseCreator` gate, validation
failures, the code opt-in, the spend throttle, the service-down path, and saving
the draft onto a real lecture afterwards.

Design reference: `docs/architecture/34-ai-article-lecture-generator.md`.
Sibling guide: `docs/api-testing/postman-ai-course-outline.md` (same topology and
setup).

**Access-denied convention** (project-wide 403-vs-404 rule): the URL carries **no
resource id**, so the only permission failure is **403**. A 404 from this
endpoint means the route is wrong, not that access was denied.

---

## Environment Variables

| Variable | Example value | Notes |
|---|---|---|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `instructor_token` | `Bearer eyJ...` | Instructor with a **verified email** |
| `institution_token` | `Bearer eyJ...` | `partner_institution` user — both roles must pass |
| `learner_token` | `Bearer eyJ...` | Learner — denial check |
| `unverified_token` | `Bearer eyJ...` | Instructor with `is_email_verified=False` — denial check |
| `lecture_id` | `128` | An article lecture owned by the instructor (§8) |
| `ai_base_url` | `http://localhost:8001` | The FastAPI AI service, for the direct calls in §2 |
| `ai_service_key` | `dev-shared-secret` | Must equal `AI_SERVICES_KEY` in the backend `.env` |

---

## Prerequisites

1. The AI service is running and reachable from the backend
   (`AI_SERVICES_BASE_URL`), with a working `GROQ_API_KEY`.
2. `AI_SERVICES_KEY` in the backend `.env` equals `SERVICE_API_KEY` in the AI
   service's `.env`. **A mismatch surfaces as a 503 from Django, not a 401.**
3. The instructor account's email is verified.
4. Generation takes several seconds. The backend's read timeout is 45 s.

> **Every call costs money.** Each request is a paid LLM call. The throttle in §7
> will stop a loop anyway.

---

## 1. Happy path

`POST {{base_url}}/courses/ai/article-lecture-preview/`
Header: `Authorization: {{instructor_token}}`

```json
{
  "lecture_title": "What a gradient actually measures",
  "course_title": "Introduction to Machine Learning",
  "section_title": "Foundations",
  "description": "Build intuition for gradients before any calculus.",
  "key_points": ["Slope in many dimensions", "Why it points uphill"],
  "audience": "Undergraduate CS students",
  "level": "beginner",
  "language": "English",
  "target_duration_minutes": 6
}
```

**200** — envelope `{success, message: "Article generated.", data}`. In `data`:

| Field | Check |
|---|---|
| `summary` | One opening paragraph |
| `sections[]` | `heading`, `paragraphs[]`, `bullets[]`, `code` (null here — see §5) |
| `takeaways_heading` | "Key takeaways", in the article's language |
| `key_takeaways[]` | 3–6 entries |
| `article_html` | Starts `<p>`; contains every `heading` as `<h2>…</h2>` |
| `word_count`, `estimated_reading_minutes` | Computed from the prose; reading time ≥ 1 |

Paste `article_html` into a browser console with
`document.body.innerHTML = ...` to eyeball it. Only `<h2> <p> <ul> <li> <pre>
<code> <em>` should appear — no `<h1>`, no `<div>`, no `<script>`.

**Verify nothing was written:** re-fetch any lecture you own; `article_content`
is unchanged. Nothing in this call touches the database.

## 2. Direct call to the AI service (bypassing Django)

Useful when Django returns 503 and you need to know which hop failed.

`POST {{ai_base_url}}/v1/article-lecture/`
Headers: `X-Service-Key: {{ai_service_key}}`, `Content-Type: application/json`

```json
{ "lecture_title": "What a gradient actually measures", "target_duration_minutes": 6 }
```

- **200** here but **503** from Django → the two keys differ, or
  `AI_SERVICES_BASE_URL` is wrong.
- **401** here → wrong `X-Service-Key`.
- Probe without a key: `GET {{ai_base_url}}/v1/article-lecture/health` → **200**
  `{"status": "ok", "service": "article-lecture"}`.

## 3. Only the title is required

```json
{ "lecture_title": "Cost functions, plainly" }
```

**200.** Defaults applied server-side: `course_title`/`section_title`/
`description`/`audience`/`level`/`extra_instructions` → `""`, `key_points` →
`[]`, `language` → `"English"`, `target_duration_minutes` → `null`,
`include_code_examples` → `false`.

## 4. Length control

Send the same body three times with `target_duration_minutes` of `3`, `10`, and
omitted. `word_count` should track it (≈180 words per minute), and
`estimated_reading_minutes` should land near what you asked for. Values are
clamped to 250–1600 words upstream, so `120` does not produce a novel.

## 5. Code examples are opt-in

Same body twice, once with `"include_code_examples": true`.

| Request | Expect |
|---|---|
| Default / `false` | Every `sections[].code` is `null`; `article_html` has no `<pre>` |
| `true` | At least one section may carry `code` with a valid `language`; `article_html` contains `<pre><code class="language-…">` |

Try `false` on a programming topic ("List comprehensions in Python") — the code
must still be stripped. The prompt asks; the service enforces.

## 6. Permissions

| Token | Expect |
|---|---|
| `{{instructor_token}}` | **200** |
| `{{institution_token}}` | **200** — `IsCourseCreator`, not `IsInstructorUser` |
| `{{learner_token}}` | **403** |
| `{{unverified_token}}` | **403** (`IsEmailVerified`) |
| none | **401** |

## 7. Validation

| Body | Expect |
|---|---|
| `{}` | **400**, `errors.lecture_title` |
| `{"lecture_title": ""}` | **400** |
| `level: "wizard"` | **400**, `errors.level` |
| `target_duration_minutes: -1` or `500` | **400**, `errors.target_duration_minutes` |
| 13 `key_points` | **400**, `errors.key_points` |

A rejected body never reaches the paid service.

## 8. Throttle

Default `AI_ARTICLE_RATE_LIMIT=10/min`, per user. The 11th call in a minute →
**429**. Lower it in `.env` to test quickly.

It is a **separate counter from `ai_outline`**: exhausting the article throttle
must leave `POST /courses/ai/outline-preview/` still answering 200.

## 9. Service down

Stop the AI service, then repeat §1 → **503**,
`"Article generation is temporarily unavailable. Please try again."` The upstream
reason (connection refused, 401, provider 502) is logged server-side and never
appears in the response.

## 10. Saving the draft

The endpoint saves nothing; the draft reaches the lecture through the ordinary
lecture PATCH:

`PATCH {{base_url}}/courses/lectures/{{lecture_id}}/`

```json
{
  "lecture_type": "article",
  "article_content": "<p>…the article_html from §1…</p>"
}
```

**200.** Then `GET {{base_url}}/courses/lectures/{{lecture_id}}/` and confirm
`article_content` round-tripped. Open the lesson in the course builder: the
rich-text editor should show headings, paragraphs and lists — not escaped tags.
An empty `article_content` on an article lecture is rejected (400) by the
serializer and by `chk_lecture_payload_by_type`.

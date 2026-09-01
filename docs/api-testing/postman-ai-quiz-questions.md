# Postman Guide — AI Quiz Question Generator

Manual API testing for `POST /api/v1/courses/ai/quiz-questions-preview/` and the
apply endpoint behind it, `POST /api/v1/courses/quizzes/<id>/questions/bulk/`.
The preview takes a quiz id and returns LLM-drafted multiple-choice questions
grounded in that section's lectures. It is a **suggestion generator**: it writes
nothing, so every "did it save?" check in §1–§9 expects *no* change. §10 is the
only call that writes.

Covers: the happy path, grounding, the dedupe behaviour, the `IsCourseCreator`
gate, validation failures, the spend throttle, the service-down path, and the
transactional bulk apply.

Design reference: `docs/architecture/35-ai-quiz-question-generator.md`.
Sibling guides: `docs/api-testing/postman-ai-article-lecture.md`,
`docs/api-testing/postman-ai-course-outline.md` (same topology and setup).

**Access-denied convention** (project-wide 403-vs-404 rule): unlike its two
sibling AI endpoints, this one is keyed to a **numeric quiz id**. A quiz the
caller does not own returns **404**, not 403 — confirming the quiz exists would
let someone probe sequential ids. A **403** here means the *role* was rejected
(learner, or unverified email), before any quiz was looked up.

---

## Environment Variables

| Variable | Example value | Notes |
|---|---|---|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `instructor_token` | `Bearer eyJ...` | Instructor with a **verified email** |
| `institution_token` | `Bearer eyJ...` | `partner_institution` user — both roles must pass |
| `learner_token` | `Bearer eyJ...` | Learner — denial check |
| `unverified_token` | `Bearer eyJ...` | Instructor with `is_email_verified=False` — denial check |
| `quiz_id` | `41` | A quiz owned by the instructor, in a section with at least one **article** lecture |
| `bare_quiz_id` | `42` | A quiz in a section with **no** article lectures (§3) |
| `foreign_quiz_id` | `43` | A quiz the instructor does **not** own (§7) |
| `ai_base_url` | `http://localhost:8001` | The FastAPI AI service, for the direct calls in §2 |
| `ai_service_key` | `dev-shared-secret` | Must equal `AI_SERVICES_KEY` in the backend `.env` |

---

## Prerequisites

1. The AI service is running and reachable from the backend
   (`AI_SERVICES_BASE_URL`), with a working `GROQ_API_KEY`.
2. `AI_SERVICES_KEY` in the backend `.env` equals `SERVICE_API_KEY` in the AI
   service's `.env`. **A mismatch surfaces as a 503 from Django, not a 401.**
3. The instructor account's email is verified.
4. `{{quiz_id}}` sits in a section that has at least one saved **article**
   lecture. Without written content the questions come from titles alone and
   §1's `grounded` check flips — that case is §3.
5. Generation takes several seconds. The backend's read timeout is 45 s.

> **Every call costs money.** Each request is a paid LLM call. The throttle in §8
> will stop a loop anyway.

---

## 1. Happy path

`POST {{base_url}}/courses/ai/quiz-questions-preview/`
Header: `Authorization: {{instructor_token}}`

```json
{
  "quiz_id": {{quiz_id}},
  "question_count": 5,
  "options_per_question": 4,
  "difficulty": "understanding"
}
```

**200** — envelope `{success, message: "Questions generated.", data}`. In `data`:

| Field | Check |
|---|---|
| `questions[]` | Up to `question_count` entries |
| `questions[].question_text` | Plain text — no `A)` prefix, no markdown |
| `questions[].options[]` | 2–5 entries; **exactly one** has `is_correct: true` |
| `questions[].explanation` | Present, for the reviewer only — never saved |
| `questions[].difficulty` | One of `recall`, `understanding`, `application` |
| `grounded` | `true` — the section has article content |
| `requested_count` | Echoes what you asked for |

Read a few questions against the lecture text: each should be answerable from it
alone, and the wrong options should be wrong for a *reason*, not filler.

**Verify nothing was written:**

`GET {{base_url}}/courses/quizzes/{{quiz_id}}/questions/` — the count is
unchanged from before the call.

---

## 2. Direct call to the AI service (bypassing Django)

Useful for separating "the model is bad" from "the wiring is bad". Django is
what assembles `source_material`; calling directly means supplying it yourself.

`POST {{ai_base_url}}/v1/quiz-questions/`
Header: `X-Service-Key: {{ai_service_key}}`

```json
{
  "quiz_title": "Gradients checkpoint",
  "source_material": "A gradient is the vector of partial derivatives. It points in the direction of steepest increase, so gradient descent steps against it. The learning rate scales the size of that step.",
  "question_count": 3,
  "options_per_question": 4,
  "difficulty": "application"
}
```

**200** — a bare `{questions, grounded, requested_count}` with no envelope; the
success/message wrapper is Django's.

Health probe, no key needed:

`GET {{ai_base_url}}/v1/quiz-questions/health` → `{"status": "ok", "service":
"quiz-questions"}`

Same call with no `X-Service-Key`, or a wrong one → **401**, even if the body is
malformed: the credential check runs before validation.

---

## 3. Grounding — a section with no written lectures

`POST {{base_url}}/courses/ai/quiz-questions-preview/` with
`{"quiz_id": {{bare_quiz_id}}}`.

**200**, but `data.grounded` is **false**. The questions were written from lesson
titles alone. The UI leads with a warning in this case; the endpoint does not
refuse, because an instructor who knows the subject can still use the draft.

`grounded` is decided by Django, not the AI service — it reflects whether the
section actually has article content, not merely whether text was sent upstream.

---

## 4. Only `quiz_id` is required

Send `{"quiz_id": {{quiz_id}}}` and nothing else. **200**, with these defaults
applied server-side:

| Field | Default |
|---|---|
| `question_count` | `5` |
| `options_per_question` | `4` |
| `difficulty` | `understanding` |
| `topics`, `avoid_questions` | `[]` |
| `extra_instructions` | `""` |

Everything about the quiz itself — its title and description, the course and
module titles, the audience, level and language, and the lecture text — is
resolved from `quiz_id`. The browser never sends any of it.

---

## 5. Difficulty and shape

| Request | Expect |
|---|---|
| `"difficulty": "recall"` | Questions answerable by remembering a stated fact |
| `"difficulty": "application"` | Questions that describe a short case and ask which outcome follows |
| `"options_per_question": 2` | True/false-shaped questions |
| `"question_count": 15` | Up to 15 — the ceiling |
| `"extra_instructions": "focus on the learning rate"` | Visibly narrower subject matter |

Run the same body twice: the questions differ. Nothing is cached — that is what
makes *Regenerate* work.

---

## 6. Dedupe — `requested_count` vs. what comes back

Apply some questions first (§10), then ask again with the same settings.

**200**, and `data.questions.length` may be **less than**
`data.requested_count`. Anything repeating a question the quiz already has is
dropped server-side; the quiz's existing questions are collected automatically,
so you never send them. An empty `questions` array is a valid response, not an
error.

To simulate the frontend's regenerate, pass the drafts still on screen:

```json
{"quiz_id": {{quiz_id}}, "avoid_questions": ["What does a gradient point towards?"]}
```

That exact question — and close rewordings of it — must not come back.

---

## 7. Permissions

`POST {{base_url}}/courses/ai/quiz-questions-preview/` with `{"quiz_id": …}`:

| Token | quiz | Expect |
|---|---|---|
| `instructor_token` | `{{quiz_id}}` | **200** |
| `institution_token` | a quiz in its own course | **200** — `IsCourseCreator`, institutions author too |
| `learner_token` | any | **403** — role rejected before any lookup |
| `unverified_token` | any | **403** — `IsEmailVerified` |
| *(none)* | any | **401** |
| `instructor_token` | `{{foreign_quiz_id}}` | **404** — not 403; see the convention note above |
| `instructor_token` | `999999` | **404** — identical body to the line above |

The last two responses must be indistinguishable. A different message for "not
yours" would defeat the point of the 404.

---

## 8. Validation

| Body | Expect |
|---|---|
| `{}` | **400**, `errors.quiz_id` |
| `{"quiz_id": {{quiz_id}}, "question_count": 0}` | **400**, `errors.question_count` |
| `{"quiz_id": {{quiz_id}}, "question_count": 16}` | **400** |
| `{"quiz_id": {{quiz_id}}, "options_per_question": 6}` | **400** |
| `{"quiz_id": {{quiz_id}}, "difficulty": "impossible"}` | **400**, `errors.difficulty` |
| `{"quiz_id": {{quiz_id}}, "avoid_questions": [ …31 strings… ]}` | **400** |

**A rejected body never reaches the paid service** — validation runs before the
quiz lookup and before the LLM call.

---

## 9. Throttle and service failures

`AI_QUIZ_RATE_LIMIT` defaults to `10/min` per user. The 11th call in a minute
returns **429**. It is its own counter: exhausting it must **not** block
`POST ai/outline-preview/` or `POST ai/article-lecture-preview/` — check one of
those still returns 200 straight afterwards.

A **503 on a large request specifically** — 15 questions with 5 options, while
small ones succeed — used to mean a Groq **413** upstream: prompt tokens and the
reserved output are both charged against the account's per-minute allowance. The
service now sizes itself against `LLM_TOKENS_PER_MINUTE`, so if this reappears,
that setting is higher than the real Groq tier. Check the AI service log for
`status=413`.

Stop the AI service (`docker compose stop`) and repeat §1:

**503** — `{"success": false, "message": "Question generation is temporarily
unavailable. Please try again."}`

Every upstream failure looks like this one: unreachable service, key mismatch,
provider error, unparseable body. The real reason is in the Django log and
never in the response.

---

## 10. Applying the draft — the only call that writes

`POST {{base_url}}/courses/quizzes/{{quiz_id}}/questions/bulk/`
Header: `Authorization: {{instructor_token}}`

```json
{
  "questions": [
    {
      "question_text": "What does a gradient point towards?",
      "options": [
        {"answer_text": "The direction of steepest increase", "is_correct": true},
        {"answer_text": "The direction of steepest decrease", "is_correct": false},
        {"answer_text": "The nearest minimum", "is_correct": false}
      ]
    }
  ]
}
```

**201** — `{success, message: "1 question(s) added successfully.", data}`, where
`data` is the created questions **with their answers**, so the builder needs no
follow-up request.

Checks:

| Check | Expect |
|---|---|
| `GET .../quizzes/{{quiz_id}}/questions/` | The new questions, appended after the existing ones |
| `data[].position` | Continues from the quiz's previous maximum |
| Existing questions | Unchanged — apply appends, never replaces or reorders |

Rejections — each returns **400** and writes **nothing**:

| Body | Expect |
|---|---|
| Two options with `is_correct: true` | **400** — the DB allows one correct answer per question |
| No option with `is_correct: true` | **400** — such a question blocks course submission |
| Two options with the same text | **400** |
| 1 option, or 6 | **400** — the range is 2–5 |
| An `answer_text` over 500 characters | **400** — matches the column |
| 21 questions | **400** — the per-call cap is 20 |

Other statuses:

| Case | Expect |
|---|---|
| `{{foreign_quiz_id}}` | **404** |
| A course not in `draft`/`rejected` (and outside the cohort carve-out) | **422** |
| Two applies racing for the same positions | **409**, with nothing half-written |

Finally, confirm the invariants generated content earns no exemption from:

1. Submit the course for review — a quiz built entirely this way passes
   completeness validation.
2. `GET {{base_url}}/courses/learn/<slug>/quizzes/<id>/` as an enrolled learner —
   the answer options carry **no** `is_correct` field at all.

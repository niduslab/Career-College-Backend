# Postman Guide — AI Coding Exercise Generator

Manual API testing for `POST /api/v1/courses/ai/coding-exercise-preview/` and the
verification runs behind it. The preview takes an exercise id and returns a
complete generated problem — description, starter code, reference solution and
evaluation script. It is a **suggestion generator**: it writes nothing and runs
nothing, so every "did it save?" check in §1–§8 expects *no* change.

Design reference: `docs/architecture/36-ai-coding-exercise-generator.md`.
Sibling guides: `postman-ai-quiz-questions.md`, `postman-ai-article-lecture.md`.

**Access-denied convention**: keyed to a numeric `exercise_id`, so an exercise
the caller does not own returns **404**, not 403. A **403** means the *role* was
rejected (learner, or unverified email) before any lookup.

---

## Environment Variables

| Variable | Example value | Notes |
|---|---|---|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `instructor_token` | `Bearer eyJ...` | Instructor with a **verified email** |
| `institution_token` | `Bearer eyJ...` | `partner_institution` user |
| `learner_token` | `Bearer eyJ...` | Denial check |
| `unverified_token` | `Bearer eyJ...` | `is_email_verified=False` — denial check |
| `exercise_id` | `57` | A **python** exercise owned by the instructor, in a module with an article lecture |
| `java_exercise_id` | `58` | A **java** exercise — §3 |
| `foreign_exercise_id` | `59` | An exercise the instructor does not own |
| `ai_base_url` | `http://localhost:8001` | The FastAPI AI service, for §2 |
| `ai_service_key` | `dev-shared-secret` | Must equal `AI_SERVICES_KEY` in the backend `.env` |

---

## Prerequisites

1. The AI service is running with a working `GROQ_API_KEY`, and
   `AI_SERVICES_KEY` matches its `SERVICE_API_KEY`. **A mismatch surfaces as a
   503 from Django, not a 401.**
2. **Celery and Docker are running**, with the runner images pulled — §5 and §6
   execute real containers. Without them the generation still works and
   verification reports *unknown*.
3. `{{exercise_id}}` has a title (the panel is disabled without one) and sits in
   a module with at least one saved article lecture.

> **Every generate costs money.** Each is a paid LLM call; §7's throttle will
> stop a loop.

---

## 1. Happy path

`POST {{base_url}}/courses/ai/coding-exercise-preview/`
Header: `Authorization: {{instructor_token}}`

```json
{
  "exercise_id": {{exercise_id}},
  "difficulty": "core",
  "topic_hint": "list iteration"
}
```

**200** — envelope `{success, message: "Exercise generated.", data}`. In `data`:

| Field | Check |
|---|---|
| `description` | States the exact function name, parameters and return value |
| `starter_code` | A signature plus a TODO — must **not** solve it |
| `solution_code` | A working implementation, different from the starter |
| `evaluation_script` | A stdlib `unittest` module importing from `exercise` |
| `test_names[]` | 3–8 plain-English lines |
| `language` | `python` — from the stored exercise |
| `grounded` | `true` |

**Verify nothing was written:**

`GET {{base_url}}/courses/coding-exercises/{{exercise_id}}/` — `starter_code`,
`solution_code` and `evaluation_script` are unchanged.

---

## 2. Direct call to the AI service

`POST {{ai_base_url}}/v1/coding-exercise/`
Header: `X-Service-Key: {{ai_service_key}}`

```json
{
  "exercise_title": "Sum a list",
  "language": "python",
  "difficulty": "core",
  "time_limit_ms": 2000
}
```

**200** — a bare object with no envelope. Health probe, no key needed:
`GET {{ai_base_url}}/v1/coding-exercise/health` →
`{"status": "ok", "service": "coding-exercise"}`.

Same call with a wrong `X-Service-Key` → **401**, even with a malformed body.

---

## 3. The language comes from the exercise

Generate against `{{java_exercise_id}}` and send `"language": "python"` in the
body as well.

**200**, and `data.language` is **java** — the request field is ignored. The
`evaluation_script` declares `public class Evaluate` with `test*` methods and
uses no JUnit. Sending the language would let the client pick a contract the
sandbox will not honour, so it is read from the stored row.

Repeat for each language and check the script shape:

| Language | The script must contain |
|---|---|
| `python` | `import unittest`, `from exercise import ...` |
| `javascript` | `require('./exercise')`, `test(` — no `describe(` |
| `java` | `class Evaluate`, `public void test...` — no `@Test` |
| `cpp` | `#include "exercise.h"`, `#include "testkit.h"`, `TEST(` — no `<bits/stdc++.h>` |

A reply in the wrong shape never reaches you: it fails validation upstream,
costs the corrective retry, and becomes a 502 → 503.

---

## 4. Only `exercise_id` is required

Send `{"exercise_id": {{exercise_id}}}`. **200**, with `difficulty` defaulting to
`core`, `topic_hint` and `extra_instructions` to `""`, `avoid_titles` to `[]`.

---

## 5. Verification — the point of the feature

Two runs against the endpoint that already exists. Neither saves anything.

**Solution must pass:**

`POST {{base_url}}/courses/coding-exercises/{{exercise_id}}/run/`

```json
{
  "code": "<data.solution_code from §1>",
  "evaluation_script": "<data.evaluation_script from §1>",
  "mode": "tests"
}
```

**202** `{task_id}`. Poll
`GET {{base_url}}/courses/learn/coding-exercises/tasks/{{task_id}}/` until
`state` is `SUCCESS`. Expect `result.status = "passed"` and
`passed_tests == total_tests`.

**Starter must fail:** the same call with `code` set to `starter_code`. Expect
`result.status` **not** `"passed"`, with real per-test results. A stub returning
`None` errors inside each test (`TypeError`, not an assertion failure) — that is
the healthy shape, and `status` is `error` rather than `failed`.

What must **not** happen is a single result named `evaluate (load)` or
`evaluation`: that means the starter never compiled, so the learner would open a
broken file. The UI treats it as **Not verified** even though the run did not
pass. Worth checking explicitly on a Java or C++ exercise, where a `// TODO`
stub with no `return` does not build.

Then confirm the exercise is still untouched:
`GET .../coding-exercises/{{exercise_id}}/` shows empty code fields.

---

## 6. Verification failures worth reproducing

| Change | Expect |
|---|---|
| Break the solution (delete its `return`) and re-run | Solution run `failed`; the UI verdict is **Not verified** |
| Set `code` to the solution for *both* runs | Starter run passes; verdict **Not verified**, "nothing for the learner to do" |
| Stop Docker (`docker compose stop`) and run | The task errors; verdict **Unknown**, and accepting is still allowed behind a second click |

---

## 7. Permissions, validation and throttle

| Token / body | Expect |
|---|---|
| `instructor_token`, own exercise | **200** |
| `institution_token`, its own exercise | **200** |
| `learner_token` | **403** |
| `unverified_token` | **403** |
| *(none)* | **401** |
| `instructor_token`, `{{foreign_exercise_id}}` | **404** |
| `instructor_token`, `999999` | **404** — identical body to the line above |
| `{}` | **400**, `errors.exercise_id` |
| `"difficulty": "impossible"` | **400** |
| 11 `avoid_titles` | **400** |

`AI_CODING_RATE_LIMIT` defaults to `10/min` per user; the 11th call returns
**429**. It is its own counter — exhausting it must not block
`ai/outline-preview/`, `ai/article-lecture-preview/` or
`ai/quiz-questions-preview/`.

Stop the AI service and repeat §1: **503**, `"Exercise generation is temporarily
unavailable. Please try again."` Every upstream failure looks identical; the real
reason is in the Django log only.

---

## 8. Accepting the draft — the only call that writes

`PATCH {{base_url}}/courses/coding-exercises/{{exercise_id}}/`

```json
{
  "description": "…",
  "starter_code": "…",
  "solution_code": "…",
  "evaluation_script": "…"
}
```

**200**. Then check the end-to-end result:

1. `GET .../coding-exercises/{{exercise_id}}/` — all four fields persisted.
2. Submit the course for review — it passes completeness validation.
3. As an enrolled learner,
   `GET {{base_url}}/courses/learn/coding-exercises/{{exercise_id}}/` — the
   payload contains **neither** `solution_code` **nor** `evaluation_script`.
4. Submit the starter code as that learner — it fails, exactly as §5 predicted.

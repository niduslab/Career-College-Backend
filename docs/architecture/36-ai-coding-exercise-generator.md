# 36 — AI Coding Exercise Generator

LLM-drafted programming exercises. The instructor opens the Coding Exercise
Builder, picks a difficulty, clicks *Generate exercise*, and gets a complete
problem — description, starter code, reference solution and evaluation script —
**already executed in the sandbox** so they can see it works before keeping it.

Fourth AI service, after the course outline generator (32), the article-lecture
writer (34) and the quiz-question generator (35). Same topology, same trust
model, same never-persist rule on the preview endpoint — read 32 first; this doc
covers only what differs.

Repos: `Career-College-AI-Services` (`app/coding_exercise_generator/`),
`Career-College-Backend` (`courses`), `Career-College-Frontend`
(`coding-ai-panel.tsx`, `coding-preview-modal.tsx`). **No migration**, and **no
new write or verification endpoint** — accepting is the `PATCH
coding-exercises/<id>/` the builder already saves through, and
`POST coding-exercises/<id>/run/` already accepts `code` and
`evaluation_script` overrides, so an unsaved draft executes as-is.

| Layer | File |
|---|---|
| Preview endpoint | `courses/all_views/ai_views.py` → `CodingExercisePreviewAPIView` |
| Body validation | `courses/all_serializers/ai_serializers.py` → `CodingExerciseRequestSerializer` |
| Grounding | `courses/services/section_context_service.py` → `build_section_source_material` |
| HTTP client | `courses/services/ai_coding_service.py` → `generate_coding_exercise`, `AICodingError` |
| Generation | `app/coding_exercise_generator/{schemas,prompts,generator,router}.py` |
| Frontend | `.../course-builder/coding-ai-panel.tsx` + `coding-preview-modal.tsx` |

---

## 1. Why this one is verified rather than just reviewed

A quiz question needs a human to judge whether it is fair. A coding exercise
either runs or it does not, and the platform can find out in about a second —
so it does, before the instructor is asked anything.

That matters more here than anywhere else in the AI work, because
**a broken evaluation script does not block course submission.**
`_validate_course_completeness` only checks the field is non-empty:

```python
if not (exercise.evaluation_script or '').strip():
```

A script that raises on import therefore publishes cleanly and fails for the
first learner who clicks Submit, as an `evaluate (load)` error carrying a
traceback. Nothing downstream catches it. (Docs 34 and 35 both said such a
script "blocks submission" — it does not; that claim is corrected here.)

### The two runs

Both go through the endpoint that already exists, with `mode='tests'`:

| Run | Code sent | Must be |
|---|---|---|
| Solution | `solution_code` | every test **passed** — it is solvable and the script runs |
| Starter | `starter_code` | tests actually ran and at least one did **not pass** |

The second check catches the most common worthless result: starter code that
already passes, which looks complete and is not an exercise.

**"Did not pass" is not the same as "did not run."** A stub that returns `None`
errors inside each test — a `TypeError`, not an assertion failure — and that is
the healthy shape. But a starter that fails to *load or compile* also produces a
non-passing run, and would sail through a naive check while handing the learner
a file that is already broken. Every harness names that case `evaluate (load)`
(Django synthesises `evaluation` when the container emits nothing at all), so a
starter run consisting only of those markers is **not** verified, and the banner
says which of the three failures it is. Java and C++ are where this bites: a
`// TODO` stub with no `return` does not compile. The prompt therefore requires
a starter that builds and fails, returning a placeholder where the language
needs one.

**The loop lives in the browser.** The review modal dispatches both runs and
polls `getCodingTaskStatus`, exactly as the builder's Run tests button does.
Django stays one fast request per AI call like its three siblings — folding an
LLM call plus two container runs into a single request would put ~10 s of work
behind a 45 s read timeout and hold a worker for all of it.

**Degradation.** If the runner is unavailable the verdict is *unknown*, shown as
a plain warning, and accepting still works behind a second click. A draft nobody
could verify is worth less than one that passed and more than nothing. What must
never appear is a verified badge on an unverified draft.

---

## 2. The per-language script contract

The harness is injected and the filenames are fixed, so a script in the wrong
shape is syntactically fine and registers **zero tests**:

| Language | Learner file | The script must be |
|---|---|---|
| `python` | `exercise.py`, importable as `exercise` | a stdlib `unittest` module |
| `javascript` | `exercise.js`, CommonJS | `require('./exercise')` + `node:assert`, tests via the injected global `test(name, fn)` |
| `java` | `Exercise.java` | `public class Evaluate`, public no-arg `test*` methods, failing by `AssertionError` |
| `cpp` | `exercise.h` | `#include "exercise.h"` + `"testkit.h"`, `TEST(name) { ASSERT_EQ(...); }`, never `<bits/stdc++.h>` |

Three defences, in order:

1. **The prompt sends only the target language's contract** and a worked
   skeleton. Showing all four invites the model to blend them.
2. **`GeneratedExercise` validates the shape** — required markers present, no
   pytest/Jest/JUnit/gtest, no `<bits/stdc++.h>`, and `starter_code` differing
   from `solution_code`. These are string checks; they exist so the obvious
   failures cost a retry rather than a container. The validators read the
   requested language from the validation context `generate_structured` now
   forwards to `model_validate`.
3. **The sandbox runs it** (§1). That is what actually settles it.

`language` is read from the stored exercise, never the request body — it decides
which contract applies, so a client-supplied one is ignored.

---

## 3. The endpoint

`POST /api/v1/courses/ai/coding-exercise-preview/` —
`[IsAuthenticated, IsEmailVerified, IsCourseCreator]`, throttled by
`AICodingThrottle` (`scope='ai_coding'`, `AI_CODING_RATE_LIMIT`, default
`10/min`, its own counter for the same reason as the other three).

Body: `exercise_id` required; `difficulty` (`intro`/`core`/`challenge`, default
`core`), `topic_hint`, `avoid_titles` (≤ 10) and `extra_instructions` optional.
Everything else — the title, the language, the module's lecture text, the
course's audience/level/language, and `time_limit_ms` — is resolved from the id.

Response `data`: `description`, `starter_code`, `solution_code`,
`evaluation_script`, `test_names[]`, `language`, `difficulty`, `grounded`.

**It reads the database, writes nothing, and runs nothing.** Denial is a **404**
for an exercise the caller does not own, per the identifier-type rule — the same
scoping (`_owned_exercise_qs`) every other coding view uses.

Grounding is `build_section_source_material`, shared with the quiz generator
rather than duplicated; `grounded` is decided in Django, which knows whether the
module actually has written lectures, not merely whether text was sent upstream.

---

## 4. The token budget

Four code blocks plus a description is the largest reply any service here
produces, so — following the quiz generator — this one sizes itself against
`LLM_TOKENS_PER_MINUTE` instead of inheriting `LLM_MAX_OUTPUT_TOKENS`. Groq
reserves prompt tokens *and* `max_completion_tokens` against one per-minute
allowance before generating and rejects the pair with a 413.

`wanted_output_tokens()` estimates a **realistic** reply from the difficulty
(1 800 / 2 400 / 3 200 tokens) plus 600 for Java and C++, which say the same
thing in more lines, plus reasoning headroom. `MAX_CODE_CHARS = 4000` stays an
inbound ceiling rather than a budgeting figure — four blocks at that size would
be ~4 000 output tokens on its own, and no request needs to reserve that.

Measured on the free 8 000 TPM tier, the worst case (Java, challenge, every
inbound field at its cap) reserves 4 581 output tokens against a 3 019-token
prompt — 7 600 total — and still keeps 5 819 characters of lecture text.
`tests/test_coding_generator.py` asserts the sum for every language/difficulty
pair, so the guard cannot silently rot.

---

## 5. Frontend

`coding-ai-panel.tsx` sits above the code editors: difficulty, an optional topic
hint, and Generate. It names the target language rather than offering a second
selector — the form above already sets it, and it is the one input that cannot
change afterwards without invalidating the script.

`coding-preview-modal.tsx` is the accept step. Editable description plus three
tabbed code panes, verification run automatically on arrival, and the verdict
banner above everything. Editing any pane marks the verdict stale and offers a
re-run — an edited exercise is no longer the one that was verified. Accepting on
anything other than **Verified** needs a second click; nobody is blocked from
saving work they intend to fix by hand.

Applying **overwrites** this exercise's four content fields, which the modal says
plainly when there is already code in them. That is the opposite of the quiz
feature's append, and deliberately so: one exercise is one row.

`RunResultPanel` was extracted from the builder into
`coding-run-result-panel.tsx` so both render per-test rows identically.

---

## 6. Deliberately not built

- **Auto-apply.** A verified exercise is proven *runnable*, not proven *good* —
  nothing checks the problem is worth setting.
- **Verification stored on the row.** Requiring a passing run at submission time
  means a migration, a staleness rule and a background job. §8.4 of the proposal
  covers it; a cheaper independent win is requiring `solution_code` alongside
  `evaluation_script` in `_validate_course_completeness`.
- **Generating a script for an exercise that already has a solution.** Useful and
  much smaller, but a different prompt with a different verification.
- **Multi-language exercises.** `CodingExercise` is single-language by design.
- **Anything that relaxes the sandbox** to accommodate generated code. The time
  limit, the memory cap and the no-network rule are learner-facing safety
  properties; a generated exercise that needs them loosened should be rejected.

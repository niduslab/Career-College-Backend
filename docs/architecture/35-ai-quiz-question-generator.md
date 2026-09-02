# 35 — AI Quiz Question Generator

LLM-drafted multiple-choice questions, grounded in the lessons the quiz sits
beside. The instructor opens Quiz Builder, sets a count and a difficulty, clicks
*Generate questions*, then reviews every question — editing text, moving the
correct answer, unticking what they don't want — before anything is written.
Accepting writes real `QuizQuestion` + `QuizAnswer` rows through one bulk call.

Third service in the `Career-College-AI-Services` project, after the course
outline generator (`32-ai-course-outline-generator.md`) and the article-lecture
writer (`34-ai-article-lecture-generator.md`). Same topology, same trust model,
same never-persist rule on the preview endpoint — read 32 first; this doc covers
only what differs.

Repos: `Career-College-AI-Services` (`app/quiz_question_generator/`),
`Career-College-Backend` (`courses`), `Career-College-Frontend`
(`quiz-ai-panel.tsx`, `quiz-preview-modal.tsx`). **No migration** — nothing in
the data model changes.

---

## 1. Topology

```
Browser ──JWT──▶ Django  ──X-Service-Key──▶ AI services ──▶ Groq
        (quiz builder)   POST /v1/quiz-questions/
```

One-directional as before: the frontend never calls the AI service, the AI
service never touches this database, and `GROQ_API_KEY` never exists in this
repo. `AI_SERVICES_BASE_URL` / `AI_SERVICES_KEY` are unchanged — a new AI
feature adds a URL path, not an env var.

| Layer | File |
|---|---|
| Preview endpoint | `courses/all_views/ai_views.py` → `QuizQuestionsPreviewAPIView` |
| Body validation | `courses/all_serializers/ai_serializers.py` → `QuizQuestionsRequestSerializer` |
| Grounding + write | `courses/services/quiz_service.py` → `build_quiz_source_material`, `collect_avoid_questions`, `bulk_create_quiz_questions` |
| HTTP client | `courses/services/ai_quiz_service.py` → `generate_quiz_questions`, `AIQuizError` |
| Apply endpoint | `courses/all_views/content_views.py` → `QuizQuestionBulkCreateAPIView` |
| Generation | `app/quiz_question_generator/{schemas,prompts,generator,router}.py` |
| Frontend client | `src/lib/course-api/ai.ts` → `generateQuizQuestions`; `authoring.ts` → `bulkCreateQuizQuestions` |
| Frontend UI | `.../course-builder/quiz-ai-panel.tsx` + `quiz-preview-modal.tsx`, mounted by `quiz-builder.tsx` |

---

## 2. The one correct answer

`QuizAnswer` carries `uniq_correct_answer_per_question`, a partial unique on
`(question) WHERE is_correct`. There is no multi-select and no "select all that
apply": supporting either is a data-model change, not a prompt change.

So a generated question with two correct options is not a degraded result — it
is **unsavable**, and it must never reach the instructor as a draft they can
accept. Three layers say so, in order:

1. **The AI service rejects it** (`GeneratedQuestion` validators). A bad reply
   costs the corrective retry and then a clean 502; it never becomes a 200.
2. **The bulk serializer rejects it** — the accept step validates independently,
   because the modal's questions have been edited by a human since generation.
3. **The database rejects it.** `bulk_create` skips `Model.clean()`, so the
   constraint is what actually backs the rule.

The review UI cannot express the invalid state at all: the correct answer is a
radio, so exactly one is always selected. Removing the selected option promotes
the first survivor rather than leaving the question unanswerable.

True/false is a two-option question. Nothing else needs a new type.

---

## 3. Grounding: Django assembles the material, not the browser

`POST ai/quiz-questions-preview/` takes a **`quiz_id`**, and
`build_quiz_source_material` builds the prompt's source text from the quiz's own
section:

- the section's `description`,
- every lecture in `SectionContent.position` order — article lectures contribute
  `article_content` flattened to plain text, video lectures contribute their
  title (weak grounding, but honest about what the module covers).

Capped at **8 000 characters**, cut at a paragraph boundary with a marker.
Stopping mid-sentence invites a question about a fact the material no longer
finishes stating. That is the ceiling on what Django *sends*; how much actually
reaches the prompt is decided upstream by the token budget in §3a.

`avoid_questions` is filled the same way, from the quiz's existing
`question_text` values, so the instructor never supplies it. The frontend adds
only the draft questions still on screen — unsaved, so the server cannot see
them, and without them a regenerate repeats the first run.

Why server-side: the builder holds one section's content list, article bodies
are fetched lazily one at a time, and letting the browser post lecture text
means the client decides what the model is grounded in. Django owns the data.

Two consequences of taking an id, both deliberate:

- **Denial is 404, not 403.** Ownership is scoped exactly like every other quiz
  view (`course_owner_q(request.user, 'section__course')` + `.distinct()`), and
  a quiz the caller does not own is indistinguishable from one that does not
  exist. That is the identifier-type rule working, not an inconsistency with the
  two sibling AI endpoints — they take no id, so theirs is 403.
- **It reads the database and still writes nothing.** Reading for context is not
  persisting a suggestion.

### When there is nothing to ground on

A section with no article lectures yields titles only. The response carries
`grounded: false` and the review modal leads with a plain warning: these were
written from titles alone, check every one. Do not hide it, and do not refuse
either — an instructor who knows the subject can still use the draft.

`grounded` is decided in Django, not upstream: the AI service only knows whether
it was *given* material, while this side knows whether there is written lecture
content behind it.

---

## 3a. The token budget, and why this service alone needs one

**Groq reserves prompt tokens *and* `max_completion_tokens` against the account's
tokens-per-minute allowance before generating**, and rejects the request with a
**413** — having produced nothing — when the two together exceed it. The AI
service maps that to a 502, and Django to a 503.

The first two AI services never meet this. Their prompts are ~800 tokens, so
800 + the configured 6 500 output sits under the free tier's 8 000 TPM. The quiz
service is the first with a *large* prompt: lecture text, a list of questions to
avoid, topics, audience. Reserving the global output ceiling on top of that
exceeded the allowance on every call.

So the two halves negotiate, in `app/quiz_question_generator/schemas.py`:

- `wanted_output_tokens(req)` estimates what the requested questions and options
  would cost — a **realistic** reply, not the largest the schema permits (15
  questions with 1 000-character stems would be ~15 000 tokens, which no tier
  here can hold; the retry loop covers an underestimate).
- `prompt_char_budget(req)` is whatever the allowance has left, floored at
  `MIN_PROMPT_CHARS` so `MIN_SOURCE_CHARS` of lecture text always survives.
- `output_token_budget(req, prompt_chars)` reserves what is left once the
  *actual* prompt is built, floored at `MIN_OUTPUT_TOKENS`.

`build_user_prompt` then trims to fit: the avoid list gets a small fixed share
(and is trimmed per entry — `drop_repeats()` still checks the **full** list after
generation, so this weakens no guarantee), the context fields are capped at the
point where they stop being payload and start being prompt, and the source
material absorbs the remainder.

**Which half gives way is the design decision.** Ungrounded questions are the
failure this feature exists to avoid; *fewer* questions is an outcome the
response already reports through `requested_count`. So the grounding floor holds
and the question count yields.

On the free 8 000-TPM tier that means a 15-question request keeps ~2 200
characters of lecture text where a 5-question one keeps the full 8 000. Raising
`LLM_TOKENS_PER_MINUTE` to match a paid tier widens both automatically — it is a
setting precisely so this arithmetic is explicit rather than two magic numbers
hoping to stay compatible.

Two further things had to change before a maximal request survived a real call:

- **The retry inflates the prompt.** `generate_structured` re-sends the whole
  conversation with the rejected reply appended, so one bad 15-question reply
  roughly doubled the second attempt's prompt and produced a 413 caused by the
  *recovery* rather than the request. The echo is now truncated, and
  `fit_output_tokens()` in `core/llm_client.py` re-fits the reservation on every
  attempt. That guard is shared, so it covers all three services.
- **Models overshoot a stated count.** A request for 15 questions came back with
  17. `MAX_QUESTIONS` was both the request ceiling and the schema ceiling, so a
  maximal request had no tolerance: one extra question was a validation failure,
  a retry, and then a 502. The schema now carries `QUESTION_OVERSHOOT` /
  `OPTION_OVERSHOOT` of headroom and `generator.trim_options()` cuts the surplus
  back — always keeping the correct option, and never letting more than
  `MAX_OPTIONS` reach the caller, since the bulk-create serializer would reject
  it.

`tests/test_quiz_generator.py` asserts prompt + output ≤ the allowance for a
maximal request at several question counts, and that overshoot is trimmed rather
than rejected. The original 413 slipped through because the existing test only
checked that the schema's worst case fitted the *output* budget — nothing
checked the two halves together, and nothing exercised a reply larger than the
request.

---

## 4. The preview endpoint

`POST /api/v1/courses/ai/quiz-questions-preview/` —
`[IsAuthenticated, IsEmailVerified, IsCourseCreator]`, throttled by
`AIQuizThrottle`.

| Field | Notes |
|---|---|
| `quiz_id` | Required. Everything else about the quiz is resolved from it |
| `question_count` | 1–15, default 5 |
| `options_per_question` | 2–5, default 4. 2 gives true/false |
| `difficulty` | `recall` \| `understanding` \| `application`, default `understanding` |
| `topics` | ≤ 12 optional hints |
| `avoid_questions` | ≤ 30 unsaved drafts on screen; the quiz's own are added server-side |
| `extra_instructions` | Free-text steer; also what makes a regenerate differ |

Response `data`: `questions[]` (each `question_text`, `options[]`,
`explanation`, `difficulty`), `grounded`, `requested_count`.

`requested_count` is echoed because `questions` can be **shorter**: the
generator drops anything paraphrasing `avoid_questions`, and anything repeated
within one batch. An empty list is a legitimate answer, not an error — the UI
reports "asked for 8, generated 6" rather than silently showing fewer.

`difficulty` is what the question asks of the learner — state a fact, explain or
distinguish, pick the outcome for a described case — and is independent of the
course's own `level`. The request carries one; each question reports its own, so
the review modal can show the mix.

**It persists nothing.** Accepting is a separate call (§5). Never make this
endpoint write: a generated question nobody read is exactly what
`_validate_course_completeness` cannot catch, because it is complete — just
possibly wrong.

---

## 5. The bulk apply endpoint

`POST /api/v1/courses/quizzes/<int:quiz_id>/questions/bulk/` →
`QuizQuestionBulkCreateAPIView`.

```json
{"questions": [
  {"question_text": "…",
   "options": [{"answer_text": "…", "is_correct": true},
               {"answer_text": "…", "is_correct": false}]}
]}
```

**Nothing about this endpoint is AI-specific.** Writing a ten-question quiz one
row at a time is 50 sequential POSTs — each running its own ownership query,
with no transaction — and a failure halfway leaves a partly-built quiz. A
hand-authored paste or a future import uses the same path.

- Ownership and `guard_editable(quiz.section.course)` exactly as the
  single-create view does. **No `section=` argument**: this is a create path, so
  the drip-release carve-out applies and content may be added to an
  already-released section.
- The serializer enforces 1–20 questions per call, 2–5 options each, exactly one
  `is_correct`, no duplicate option text, and the column lengths
  (`answer_text` ≤ 500).
- One `transaction.atomic()`. Positions continue from `max(position) + 1` read
  inside the transaction; an `IntegrityError` on
  `uniq_quizquestion_quiz_position` — two applies racing for the same slot — is
  a **409** with nothing half-written.
- Stamps `Quiz.last_edited_by`. `QuizQuestion` and `QuizAnswer` carry no author
  fields by design: they are sub-rows of an already-authored parent.
- Returns the created questions **with their answers**, so the builder appends
  them without a refetch.

---

## 6. What does not change

`_validate_course_completeness` still blocks a quiz with no questions and a
question with no correct answer. `_LearnerQuizAnswerOptionSerializer` still
never declares `is_correct` — absence is a stronger guarantee than conditional
removal. Generated content earns no exemption from either, and both have
regression tests in `courses/all_tests/test_quiz_bulk_create.py`.

---

## 7. Throttling and failure

`AIQuizThrottle` (`scope='ai_quiz'`, `AI_QUIZ_RATE_LIMIT`, default `10/min`) is
the third throttle guarding spend rather than data integrity. Its own scope
again: questions are generated per quiz, outlines once per course, articles once
per lesson, so a shared counter would let any one of them exhaust the others.

Every upstream failure collapses to one **503** with one generic message
(`AIQuizError`) — network error, key mismatch, provider 502, unparseable body
are indistinguishable to the client. The reason is logged, never forwarded.
`REQUEST_TIMEOUT = (5, 45)`: the read leg stays above the AI service's own 40 s
LLM timeout so that service fails first with a real status.

Upstream, `/v1/quiz-questions/` maps failures like its siblings — provider
unreachable → 503, provider error → 502, unusable output after the retry budget
→ 502. A reply with a zero- or two-correct question lands in that last case.

---

## 8. Frontend

`quiz-ai-panel.tsx` sits in Quiz Builder above the question list, in both the
empty and populated states. It offers the count, options per question,
difficulty and a focus steer, and is disabled with a visible reason while the
quiz has no title.

`quiz-preview-modal.tsx` is the accept step, modelled on
`outline-preview-modal.tsx` — draft in, edited draft out, the parent does the
writing. Per question: an include checkbox, editable text, editable options with
a radio for the correct one, and add/remove within 2–5. The `explanation` is
shown greyed and labelled *not saved* (§9). A question closely matching one the
quiz already has is badged and **unticked by default** — offered, since the
instructor may prefer the wording, but never pre-accepted.

Three details worth keeping:

- **Errors render inline in the modal, never as a toast.** A toast over a
  dismissed modal loses the draft the instructor was editing.
- **Applying appends.** It never replaces or reorders existing questions. A
  generated plan describes work not yet done and must not touch authored
  content.
- **Closing during review discards the draft behind a two-click confirm** — the
  repo's existing idiom (`aiConfirmReplace` in `lesson-modal.tsx`); there is no
  dirty-tracking anywhere in that codebase to reuse.

---

## 9. Deliberately not built

- **No auto-apply.** The accept step *is* the feature (§4).
- **`explanation` is not persisted.** `QuizQuestion` has no column for it, and
  adding one is a real feature of its own — a migration, a serializer field, and
  learner reveal-on-wrong rules beside `build_quiz_attempt_result`. Generating
  it costs nothing extra and it earns its place in the review step, where the
  instructor is judging whether a question is fair.
- **No multi-select or "select all that apply".** The schema is single-correct
  (§2).
- **No regenerating one question in place.** A whole-quiz call is one LLM
  request; per-question regeneration multiplies spend for a marginal gain.
- **No assignments or coding exercises.** Each must be *complete* to be safe —
  an assignment needs a `model_answer` per question, a coding exercise a working
  `evaluation_script`, and an AI-written script that does not run blocks
  submission. Each is its own feature.
- **No tuning from learner attempt data.** `QuizAttempt` holds the history, but
  using it means a feedback loop and per-question statistics.
- **`Quiz.related_lectures` is untouched.** The M2M exists on the model but no
  serializer exposes it; §3 uses the section's lectures and does not need it.

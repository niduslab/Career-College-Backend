# 06) Quizzes

Quizzes are practice-oriented in the current design (no `passing_score`, no `time_limit`).
A quiz is authored by an instructor and submitted as a new attempt by learners.

## Key files

| File | Purpose |
|------|---------|
| `courses/all_models/assessment_models.py` | `Quiz`, `QuizQuestion`, `QuizAnswer`, `QuizAttempt`, `QuizAttemptAnswer` |
| `courses/all_views/content_views.py` | Quiz/question/answer instructor endpoints |
| `courses/all_views/learner_views.py` | Learner quiz detail and submit endpoints |
| `courses/all_serializers/learner_serializers.py` | `LearnerQuizDetailSerializer`, `build_quiz_attempt_result()` |
| `courses/services/learner_service.py` | `submit_quiz_attempt()` |
| `courses/urls.py` | Route definitions |

---

## Instructor-side models

### `Quiz`

- `section` (FK → `CourseSection`)
- `title`, `description`
- `related_lectures` (M2M → `Lecture`) — optional link to contextual lectures
- `GenericRelation` to `SectionContent` — cascade delete removes curriculum slot

### `QuizQuestion`

- `quiz` (FK → `Quiz`)
- `question_text`
- `position` — integer ordering within the quiz; **unique per quiz**

### `QuizAnswer`

- `question` (FK → `QuizQuestion`)
- `answer_text`
- `is_correct` (BooleanField)

**Rule:** At most one `is_correct=True` per question. Enforced at both the serializer level and
via a DB constraint. A quiz cannot be submitted for review until every question has at least one
correct answer — this is checked in `NidusCourse._validate_course_completeness()`.

---

## Learner-side models

### `QuizAttempt`

Created each time a learner submits a quiz. No cap on attempts — every submit creates a new row.

| Field | Type | Notes |
|-------|------|-------|
| `user` | FK → `User` | Learner who submitted |
| `quiz` | FK → `Quiz` | |
| `score` | PositiveIntegerField | Number of correct answers |
| `max_score` | PositiveIntegerField | Total number of questions at submit time |
| `submitted_at` | DateTimeField | Set at creation |

### `QuizAttemptAnswer`

One row per question per attempt. `is_correct` is **denormalized** onto this row at submit time
from the live `QuizAnswer.is_correct` value.

| Field | Type | Notes |
|-------|------|-------|
| `attempt` | FK → `QuizAttempt` | |
| `question` | FK → `QuizQuestion` | |
| `selected_answer` | FK → `QuizAnswer` (null) | Null if learner skipped the question |
| `is_correct` | BooleanField | Copied from `QuizAnswer.is_correct` at submit time |

**Why denormalize `is_correct`?** If an instructor later edits the answer key (marks a different
answer as correct), historical attempts should not retroactively change. Freezing `is_correct` onto
the attempt row preserves the historical verdict.

---

## Instructor authoring flow

```
1. Create quiz (curriculum-first, preferred):
   POST /api/v1/courses/sections/{section_id}/contents/
     body: { "item_type": "quiz", "title": "REST Basics Quiz" }
   → Creates Quiz + SectionContent in one atomic transaction
   → Returns { section_content_id, object_id (quiz_id), ... }

   (Alternative: POST /api/v1/courses/quizzes/ directly, then manage SectionContent separately)

2. Add questions:
   POST /api/v1/courses/quizzes/{quiz_id}/questions/
     body: { "question_text": "What does REST stand for?", "position": 1 }

3. Add answers to each question:
   POST /api/v1/courses/quiz-questions/{question_id}/answers/
     body: { "answer_text": "Representational State Transfer", "is_correct": true }
     body: { "answer_text": "Remote State Transfer", "is_correct": false }
     ...

4. Correct-answer rule enforced at API:
   → Serializer rejects a second is_correct=True for the same question (400)

5. Reorder questions:
   PATCH /api/v1/courses/quiz-questions/{question_id}/  { "position": 2 }
   (position uniqueness enforced; swap positions if needed)
```

---

## Learner submission flow

```
GET /api/v1/courses/learn/quizzes/{quiz_id}/
  Permission: enrolled learner OR course's own instructor
  → 404 for unenrolled (quiz IDs are not public)
         │
         ▼
LearnerQuizDetailSerializer returns:
  • id, section_id, title, description, question_count
  • questions: [
      { id, question_text, position,
        answers: [{ id, answer_text }]  ← is_correct NOT included
      }
    ]
  • latest_attempt: { attempt_id, score, max_score, submitted_at }
    (null if no previous attempt)

──────────────────────────────────────────────────────────────

POST /api/v1/courses/learn/quizzes/{quiz_id}/submit/
  Permission: IsLearnerUser (instructors get 403 — preview must not create attempts)
  body: {
    "answers": [
      { "question_id": 1, "selected_answer_id": 4 },
      { "question_id": 2, "selected_answer_id": null }  ← skipped question
    ]
  }
         │
         ▼
QuizSubmissionSerializer validates:
  • No duplicate question_ids
  • All question_ids exist in this quiz
  • selected_answer_id (if provided) belongs to its question
         │
         ▼
submit_quiz_attempt(user, quiz, answers_payload)
  [courses/services/learner_service.py]
         │
         ├─ Verify access (enrollment or instructor)
         │
         ├─ Atomic transaction:
         │    QuizAttempt.objects.create(user, quiz, score=0, max_score=len(questions))
         │    For each question:
         │      selected = QuizAnswer.objects.get(id=selected_answer_id) or None
         │      is_correct = selected.is_correct if selected else False
         │      QuizAttemptAnswer.objects.create(
         │          attempt, question, selected_answer=selected,
         │          is_correct=is_correct  ← FROZEN from live answer key
         │      )
         │    score = count of is_correct=True answers
         │    attempt.score = score; attempt.save()
         │    recalculate_progress(enrollment)  ← quiz marked as attempted
         │
         ▼
build_quiz_attempt_result(attempt)
  [courses/all_serializers/learner_serializers.py]
  Returns per-question result:
  {
    "attempt_id": 42,
    "score": 3,
    "max_score": 5,
    "submitted_at": "...",
    "questions": [
      {
        "question_id": 1,
        "question_text": "What does REST stand for?",
        "selected_answer_id": 4,
        "selected_answer_text": "Representational State Transfer",
        "is_correct": true
        ← correct_answer_id / correct_answer_text NOT present (answered correctly)
      },
      {
        "question_id": 2,
        "question_text": "What HTTP method creates a resource?",
        "selected_answer_id": 7,
        "selected_answer_text": "GET",
        "is_correct": false,
        "correct_answer_id": 9,            ← REVEALED only when wrong
        "correct_answer_text": "POST"      ← REVEALED only when wrong
      }
    ]
  }
```

**Reveal rule:** `correct_answer_id` and `correct_answer_text` appear in the response **only when
`is_correct=False`**. This is implemented in `build_quiz_attempt_result()` (a plain Python
function, not a serializer class) — the conditional-presence logic is trivial in plain Python
and centralized so every caller gets identical behavior.

---

## Progress integration

After a quiz is submitted, `recalculate_progress(enrollment)` is called at the end of the
`submit_quiz_attempt` transaction. A quiz is counted as "completed" (for progress purposes) when
**at least one `QuizAttempt` exists** for the `(user, quiz)` pair — the score doesn't matter.
See `12-enrollment.md` for the full progress calculation formula.

---

## API surface

**Instructor:**
```
POST   /api/v1/courses/quizzes/                            → create (direct)
GET    /api/v1/courses/quizzes/{quiz_id}/                  → detail
PATCH  /api/v1/courses/quizzes/{quiz_id}/                  → update metadata
DELETE /api/v1/courses/quizzes/{quiz_id}/                  → delete (cascades SectionContent)

GET/POST   /api/v1/courses/quizzes/{quiz_id}/questions/
GET/PATCH/DELETE  /api/v1/courses/quiz-questions/{question_id}/
GET/POST   /api/v1/courses/quiz-questions/{question_id}/answers/
GET/PATCH/DELETE  /api/v1/courses/quiz-answers/{answer_id}/
```

**Learner:**
```
GET   /api/v1/courses/learn/quizzes/{quiz_id}/         → quiz detail (no is_correct)
POST  /api/v1/courses/learn/quizzes/{quiz_id}/submit/  → submit attempt, get results
```

---

## Why this design

- **`is_correct` denormalized onto `QuizAttemptAnswer`** preserves historical verdicts. If the
  instructor later changes the correct answer, past attempts keep their original scored result.
- **Separate `QuizAttempt` per submit** (no attempt cap) lets learners retake quizzes freely. Each
  attempt is an independent row, so all history is available.
- **`build_quiz_attempt_result()` as a plain function** (not a serializer class) handles the
  conditional-reveal logic cleanly — `correct_answer_*` fields appear only on wrong answers, which
  is awkward to express with DRF `SerializerMethodField` declarations but trivial in Python.
- **`is_correct` absent from learner GET response** — the `_LearnerQuizAnswerOptionSerializer`
  simply does not declare the field. Absence is a stronger guarantee against accidental leakage
  than conditional `to_representation` stripping.
- **Two creation styles** (curriculum-first via `/contents/` or direct via `/quizzes/`) support
  both drag-and-drop curriculum builders and direct resource APIs without code duplication.

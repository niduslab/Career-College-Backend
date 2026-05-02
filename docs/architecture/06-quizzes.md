# 06) Quizzes

Quizzes are practice-oriented in current design (no `passing_score`, no `time_limit`).

## Key files

- `courses/models.py`: `Quiz`, `QuizQuestion`, `QuizAnswer`
- `courses/all_views/content_views.py`: quiz, question, answer endpoints
- `courses/serializers.py`: quiz/question/answer serializers
- `courses/urls.py`: routes
- `courses/services.py`: curriculum linkage and ordering helpers

## Models and fields

## `Quiz`

- `section` (FK -> `CourseSection`)
- `title`
- `description`
- `related_lectures` (M2M -> `Lecture`)
- Generic relation to `SectionContent` for curriculum placement

## `QuizQuestion`

- `quiz` (FK)
- `question_text`
- `position` (unique per quiz)

## `QuizAnswer`

- `question` (FK)
- `answer_text`
- `is_correct`
- Rule: at most one `is_correct=True` per question.

## Creation styles

1. Curriculum-first:
   - `POST /courses/sections/{section_id}/contents/` with `item_type="quiz"`
   - Creates `Quiz` and linked `SectionContent`.
2. Direct-quiz:
   - `POST /courses/quizzes/`
   - Also creates linked curriculum slot based on `position`.

Both are valid; first is better for drag-and-drop curriculum builders.

## Question/answer workflow

1. Create quiz.
2. Add questions to `/courses/quizzes/{quiz_id}/questions/`.
3. Add answers to `/courses/quiz-questions/{question_id}/answers/`.
4. Keep exactly one correct answer per question.

## Workflow

1. Quiz is created (curriculum-first or direct endpoint).
2. Quiz is placed in section order via `SectionContent`.
3. Questions are created in ordered sequence (`position`).
4. Answers are added, enforcing one correct answer per question.

## System Explanation (Why This Design)

- Supports both UI-driven curriculum creation and direct resource operations.
- Ordered questions simplify deterministic quiz rendering.
- Correct-answer constraint enforces content integrity at API + DB layers.

**Current State**
1. Enrollment exists and is clearly intended as the access gate (`active enrollment` required): [enrollment_models.py](/c:/Users/rdnid/OneDrive/Desktop/career_college_backend/courses/all_models/enrollment_models.py:13)
2. Learner endpoints currently stop at `my-courses` metadata/dashboard: [enrollment_views.py](/c:/Users/rdnid/OneDrive/Desktop/career_college_backend/courses/all_views/enrollment_views.py:198)
3. Lecture/quiz/assignment APIs are instructor-owned (`course__instructors=request.user`) and not learner consumption APIs: [content_views.py](/c:/Users/rdnid/OneDrive/Desktop/career_college_backend/courses/all_views/content_views.py:109)
4. You already have `WatchProgress` and progress recalculation signals, so the progress backbone is in place: [content_models.py](/c:/Users/rdnid/OneDrive/Desktop/career_college_backend/courses/all_models/content_models.py:282), [signals.py](/c:/Users/rdnid/OneDrive/Desktop/career_college_backend/courses/signals.py:25)
5. Sensitive fields are known and must stay hidden for learners (`model_answer`, `solution_code`, hidden test cases): [assessment_models.py](/c:/Users/rdnid/OneDrive/Desktop/career_college_backend/courses/all_models/assessment_models.py:75), [assessment_models.py](/c:/Users/rdnid/OneDrive/Desktop/career_college_backend/courses/all_models/assessment_models.py:293)

---

**Core Design Principle**
Build a **separate learner API surface** (new views + serializers) instead of reusing instructor endpoints with conditionals.  
Reason: avoids accidental data leaks and keeps permissions/business rules clean.

---

**Recommended Learner Endpoint Set (MVP)**
1. `GET /api/v1/courses/learn/{course_slug}/curriculum/`
- Returns ordered sections + `SectionContent` items learner can consume.
- Include lightweight item metadata only.
- Source order from `SectionContent.position` (already canonical).

2. `GET /api/v1/courses/learn/lectures/{lecture_id}/`
- Returns learner-safe lecture payload.
- For video: expose stream URLs/status from lecture/active asset.
- For article: expose article text.

3. `POST /api/v1/courses/learn/lectures/{lecture_id}/progress/`
- Upsert `WatchProgress` (`watched_seconds`, `is_completed`).
- Idempotent update behavior.

4. `GET /api/v1/courses/learn/quizzes/{quiz_id}/`
- Returns quiz + questions + answer options for attempt UI.
- Must not expose correctness metadata in payload used for submission.

5. `POST /api/v1/courses/learn/quizzes/{quiz_id}/submit/`
- Accepts selected answers.
- Returns score/pass/fail + per-question feedback policy you choose.

6. `GET /api/v1/courses/learn/assignments/{assignment_id}/`
- Learner-facing assignment + questions.
- Never include `model_answer`.

7. `POST /api/v1/courses/learn/assignments/{assignment_id}/submit/`
- Store learner responses for instructor review/manual grading (or auto-grade if objective).

8. `GET /api/v1/courses/learn/{course_slug}/progress/`
- Aggregated completion snapshot for UI refresh.

---

**New Models You’ll Likely Need**
1. `QuizAttempt`
- `user`, `quiz`, `score`, `max_score`, `passed`, `attempt_number`, `submitted_at`, optional `time_spent_seconds`

2. `QuizAttemptAnswer`
- `attempt`, `question`, `selected_answer`, `is_correct`

3. `AssignmentSubmission`
- `user`, `assignment`, `status` (`draft/submitted/reviewed`), `submitted_at`, `grade`, `feedback`

4. `AssignmentSubmissionAnswer`
- `submission`, `question`, `answer_text`

You already have strong course/content/enrollment schema; these are the missing learner interaction records.

---

**Permissions & Access Guard**
Create a reusable permission/helper like `CanAccessEnrolledCourseContent`:
1. user must be authenticated learner
2. user must have active enrollment for the item’s course
3. course must be published (or whatever policy you choose)

This formalizes what your enrollment model already documents: [enrollment_models.py](/c:/Users/rdnid/OneDrive/Desktop/career_college_backend/courses/all_models/enrollment_models.py:13)

---

**Serializer Strategy (Leak-Proof)**
Create dedicated learner serializers, never reuse instructor serializers directly:
1. `LearnerLectureSerializer`
2. `LearnerQuizSerializer` (no `is_correct` in question options before submit)
3. `LearnerAssignmentSerializer` (no `model_answer`)
4. `LearnerCodingExerciseSerializer` (if/when needed; never `solution_code`, never hidden test cases)

Today, instructor serializers include sensitive fields and rely on context-based stripping in some places, which is risky for learner endpoints: [assessment_serializers.py](/c:/Users/rdnid/OneDrive/Desktop/career_college_backend/courses/all_serializers/assessment_serializers.py:275)

---

**Service Layer Ideas**
Add `courses/services/learner_service.py` with functions like:
1. `get_enrolled_course_curriculum(user, course_slug)`
2. `get_learner_lecture(user, lecture_id)`
3. `upsert_watch_progress(user, lecture_id, watched_seconds, is_completed)`
4. `start_or_validate_quiz_attempt(user, quiz_id)`
5. `grade_quiz_attempt(user, quiz_id, answers_payload)`
6. `submit_assignment(user, assignment_id, responses_payload)`

This keeps views slim and matches your existing service-heavy style.

---

**Performance Plan**
1. Curriculum endpoint: prefetch sections + contents + item maps in bulk (similar pattern you already use in instructor content list serializer context).
2. Add query indexes for attempt/submission tables on `(user, quiz)` and `(user, assignment)`.
3. Keep progress recalculation signal (already optimized) and optionally debounce lecture progress writes from frontend.

---

**Behavioral Rules to Decide Now**
1. Retake policy for quizzes: unlimited vs capped, highest score vs latest score
2. Assignment policy: single submission vs resubmission
3. Completion criteria: lecture-only (current) vs include quiz/assignment completions
4. Locked progression: free navigation vs sequential unlock

These decisions shape model constraints and response contracts.

---

**Testing Plan (High Priority)**
Add `courses/all_tests/test_learner_consumption.py` covering:
1. enrolled learner can access course content
2. unenrolled learner gets `403/404` (pick one policy and keep consistent)
3. instructor cannot use learner-only endpoints
4. quiz payload does not leak correct answers pre-submit
5. assignment payload never leaks `model_answer`
6. watch progress updates enrollment progress
7. attempts/submissions are scoped per learner (no cross-user visibility)

---

**Suggested Delivery Phases**
1. Phase 1 (fast MVP): curriculum + lecture detail + watch progress update
2. Phase 2: quiz consume + submit + auto-grade
3. Phase 3: assignment consume + submission lifecycle + instructor review endpoints
4. Phase 4: coding exercise learner runtime/submission (if needed)

---


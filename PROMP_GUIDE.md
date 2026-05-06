# CODING EXERCISE

Prompt 1 — Instructor: Coding Exercise CRUD

You are working on a Django LMS backend (Udemy-style). The codebase already has a
`courses` Django app with these relevant files:
  - courses/models.py       — NidusCourse, CourseSection, SectionContent (generic slot),
                              Lecture, Quiz, QuizQuestion, QuizAnswer, VideoAsset, etc.
  - courses/serializers.py  — instructor-facing serializers for all models above
  - courses/content_views.py — APIView-based views: SectionContentListCreateAPIView,
                               LectureCreateAPIView, QuizCreateAPIView, etc.
  - courses/course_views.py  — CourseListAPIView, CourseCreateAPIView, CourseDetailView
  - courses/services.py      — create_section_content_for_object(), reorder_section_content(), etc.
  - courses/urls.py          — all URL patterns
  - courses/views.py         — re-exports from all_views / content_views

SectionContent already has ItemType.CODING = 'coding' defined but not implemented.
All content types (Lecture, Quiz) follow this pattern:
  1. The model has a `section` FK and a `GenericRelation` back to SectionContent.
  2. Creating the object also creates a SectionContent slot (via create_section_content_for_object()).
  3. Deleting the object cascades to the SectionContent slot via GenericRelation.
  4. SectionContentListCreateAPIView.post() handles item_type dispatch.
  5. SectionContentSerializer.get_content() returns a brief dict for each type.

Permissions used throughout: IsAuthenticated + IsEmailVerified + IsVerifiedInstructor
(from core.permissions). Use these exact permission classes — never raw is_staff.

---

TASK: Implement instructor CRUD for coding exercises as a SectionContent item type.
Do NOT implement code execution, learner submission, or Celery tasks — those are Part 2.

---

### 1. Add models to courses/models.py

Add these three models (keep all existing models intact):

**CodingExercise(TimestampedModel)**
  - section: FK(CourseSection, CASCADE, related_name='coding_exercises')
  - title: CharField(max_length=255)
  - description: TextField(blank=True, default='')
  - problem_statement: TextField()
  - difficulty: CharField(max_length=10, choices=[easy/medium/hard], default='easy', db_index=True)
  - default_language: CharField(max_length=20, default='python')
  - supported_languages: JSONField(default=list)
    — stores e.g. ["python", "javascript", "cpp", "java"]
  - time_limit_ms: PositiveIntegerField(default=2000)
  - section_content: GenericRelation(SectionContent, content_type_field='content_type',
      object_id_field='object_id', related_query_name='coding_exercise')
  - Meta: db_table='coding_exercises', ordering=['-created_at']
  - Index on (section, difficulty)

**CodingExerciseLanguageConfig(TimestampedModel)**
  - exercise: FK(CodingExercise, CASCADE, related_name='language_configs')
  - language: CharField(max_length=20)
    choices: python, javascript, cpp, java
  - starter_code: TextField(blank=True, default='')
  - solution_code: TextField(blank=True, default='')
    !! solution_code must NEVER appear in any learner-facing serializer
  - Meta: db_table='coding_exercise_language_configs'
  - UniqueConstraint on (exercise, language) named 'uniq_coding_lang_config'
  - Index on (exercise, language)

**CodingTestCase(models.Model)**
  - exercise: FK(CodingExercise, CASCADE, related_name='test_cases')
  - input_data: TextField()
  - expected_output: TextField()
  - is_hidden: BooleanField(default=False, db_index=True)
    — hidden cases are used for grading only, never shown to learners
  - explanation: CharField(max_length=255, blank=True, default='')
  - position: PositiveIntegerField(default=1, db_index=True)
  - Meta: db_table='coding_test_cases', ordering=['exercise_id', 'position', 'id']
  - UniqueConstraint on (exercise, position) named 'uniq_testcase_exercise_position'
  - Index on (exercise, position)

---

### 2. Add serializers to courses/serializers.py

These are ALL instructor-facing (no learner serializers needed in Part 1):

**CodingTestCaseSerializer(ModelSerializer)**
  fields: id, exercise_id (read-only via source), input_data, expected_output,
          is_hidden, explanation, position
  read_only: id, exercise_id

**CodingExerciseLanguageConfigSerializer(ModelSerializer)**
  fields: id, exercise_id (read-only via source), language, starter_code, solution_code
  read_only: id, exercise_id

**CodingExerciseSerializer(ModelSerializer)** — read-only representation
  fields: id, section_id (read-only), title, description, problem_statement,
          difficulty, default_language, supported_languages, time_limit_ms,
          language_configs (nested CodingExerciseLanguageConfigSerializer, many, read-only),
          test_cases (nested CodingTestCaseSerializer, many, read-only),
          created_at, updated_at
  all fields read-only

**CodingExerciseCreateUpdateSerializer(ModelSerializer)**
  fields: title, description, problem_statement, difficulty, default_language,
          supported_languages, time_limit_ms
  validate_title: strip, min 3 chars
  validate_supported_languages: must be a list, each item in
    ['python','javascript','cpp','java'], non-empty
  validate: default_language must be in supported_languages

---

### 3. Create courses/coding_views.py

Follow the exact style of courses/content_views.py (APIView, permission_classes,
parser_classes, _get_owned_* helpers, consistent response shape).

**CodingExerciseCreateAPIView(APIView)**
  POST /api/courses/coding-exercises/
  permissions: IsAuthenticated, IsEmailVerified, IsVerifiedInstructor
  - Validate section ownership (section must belong to a course the user instructs)
  - Validate request body with CodingExerciseCreateUpdateSerializer
  - In a transaction: create CodingExercise, then call
    create_section_content_for_object(section, exercise, SectionContent.ItemType.CODING, position)
  - Accept optional 'position' and 'section' (PK) in request body
  - Return 201 with CodingExerciseSerializer data

**CodingExerciseDetailAPIView(APIView)**
  GET / PATCH / DELETE /api/courses/coding-exercises/{exercise_id}/
  - _get_owned_exercise: select_related('section__course'), guard course__instructors=request.user
  - GET: return serialized exercise
  - PATCH: partial update via CodingExerciseCreateUpdateSerializer
  - DELETE: exercise.delete() — GenericRelation cascades SectionContent slot

**CodingExerciseLanguageConfigListCreateAPIView(APIView)**
  GET / POST /api/courses/coding-exercises/{exercise_id}/language-configs/
  - GET: return all language configs for this exercise
  - POST: create one language config; reject if language already exists (IntegrityError → 400)

**CodingExerciseLanguageConfigDetailAPIView(APIView)**
  GET / PATCH / DELETE /api/courses/coding-exercises/{exercise_id}/language-configs/{config_id}/

**CodingTestCaseListCreateAPIView(APIView)**
  GET / POST /api/courses/coding-exercises/{exercise_id}/testcases/

**CodingTestCaseDetailAPIView(APIView)**
  GET / PATCH / DELETE /api/courses/coding-exercises/{exercise_id}/testcases/{tc_id}/

All views must verify the exercise belongs to a course the requesting user instructs.

---

### 4. Update existing files

**courses/content_views.py — SectionContentListCreateAPIView.post()**
  Add elif for item_type == 'coding' that calls a _create_coding_exercise() helper
  (same pattern as _create_lecture and _create_quiz). It should:
  - Validate body with CodingExerciseCreateUpdateSerializer
  - Require 'section' from context (the section_id URL param already provides this)
  - Create exercise + SectionContent slot in a transaction

**courses/serializers.py — SectionContentSerializer.get_content()**
  Add handling for ItemType.CODING:
    coding_exercises: dict = self.context.get('coding_exercises', {})
    if obj.item_type == SectionContent.ItemType.CODING:
        ex = coding_exercises.get(obj.object_id)
        if ex:
            return {'id': ex.id, 'title': ex.title, 'difficulty': ex.difficulty,
                    'default_language': ex.default_language}

**courses/content_views.py — SectionContentListCreateAPIView.get()**
  Add coding_exercise bulk-load (same pattern as lectures/quizzes):
    coding_exercise_ids = [c.object_id for c in contents if c.item_type == SectionContent.ItemType.CODING]
    coding_exercises = {ex.id: ex for ex in CodingExercise.objects.filter(id__in=coding_exercise_ids)} if coding_exercise_ids else {}
  Pass 'coding_exercises' in serializer context.

**courses/urls.py**
  Import and register:
    path('coding-exercises/', CodingExerciseCreateAPIView.as_view(), name='coding-exercise-create'),
    path('coding-exercises/<int:exercise_id>/', CodingExerciseDetailAPIView.as_view(), name='coding-exercise-detail'),
    path('coding-exercises/<int:exercise_id>/language-configs/', CodingExerciseLanguageConfigListCreateAPIView.as_view(), name='coding-lang-config-list-create'),
    path('coding-exercises/<int:exercise_id>/language-configs/<int:config_id>/', CodingExerciseLanguageConfigDetailAPIView.as_view(), name='coding-lang-config-detail'),
    path('coding-exercises/<int:exercise_id>/testcases/', CodingTestCaseListCreateAPIView.as_view(), name='coding-testcase-list-create'),
    path('coding-exercises/<int:exercise_id>/testcases/<int:tc_id>/', CodingTestCaseDetailAPIView.as_view(), name='coding-testcase-detail'),

**courses/views.py**
  Add all new view classes to imports and __all__.

---

### 5. Migrations

After all model changes:
  python manage.py makemigrations courses
  python manage.py migrate

---

### 6. Tests

Add a test class CodingExerciseInstructorTests(APITestCase) in the courses test file.
Cover:
  - Instructor can create a coding exercise (verify SectionContent slot is also created)
  - Instructor can retrieve, update, delete a coding exercise
  - Instructor can add/update/delete language configs
  - Instructor can add/update/delete test cases (including hidden ones)
  - Non-instructor or non-owner cannot access these endpoints (403)
  - solution_code is present in instructor responses
  - Creating exercise via SectionContentListCreateAPIView (item_type='coding') also works
  - SectionContent list includes coding exercise with brief content dict (no solution_code)

Use APITestCase, set up a test instructor user with IsEmailVerified + IsVerifiedInstructor
permission equivalents as used elsewhere in the existing test suite.
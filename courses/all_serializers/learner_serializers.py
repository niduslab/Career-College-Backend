"""
Learner-facing serializers for the Phase-1 + Phase-2 consumption surface.

Kept separate from the instructor-side serializers so sensitive fields
(model_answer, solution_code, hidden test cases, quiz correctness flags)
cannot accidentally bleed into a learner response — these serializers
simply do not declare those fields.

`build_quiz_attempt_result` is a function rather than a `Serializer` class
because it shapes a per-question verdict whose presence-of-fields rule
("show correct answer only when wrong") is awkward to express with DRF
field declarations and trivial in plain Python.
"""

from django.db.models import Prefetch
from rest_framework import serializers

from courses.all_serializers.content_serializers import (
    _normalize_media_relative_path,
    _normalize_renditions_playlists,
)
from courses.models import (
    AssignmentSubmission,
    CodingSubmission,
    CodingSubmissionTestResult,
    QuizAnswer,
)


class LearnerWatchProgressSerializer(serializers.Serializer):
    """Read-side projection of a learner's per-lecture progress."""

    watched_seconds = serializers.IntegerField()
    is_completed = serializers.BooleanField()
    last_watched_at = serializers.DateTimeField(allow_null=True)


class LearnerLectureDetailSerializer(serializers.Serializer):
    """
    Learner-safe lecture payload.

    Video lectures expose HLS playlist + renditions; article lectures expose
    the article text. `transcoding_error` and the raw `VideoAsset` (including
    file paths and mime type) are deliberately omitted.
    """

    id = serializers.IntegerField()
    section_id = serializers.IntegerField()
    title = serializers.CharField()
    lecture_type = serializers.CharField()
    article_content = serializers.CharField()
    stream_master_playlist = serializers.SerializerMethodField()
    stream_renditions = serializers.SerializerMethodField()
    duration_seconds = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    def get_stream_master_playlist(self, lecture):
        return _normalize_media_relative_path(lecture.stream_master_playlist)

    def get_stream_renditions(self, lecture):
        return _normalize_renditions_playlists(lecture.stream_renditions)

    def get_duration_seconds(self, lecture):
        # Pulled from the active VideoAsset via the view's context; falls back
        # to None for article lectures or videos that haven't finished probing.
        return self.context.get('duration_seconds')

    def get_progress(self, lecture):
        wp = self.context.get('watch_progress')
        if wp is None:
            return None
        return {
            'watched_seconds': wp.watched_seconds,
            'is_completed': wp.is_completed,
            'last_watched_at': wp.last_watched_at,
        }


class WatchProgressUpsertSerializer(serializers.Serializer):
    """Validate the POST body for `/learn/lectures/<id>/progress/`."""

    watched_seconds = serializers.IntegerField(min_value=0)
    is_completed = serializers.BooleanField()


# ---------------------------------------------------------------------------
# Quiz consumption + submission (Phase 2)
# ---------------------------------------------------------------------------

class _LearnerQuizAnswerOptionSerializer(serializers.Serializer):
    """One answer option for the attempt UI. `is_correct` is deliberately
    not declared — absence is a stronger guarantee than conditional removal."""

    id = serializers.IntegerField()
    answer_text = serializers.CharField()


class _LearnerQuizQuestionSerializer(serializers.Serializer):
    """Question + its answer options, no correctness exposed."""

    id = serializers.IntegerField()
    question_text = serializers.CharField()
    position = serializers.IntegerField()
    answers = serializers.SerializerMethodField()

    def get_answers(self, question):
        # Use the prefetched cache so we don't N+1 across questions.
        prefetched = getattr(question, '_prefetched_objects_cache', {})
        answer_objs = prefetched['answers'] if 'answers' in prefetched else list(question.answers.all())
        return _LearnerQuizAnswerOptionSerializer(answer_objs, many=True).data


class LearnerQuizDetailSerializer(serializers.Serializer):
    """Learner-safe quiz payload for the attempt UI."""

    id = serializers.IntegerField()
    section_id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()
    question_count = serializers.SerializerMethodField()
    questions = serializers.SerializerMethodField()
    latest_attempt = serializers.SerializerMethodField()

    def get_question_count(self, quiz):
        prefetched = getattr(quiz, '_prefetched_objects_cache', {})
        if 'questions' in prefetched:
            return len(prefetched['questions'])
        return quiz.questions.count()

    def get_questions(self, quiz):
        prefetched = getattr(quiz, '_prefetched_objects_cache', {})
        question_objs = prefetched['questions'] if 'questions' in prefetched else list(quiz.questions.order_by('position', 'id'))
        return _LearnerQuizQuestionSerializer(question_objs, many=True).data

    def get_latest_attempt(self, _quiz):
        attempt = self.context.get('latest_attempt')
        if attempt is None:
            return None
        return {
            'attempt_id': attempt.id,
            'score': attempt.score,
            'max_score': attempt.max_score,
            'submitted_at': attempt.submitted_at,
        }


class _QuizAnswerSubmissionSerializer(serializers.Serializer):
    """One element of the submission payload's `answers` list."""

    question_id = serializers.IntegerField()
    # `null` means the learner left the question unanswered.
    selected_answer_id = serializers.IntegerField(allow_null=True, required=False)


class QuizSubmissionSerializer(serializers.Serializer):
    """Validate the POST body for `/learn/quizzes/<id>/submit/`."""

    answers = _QuizAnswerSubmissionSerializer(many=True)

    def validate_answers(self, value):
        # Reject duplicate question_ids in the payload — at most one
        # selected answer per question, matching the unique constraint
        # on QuizAttemptAnswer.
        seen = set()
        for item in value:
            qid = item['question_id']
            if qid in seen:
                raise serializers.ValidationError(
                    f'question_id {qid} appears more than once in the payload.'
                )
            seen.add(qid)
        return value

    def validate(self, attrs):
        quiz = self.context.get('quiz')
        if quiz is None:
            return attrs

        # Cross-reference each submitted question_id and selected_answer_id
        # against the actual quiz structure. Rejecting bad IDs here is
        # cleaner than letting them silently drop in the service layer.
        valid_answer_ids_by_question: dict[int, set[int]] = {}
        for question in quiz.questions.all():
            prefetched = getattr(question, '_prefetched_objects_cache', {})
            answers_iter = prefetched['answers'] if 'answers' in prefetched else question.answers.all()
            valid_answer_ids_by_question[question.id] = {a.id for a in answers_iter}

        errors = []
        for item in attrs['answers']:
            qid = item['question_id']
            if qid not in valid_answer_ids_by_question:
                errors.append(f'question_id {qid} does not belong to this quiz.')
                continue
            selected = item.get('selected_answer_id')
            if selected is not None and selected not in valid_answer_ids_by_question[qid]:
                errors.append(
                    f'selected_answer_id {selected} does not belong to question {qid}.'
                )

        if errors:
            raise serializers.ValidationError({'answers': errors})
        return attrs


def build_quiz_attempt_result(attempt) -> dict:
    """
    Build the response payload for a freshly-submitted (or re-fetched) quiz
    attempt. The shape is:

        {
            'attempt_id', 'score', 'max_score', 'submitted_at',
            'questions': [
                {
                    'question_id', 'question_text',
                    'selected_answer_id', 'selected_answer_text',
                    'is_correct',
                    # only present when is_correct=False:
                    'correct_answer_id', 'correct_answer_text',
                },
                ...
            ],
        }

    The "show correct answer only when wrong" rule is implemented here so
    every caller (current and future) gets identical behaviour without
    needing a conditional in the view.
    """
    # Pull the attempt's answers with the related question + selected_answer
    # eagerly so we don't N+1. The correct-answer lookup needs the full
    # QuizAnswer set per question, so prefetch that too.
    answer_rows = list(
        attempt.answers
        .select_related('question', 'selected_answer')
        .prefetch_related(Prefetch('question__answers', queryset=QuizAnswer.objects.order_by('id')))
        .order_by('question__position', 'question_id')
    )

    questions_payload = []
    for row in answer_rows:
        item = {
            'question_id': row.question_id,
            'question_text': row.question.question_text,
            'selected_answer_id': row.selected_answer_id,
            'selected_answer_text': row.selected_answer.answer_text if row.selected_answer else None,
            'is_correct': row.is_correct,
        }
        if not row.is_correct:
            correct = next(
                (a for a in row.question.answers.all() if a.is_correct),
                None,
            )
            if correct is not None:
                item['correct_answer_id'] = correct.id
                item['correct_answer_text'] = correct.answer_text
        questions_payload.append(item)

    return {
        'attempt_id': attempt.id,
        'score': attempt.score,
        'max_score': attempt.max_score,
        'submitted_at': attempt.submitted_at,
        'questions': questions_payload,
    }


# ---------------------------------------------------------------------------
# Assignment consumption + submission (Phase 2)
# ---------------------------------------------------------------------------

class _LearnerAssignmentQuestionSerializer(serializers.Serializer):
    """Learner-safe assignment question payload.

    `model_answer` and `rubric` are deliberately NOT declared — absence is
    a stronger guarantee than conditional removal. Same pattern as the
    quiz answer-option serializer.
    """

    id = serializers.IntegerField()
    question_text = serializers.CharField()
    points = serializers.IntegerField()
    hint = serializers.CharField()
    position = serializers.IntegerField()


def _latest_submission_summary(submission) -> dict | None:
    if submission is None:
        return None
    return {
        'submission_id': submission.id,
        'status': submission.status,
        'total_score': submission.total_score,
        'max_score': submission.max_score,
        'submitted_at': submission.submitted_at,
        'graded_at': submission.graded_at,
    }


class LearnerAssignmentDetailSerializer(serializers.Serializer):
    """Learner-safe assignment payload for the attempt UI."""

    id = serializers.IntegerField()
    section_id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()
    instructions = serializers.CharField()
    passing_score = serializers.IntegerField()
    max_score = serializers.SerializerMethodField()
    question_count = serializers.SerializerMethodField()
    questions = serializers.SerializerMethodField()
    latest_submission = serializers.SerializerMethodField()

    def _prefetched_questions(self, assignment):
        prefetched = getattr(assignment, '_prefetched_objects_cache', {})
        if 'questions' in prefetched:
            return prefetched['questions']
        return list(assignment.questions.order_by('position', 'id'))

    def get_max_score(self, assignment) -> int:
        return sum((q.points or 0) for q in self._prefetched_questions(assignment))

    def get_question_count(self, assignment) -> int:
        return len(self._prefetched_questions(assignment))

    def get_questions(self, assignment):
        return _LearnerAssignmentQuestionSerializer(
            self._prefetched_questions(assignment), many=True,
        ).data

    def get_latest_submission(self, _assignment):
        return _latest_submission_summary(self.context.get('latest_submission'))


class _AssignmentAnswerSubmissionSerializer(serializers.Serializer):
    """One entry in the POST /submit/ body's `answers` list."""

    question_id = serializers.IntegerField()
    answer_text = serializers.CharField(allow_blank=True, trim_whitespace=False)


class AssignmentSubmissionInputSerializer(serializers.Serializer):
    """Validate the POST body for `/learn/assignments/<id>/submit/`."""

    answers = _AssignmentAnswerSubmissionSerializer(many=True, allow_empty=False)

    def validate_answers(self, value):
        seen = set()
        for item in value:
            qid = item['question_id']
            if qid in seen:
                raise serializers.ValidationError(
                    f'question_id {qid} appears more than once in the payload.'
                )
            seen.add(qid)
        return value

    def validate(self, attrs):
        assignment = self.context.get('assignment')
        if assignment is None:
            return attrs

        prefetched = getattr(assignment, '_prefetched_objects_cache', {})
        if 'questions' in prefetched:
            question_ids = {q.id for q in prefetched['questions']}
        else:
            question_ids = set(assignment.questions.values_list('id', flat=True))

        errors = []
        submitted_ids = set()
        for item in attrs['answers']:
            qid = item['question_id']
            submitted_ids.add(qid)
            if qid not in question_ids:
                errors.append(f'question_id {qid} does not belong to this assignment.')

        missing = question_ids - submitted_ids
        if missing:
            errors.append(
                f'Missing answers for question_id(s): {sorted(missing)}. '
                'All questions must have an answer (use an empty string to skip).'
            )

        if errors:
            raise serializers.ValidationError({'answers': errors})
        return attrs


def build_assignment_submission_result(submission) -> dict:
    """Build the response payload for an assignment submission detail GET.

    Always returns: id, assignment_id, status, total_score, max_score,
    submitted_at, graded_at, grading_error, answers.

    Each answer row includes: question_id, question_text, answer_text,
    score, max_score, criterion_results, feedback. The instructor's
    `model_answer` is included on a per-question basis **only** when the
    submission has reached a terminal graded state (`passed` or `failed`).
    During `submitted` / `grading` / `grading_failed`, `model_answer` is
    omitted entirely — absence beats conditional null.
    """
    reveal_model_answer = submission.status in (
        AssignmentSubmission.Status.PASSED,
        AssignmentSubmission.Status.FAILED,
    )

    answer_rows = list(
        submission.answers
        .select_related('question')
        .order_by('question__position', 'question_id')
    )

    answers_payload = []
    for row in answer_rows:
        item = {
            'question_id': row.question_id,
            'question_text': row.question.question_text,
            'answer_text': row.answer_text,
            'score': row.score,
            'max_score': row.max_score,
            'criterion_results': row.criterion_results,
            'feedback': row.feedback,
        }
        if reveal_model_answer:
            item['model_answer'] = row.question.model_answer
        answers_payload.append(item)

    return {
        'submission_id': submission.id,
        'assignment_id': submission.assignment_id,
        'status': submission.status,
        'total_score': submission.total_score,
        'max_score': submission.max_score,
        'submitted_at': submission.submitted_at,
        'graded_at': submission.graded_at,
        'grading_error': submission.grading_error,
        'answers': answers_payload,
    }


# ===========================================================================
# Coding exercises (Phase 2)
# ===========================================================================
#
# Sensitive instructor-only fields are NEVER declared on these serializers
# (absence > conditional removal, per CLAUDE.md):
#   - CodingExerciseLanguageConfig.solution_code is not declared on
#     _LearnerCodingLanguageConfigSerializer.
#   - Hidden CodingTestCases are filtered upstream in the service layer; the
#     learner test-case serializer only ever sees visible rows.
#
# The redaction serializer for submission test result rows
# (_LearnerCodingSubmissionTestResultSerializer) DOES inherit hidden rows --
# it has to, since Submit runs every test case. It blanks input/expected/
# actual when is_hidden=True while still surfacing status + runtime_ms so
# the learner can see whether their hidden tests passed.


class _LearnerCodingLanguageConfigSerializer(serializers.Serializer):
    """Per-language starter code. solution_code is intentionally absent."""

    id = serializers.IntegerField()
    language = serializers.CharField()
    starter_code = serializers.CharField()


class _LearnerCodingTestCaseSerializer(serializers.Serializer):
    """Visible test case (hidden rows are filtered upstream)."""

    id = serializers.IntegerField()
    input_data = serializers.CharField()
    expected_output = serializers.CharField()
    explanation = serializers.CharField()
    position = serializers.IntegerField()


class _LearnerCodingSubmissionSummarySerializer(serializers.Serializer):
    """Compact submission summary attached to the exercise detail view."""

    id = serializers.IntegerField()
    status = serializers.CharField()
    score = serializers.DecimalField(max_digits=5, decimal_places=2)
    passed_tests = serializers.IntegerField()
    total_tests = serializers.IntegerField()
    runtime_ms = serializers.IntegerField()
    submitted_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)


class LearnerCodingExerciseDetailSerializer(serializers.Serializer):
    """Detail payload for /learn/coding-exercises/<id>/."""

    id = serializers.IntegerField()
    section_id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()
    problem_statement = serializers.CharField()
    difficulty = serializers.CharField()
    default_language = serializers.CharField()
    supported_languages = serializers.ListField(child=serializers.CharField())
    time_limit_ms = serializers.IntegerField()
    language_configs = serializers.SerializerMethodField()
    test_cases = serializers.SerializerMethodField()
    latest_submission = serializers.SerializerMethodField()

    def get_language_configs(self, exercise):
        configs = getattr(exercise, '_prefetched_language_configs', None) or list(
            exercise.language_configs.all().order_by('language')
        )
        return _LearnerCodingLanguageConfigSerializer(configs, many=True).data

    def get_test_cases(self, exercise):
        cases = getattr(exercise, '_prefetched_test_cases', None) or list(
            exercise.test_cases.filter(is_hidden=False).order_by('position', 'id')
        )
        return _LearnerCodingTestCaseSerializer(cases, many=True).data

    def get_latest_submission(self, exercise):
        latest = self.context.get('latest_submission')
        if latest is None:
            return None
        return _LearnerCodingSubmissionSummarySerializer(latest).data


class _LearnerCodingSubmissionTestResultSerializer(serializers.Serializer):
    """Per-test result row, redacted when is_hidden=True.

    Hidden tests still surface status + runtime so the learner can SEE
    whether they passed, just not what was in them.
    """

    id = serializers.IntegerField()
    position = serializers.IntegerField()
    status = serializers.CharField()
    runtime_ms = serializers.IntegerField()
    exit_code = serializers.IntegerField()
    is_hidden = serializers.BooleanField()
    input_data = serializers.CharField()
    expected_output = serializers.CharField()
    actual_output = serializers.CharField()
    stdout = serializers.CharField()
    stderr = serializers.CharField()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.is_hidden:
            data['input_data'] = ''
            data['expected_output'] = ''
            data['actual_output'] = ''
            data['stdout'] = ''
            # stderr stays — a hidden test that errored still needs a
            # message so the learner knows what went wrong. The grader
            # shapes that string and it doesn't reveal expected output.
        return data


class LearnerCodingSubmissionSerializer(serializers.Serializer):
    """Full Submit-mode submission shape with per-test results."""

    id = serializers.IntegerField()
    exercise_id = serializers.IntegerField()
    language = serializers.CharField()
    code = serializers.CharField()
    status = serializers.CharField()
    total_tests = serializers.IntegerField()
    passed_tests = serializers.IntegerField()
    score = serializers.DecimalField(max_digits=5, decimal_places=2)
    runtime_ms = serializers.IntegerField()
    error_message = serializers.CharField()
    stdout = serializers.CharField()
    stderr = serializers.CharField()
    submitted_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)
    test_results = serializers.SerializerMethodField()

    def get_test_results(self, submission):
        # Hidden test rows are excluded entirely from the learner response.
        # Learner still sees aggregate counts (total_tests / passed_tests /
        # score) which include hidden tests, so they can infer hidden
        # verdict from the mismatch between len(test_results) and total_tests.
        rows = list(
            submission.test_results
            .filter(is_hidden=False)
            .order_by('position', 'id')
        )
        return _LearnerCodingSubmissionTestResultSerializer(rows, many=True).data


class CodingRunSubmitSerializer(serializers.Serializer):
    """Input validator for POST /run/ and POST /submit/."""

    language = serializers.CharField(max_length=20)
    code = serializers.CharField(allow_blank=False)


def build_coding_run_result_payload(task_result: dict | None) -> dict | None:
    """Wrap the dict returned by evaluate_coding_run_task into the polling
    endpoint's response shape. Returns None if `task_result` is None
    (PENDING / STARTED branches).
    """
    if task_result is None:
        return None
    # The task already produces a learner-safe shape (visible tests only).
    # No further redaction required at this layer.
    return task_result

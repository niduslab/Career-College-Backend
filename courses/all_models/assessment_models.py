from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from courses.all_models.content_models import SectionContent
from courses.all_models.course_models import AuthoredModel, CourseSection, TimestampedModel


# Coding exercises — instructor-authored programming problems

class CodingExercise(AuthoredModel):
    """Coding problem attached to a section; ordered via SectionContent."""

    class Difficulty(models.TextChoices):
        EASY = 'easy', 'Easy'
        MEDIUM = 'medium', 'Medium'
        HARD = 'hard', 'Hard'

    section = models.ForeignKey(
        CourseSection,
        on_delete=models.CASCADE,
        related_name='coding_exercises',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    problem_statement = models.TextField()
    difficulty = models.CharField(
        max_length=10,
        choices=Difficulty.choices,
        default=Difficulty.EASY,
        db_index=True,
    )
    default_language = models.CharField(max_length=20, default='python')
    supported_languages = models.JSONField(
        default=list,
        help_text='e.g. ["python", "javascript", "cpp", "java"]',
    )
    time_limit_ms = models.PositiveIntegerField(default=2000)
    section_content = GenericRelation(
        SectionContent,
        content_type_field='content_type',
        object_id_field='object_id',
        related_query_name='coding_exercise',
    )

    class Meta:
        db_table = 'coding_exercises'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['section', 'difficulty'], name='idx_coding_section_difficulty'),
        ]

    def __str__(self):
        return self.title


class CodingExerciseLanguageConfig(TimestampedModel):
    """Per-language starter and solution code for a CodingExercise."""

    class Language(models.TextChoices):
        PYTHON = 'python', 'Python'
        JAVASCRIPT = 'javascript', 'JavaScript'
        CPP = 'cpp', 'C++'
        JAVA = 'java', 'Java'

    exercise = models.ForeignKey(
        CodingExercise,
        on_delete=models.CASCADE,
        related_name='language_configs',
    )
    language = models.CharField(max_length=20, choices=Language.choices)
    starter_code = models.TextField(blank=True, default='')
    # solution_code must NEVER appear in any learner-facing serializer
    solution_code = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'coding_exercise_language_configs'
        constraints = [
            models.UniqueConstraint(fields=['exercise', 'language'], name='uniq_coding_lang_config'),
        ]
        indexes = [
            models.Index(fields=['exercise', 'language'], name='idx_coding_lang_config'),
        ]

    def __str__(self):
        return f'{self.exercise.title} — {self.language}'


class CodingTestCase(models.Model):
    """Input/output test case for a CodingExercise. Hidden cases are grading-only."""

    exercise = models.ForeignKey(
        CodingExercise,
        on_delete=models.CASCADE,
        related_name='test_cases',
    )
    input_data = models.TextField()
    expected_output = models.TextField()
    is_hidden = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Hidden cases are used for grading only and never shown to learners.',
    )
    explanation = models.CharField(max_length=255, blank=True, default='')
    position = models.PositiveIntegerField(default=1, db_index=True)

    class Meta:
        db_table = 'coding_test_cases'
        ordering = ['exercise_id', 'position', 'id']
        constraints = [
            models.UniqueConstraint(fields=['exercise', 'position'], name='uniq_testcase_exercise_position'),
        ]
        indexes = [
            models.Index(fields=['exercise', 'position'], name='idx_coding_testcase_pos'),
        ]

    def __str__(self):
        return f'TestCase {self.position} for exercise {self.exercise_id}'


# Quiz system — MCQ-based assessments via SectionContent

class Quiz(AuthoredModel):
    """Practice quiz belonging to a section."""

    section = models.ForeignKey(
        CourseSection,
        on_delete=models.CASCADE,
        related_name='quizzes',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    related_lectures = models.ManyToManyField(
        'Lecture',
        related_name='related_quizzes',
        blank=True,
        help_text='Lectures this quiz is intended to assess. All must belong to the same section.',
    )
    # Cascade-deletes SectionContent rows when this quiz is deleted.
    section_content = GenericRelation(
        SectionContent,
        content_type_field='content_type',
        object_id_field='object_id',
        related_query_name='quiz',
    )

    class Meta:
        db_table = 'quizzes'
        verbose_name = 'Quiz'
        verbose_name_plural = 'Quizzes'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['section', '-created_at'], name='idx_quiz_section_date'),
        ]

    def validate_related_lectures(self):
        """
        Enforce that every related lecture belongs to this quiz's section.
        M2M cannot be validated in clean() on unsaved instances; call this
        explicitly from the serializer after the M2M relationship is set.
        """
        invalid = self.related_lectures.exclude(section=self.section)
        if invalid.exists():
            raise ValidationError(
                {'related_lectures': 'All related lectures must belong to the same section as this quiz.'}
            )

    def __str__(self):
        return f'{self.title} (Section: {self.section_id})'


class QuizQuestion(models.Model):
    """Single MCQ question within a quiz."""

    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='questions',
    )
    question_text = models.TextField()
    position = models.PositiveIntegerField(default=1, db_index=True)

    class Meta:
        db_table = 'quiz_questions'
        verbose_name = 'Quiz Question'
        verbose_name_plural = 'Quiz Questions'
        ordering = ['quiz_id', 'position', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['quiz', 'position'],
                name='uniq_quizquestion_quiz_position',
            ),
        ]
        indexes = [
            models.Index(fields=['quiz', 'position'], name='idx_qquestion_quiz_position'),
        ]

    def __str__(self):
        return f'Q{self.position}: {self.question_text[:80]}'


class QuizAnswer(models.Model):
    """Answer option for a quiz question. Exactly one answer per question may be correct."""

    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name='answers',
    )
    answer_text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = 'quiz_answers'
        verbose_name = 'Quiz Answer'
        verbose_name_plural = 'Quiz Answers'
        ordering = ['question_id', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['question'],
                condition=models.Q(is_correct=True),
                name='uniq_correct_answer_per_question',
            ),
        ]
        indexes = [
            models.Index(fields=['question', 'is_correct'], name='idx_qanswer_question_correct'),
        ]

    def clean(self):
        super().clean()
        if self.is_correct:
            qs = QuizAnswer.objects.filter(question=self.question, is_correct=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'is_correct': 'Each question may have only one correct answer.'})

    def __str__(self):
        marker = ' [correct]' if self.is_correct else ''
        return f'{self.answer_text}{marker}'


# Quiz attempts — learner submission records.

class QuizAttempt(TimestampedModel):
    """A learner's submission of a quiz.

    Each submit creates a new attempt + one QuizAttemptAnswer per question;
    `is_correct` is denormalized onto the answer row at submit time so later
    answer-key edits don't rewrite historical verdicts.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quiz_attempts',
    )
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='attempts',
    )
    score = models.PositiveIntegerField(default=0)
    max_score = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'quiz_attempts'
        verbose_name = 'Quiz Attempt'
        verbose_name_plural = 'Quiz Attempts'
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['user', 'quiz', '-submitted_at'], name='idx_qattempt_user_quiz_date'),
            models.Index(fields=['quiz', '-submitted_at'], name='idx_qattempt_quiz_date'),
        ]

    def __str__(self):
        return f'Attempt {self.pk}: {self.user} on quiz {self.quiz_id} ({self.score}/{self.max_score})'


class QuizAttemptAnswer(models.Model):
    """One per-question record within a QuizAttempt."""

    attempt = models.ForeignKey(
        QuizAttempt,
        on_delete=models.CASCADE,
        related_name='answers',
    )
    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name='attempt_answers',
    )
    # Nullable so an attempt can record a skipped/unanswered question.
    selected_answer = models.ForeignKey(
        QuizAnswer,
        on_delete=models.SET_NULL,
        related_name='attempt_answers',
        null=True,
        blank=True,
    )
    is_correct = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = 'quiz_attempt_answers'
        verbose_name = 'Quiz Attempt Answer'
        verbose_name_plural = 'Quiz Attempt Answers'
        ordering = ['attempt_id', 'question_id']
        constraints = [
            models.UniqueConstraint(
                fields=['attempt', 'question'],
                name='uniq_attempt_answer_per_question',
            ),
        ]
        indexes = [
            models.Index(fields=['attempt', 'question'], name='idx_qaanswer_attempt_question'),
        ]

    def __str__(self):
        verdict = 'correct' if self.is_correct else 'wrong'
        return f'Attempt {self.attempt_id} Q{self.question_id} [{verdict}]'


# Assignments — instructor-authored open-ended questions, ordered via SectionContent.

class Assignment(AuthoredModel):
    """Open-ended assignment attached to a section; ordered via SectionContent."""

    section = models.ForeignKey(
        CourseSection,
        on_delete=models.CASCADE,
        related_name='assignments',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    instructions = models.TextField(blank=True, default='')
    total_score = models.PositiveIntegerField(default=0)
    passing_score = models.PositiveIntegerField(default=0)
    section_content = GenericRelation(
        SectionContent,
        content_type_field='content_type',
        object_id_field='object_id',
        related_query_name='assignment',
    )

    class Meta:
        db_table = 'courses_assignment'
        verbose_name = 'Assignment'
        verbose_name_plural = 'Assignments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['section', '-created_at'], name='idx_assignment_section_date'),
        ]

    def __str__(self):
        return self.title


class AssignmentQuestion(models.Model):
    """Single open-ended question within an Assignment."""

    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='questions',
    )
    question_text = models.TextField()
    # model_answer is INSTRUCTOR-ONLY and must never be exposed to learners.
    model_answer = models.TextField(blank=True, default='')
    # rubric drives auto-grading. List of criterion objects; INSTRUCTOR-ONLY.
    rubric = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'List of grading criteria, e.g. '
            '[{"type": "keyword", "value": "x", "points": 2, '
            '"feedback_on_match": "...", "feedback_on_miss": "..."}]. '
            'Sum of criterion.points must equal this question.points.'
        ),
    )
    points = models.PositiveIntegerField(default=10)
    hint = models.TextField(blank=True, default='')
    position = models.PositiveIntegerField(db_index=True)

    class Meta:
        db_table = 'courses_assignment_question'
        verbose_name = 'Assignment Question'
        verbose_name_plural = 'Assignment Questions'
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(
                fields=['assignment', 'position'],
                name='uniq_aquestion_assignment_position',
            ),
        ]
        indexes = [
            models.Index(fields=['assignment', 'position'], name='idx_aquestion_assign_position'),
        ]

    def __str__(self):
        return f'Q{self.position}: {self.question_text[:80]}'


# Assignment submissions — learner attempts at an assignment.

class AssignmentSubmission(TimestampedModel):
    """A learner's submission of an Assignment.

    Graded out-of-band by grade_assignment_submission_task: submitted -> grading
    -> passed | failed | grading_failed. Answer rows snapshot rubric + points so
    historical submissions survive later question edits. See
    docs/architecture in CLAUDE.md (Learner Consumption Endpoints).
    """

    class Status(models.TextChoices):
        SUBMITTED = 'submitted', 'Submitted'
        GRADING = 'grading', 'Grading'
        PASSED = 'passed', 'Passed'
        FAILED = 'failed', 'Failed'
        GRADING_FAILED = 'grading_failed', 'Grading failed'

    TERMINAL_STATUSES = (Status.PASSED, Status.FAILED, Status.GRADING_FAILED)
    IN_FLIGHT_STATUSES = (Status.SUBMITTED, Status.GRADING)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assignment_submissions',
    )
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='submissions',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    graded_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SUBMITTED,
        db_index=True,
    )
    total_score = models.PositiveIntegerField(default=0)
    max_score = models.PositiveIntegerField()
    grading_error = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'assignment_submissions'
        verbose_name = 'Assignment Submission'
        verbose_name_plural = 'Assignment Submissions'
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['user', 'assignment', '-submitted_at'], name='idx_asub_user_assign_date'),
            models.Index(fields=['assignment', 'status'], name='idx_asub_assign_status'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'assignment'],
                condition=Q(status__in=['submitted', 'grading']),
                name='uniq_inflight_assignment_submission',
            ),
        ]

    def __str__(self):
        return (
            f'Submission {self.pk}: {self.user} on assignment {self.assignment_id} '
            f'[{self.status} {self.total_score}/{self.max_score}]'
        )


class AssignmentSubmissionAnswer(models.Model):
    """One per-question record within an AssignmentSubmission."""

    submission = models.ForeignKey(
        AssignmentSubmission,
        on_delete=models.CASCADE,
        related_name='answers',
    )
    question = models.ForeignKey(
        AssignmentQuestion,
        on_delete=models.CASCADE,
        related_name='+',
    )
    answer_text = models.TextField(blank=True, default='')
    score = models.PositiveIntegerField(default=0)
    max_score = models.PositiveIntegerField()
    # Frozen at submit time so later rubric edits don't rewrite past grades.
    rubric_snapshot = models.JSONField(default=list, blank=True)
    # Written by the grader; one dict per rubric criterion.
    criterion_results = models.JSONField(default=list, blank=True)
    feedback = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'assignment_submission_answers'
        verbose_name = 'Assignment Submission Answer'
        verbose_name_plural = 'Assignment Submission Answers'
        ordering = ['submission_id', 'question_id']
        constraints = [
            models.UniqueConstraint(
                fields=['submission', 'question'],
                name='uniq_submission_question',
            ),
        ]
        indexes = [
            models.Index(fields=['submission', 'question'], name='idx_asubans_sub_question'),
        ]

    def __str__(self):
        return (
            f'AnswerRow sub={self.submission_id} q={self.question_id} '
            f'[{self.score}/{self.max_score}]'
        )


# Coding submissions — learner attempts at a CodingExercise.

class CodingSubmission(TimestampedModel):
    """A learner's persisted submission of a CodingExercise.

    Graded out-of-band by evaluate_coding_submission_task: queued -> grading ->
    passed | failed | error. See docs/architecture/09-coding-exercises.md.
    """

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        GRADING = 'grading', 'Grading'
        PASSED = 'passed', 'Passed'
        FAILED = 'failed', 'Failed'
        ERROR = 'error', 'Error'

    TERMINAL_STATUSES = (Status.PASSED, Status.FAILED, Status.ERROR)
    IN_FLIGHT_STATUSES = (Status.QUEUED, Status.GRADING)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='coding_submissions',
    )
    exercise = models.ForeignKey(
        CodingExercise,
        on_delete=models.CASCADE,
        related_name='submissions',
    )
    language = models.CharField(max_length=20)
    code = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    total_tests = models.PositiveIntegerField(default=0)
    passed_tests = models.PositiveIntegerField(default=0)
    score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    runtime_ms = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    stdout = models.TextField(blank=True, default='')
    stderr = models.TextField(blank=True, default='')
    submitted_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'coding_submissions'
        verbose_name = 'Coding Submission'
        verbose_name_plural = 'Coding Submissions'
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['user', 'exercise', '-submitted_at'], name='idx_csub_user_ex_date'),
            models.Index(fields=['status'], name='idx_csub_status'),
            models.Index(fields=['submitted_at'], name='idx_csub_submitted_at'),
        ]

    def __str__(self):
        return (
            f'CodingSubmission {self.pk}: {self.user} on exercise {self.exercise_id} '
            f'[{self.status} {self.passed_tests}/{self.total_tests}]'
        )


class CodingSubmissionTestResult(models.Model):
    """One per-test execution record within a CodingSubmission."""

    class Status(models.TextChoices):
        PASSED = 'passed', 'Passed'
        FAILED = 'failed', 'Failed'
        ERROR = 'error', 'Error'

    submission = models.ForeignKey(
        CodingSubmission,
        on_delete=models.CASCADE,
        related_name='test_results',
    )
    # Nullable on delete so result rows survive when the test case is later
    # removed -- the snapshot fields below carry the input/output anyway.
    test_case = models.ForeignKey(
        CodingTestCase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        db_index=True,
    )
    input_data = models.TextField(blank=True, default='')
    expected_output = models.TextField(blank=True, default='')
    actual_output = models.TextField(blank=True, default='')
    stdout = models.TextField(blank=True, default='')
    stderr = models.TextField(blank=True, default='')
    runtime_ms = models.PositiveIntegerField(default=0)
    exit_code = models.IntegerField(default=0)
    is_hidden = models.BooleanField(default=False, db_index=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'coding_submission_test_results'
        verbose_name = 'Coding Submission Test Result'
        verbose_name_plural = 'Coding Submission Test Results'
        ordering = ['submission_id', 'position', 'id']
        indexes = [
            models.Index(fields=['submission', 'position'], name='idx_csubres_sub_pos'),
        ]

    def __str__(self):
        return f'TestResult sub={self.submission_id} pos={self.position} [{self.status}]'

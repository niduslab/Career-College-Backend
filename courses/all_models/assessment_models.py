from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import ValidationError
from django.db import models

from courses.all_models.content_models import SectionContent
from courses.all_models.course_models import CourseSection, TimestampedModel


# =============================================================================
# Coding exercises — instructor-authored programming problems (Part 1: CRUD only)
# =============================================================================

class CodingExercise(TimestampedModel):
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


# =============================================================================
# NEW: Quiz system — MCQ-based assessments integrated via SectionContent
# =============================================================================

class Quiz(TimestampedModel):
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


# =============================================================================
# Quiz attempts — learner submission records for the Phase-2 consumption surface.
# Each call to POST /api/v1/courses/learn/quizzes/{id}/submit/ creates a new
# QuizAttempt with one QuizAttemptAnswer per question in the quiz. The
# `is_correct` flag on QuizAttemptAnswer is denormalized at submit time so
# that re-rendering an old attempt's verdict doesn't depend on the answer
# key still matching — instructor edits to QuizAnswer.is_correct after a
# learner has attempted the quiz won't retroactively rewrite the attempt.
# =============================================================================

class QuizAttempt(TimestampedModel):
    """A single learner's submission of a quiz."""

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


# =============================================================================
# Assignments — instructor-authored open-ended questions with model answers.
# Ordered via SectionContent like Lecture / Quiz / CodingExercise.
# =============================================================================

class Assignment(TimestampedModel):
    """Open-ended assignment attached to a section; ordered via SectionContent."""

    section = models.ForeignKey(
        CourseSection,
        on_delete=models.CASCADE,
        related_name='assignments',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    instructions = models.TextField(blank=True, default='')
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


# =============================================================================

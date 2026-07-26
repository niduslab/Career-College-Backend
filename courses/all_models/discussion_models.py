from django.conf import settings
from django.db import models

from courses.all_models.content_models import SectionContent
from courses.all_models.course_models import NidusCourse, TimestampedModel


class CourseQuestion(TimestampedModel):
    """A question / discussion thread started by an enrolled learner (or instructor).

    Optionally anchored to a specific content item (`related_content`) so a
    learner can ask "about this lecture/quiz". A null `related_content` is a
    general, course-level question.

    Access to the whole Q&A surface is gated at the service layer — only active
    enrolled learners and the course's own instructors may read or write.
    """

    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='questions',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_questions',
    )
    # Optional anchor to the lecture/quiz/assignment/coding slot the question is
    # about. SET_NULL so deleting the content doesn't erase the discussion.
    related_content = models.ForeignKey(
        SectionContent,
        on_delete=models.SET_NULL,
        related_name='questions',
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    is_pinned = models.BooleanField(default=False, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    reply_count = models.PositiveIntegerField(default=0)
    upvote_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'course_questions'
        verbose_name = 'Course Question'
        verbose_name_plural = 'Course Questions'
        ordering = ['-is_pinned', '-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                check=models.Q(reply_count__gte=0),
                name='chk_question_reply_count_non_negative',
            ),
            models.CheckConstraint(
                check=models.Q(upvote_count__gte=0),
                name='chk_question_upvote_count_non_negative',
            ),
        ]
        indexes = [
            models.Index(fields=['course', 'is_deleted', '-created_at'], name='idx_question_course_date'),
            models.Index(fields=['course', 'is_deleted', '-upvote_count'], name='idx_question_course_upvotes'),
            models.Index(fields=['related_content'], name='idx_question_related_content'),
        ]

    def __str__(self):
        return f'Question {self.pk} by {self.author_id} on course {self.course_id}'


class QuestionReply(TimestampedModel):
    """A reply within a question thread. Two-level threading (question → replies).

    `is_instructor_reply` is denormalized at create time so the UI can badge
    instructor answers without a join back to the course roster.
    """

    question = models.ForeignKey(
        CourseQuestion,
        on_delete=models.CASCADE,
        related_name='replies',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='question_replies',
    )
    body = models.TextField()
    is_instructor_reply = models.BooleanField(default=False, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    upvote_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'question_replies'
        verbose_name = 'Question Reply'
        verbose_name_plural = 'Question Replies'
        ordering = ['created_at', 'id']
        constraints = [
            models.CheckConstraint(
                check=models.Q(upvote_count__gte=0),
                name='chk_reply_upvote_count_non_negative',
            ),
        ]
        indexes = [
            models.Index(fields=['question', 'is_deleted', 'created_at'], name='idx_reply_question_date'),
        ]

    def __str__(self):
        return f'Reply {self.pk} by {self.author_id} on question {self.question_id}'

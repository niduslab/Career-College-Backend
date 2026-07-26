"""Business logic for the course Q&A / discussion feature.

Access rule (enforced ONLY here, never in a view):
    A caller may read or write a course's Q&A when they are an active enrolled
    learner OR one of the course's own instructors OR its partner-institution
    owner OR a platform admin. Everyone else is denied.

Access-denied status follows the project convention:
    slug-based entry (list/create under <slug>/questions/) → 403
    numeric-id entry (question/reply detail, replies, votes)  → 404
"""

import logging

from django.db import transaction
from django.db.models import F, Prefetch, QuerySet
from django.utils import timezone

from courses.all_models.content_models import SectionContent
from courses.all_models.course_models import NidusCourse
from courses.all_models.discussion_models import (
    CourseQuestion,
    QuestionReply,
)
from courses.services.learner_service import resolve_course_access

logger = logging.getLogger(__name__)


class DiscussionError(Exception):
    """Raised by discussion service functions for domain-rule violations."""

    def __init__(self, message: str, http_status: int = 422):
        self.message = message
        self.http_status = http_status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Access resolution
# ---------------------------------------------------------------------------

def _assert_access(user, course: NidusCourse, *, not_found_status: int) -> bool:
    """Assert the caller may use this course's Q&A. Returns is_instructor.

    Raises DiscussionError(not_found_status) when the caller has no access —
    403 for slug entry points, 404 for numeric-id entry points.
    """
    # Platform admins and the course creator are resolved from already-loaded
    # columns, so they never pay for the roster/enrollment lookup below.
    if user.is_staff or getattr(user, 'user_type', None) == 'admin':
        return True
    if course.created_by_id == user.pk:
        return True

    # resolve_course_access already evaluates course.instructors — don't
    # re-query the roster here.
    is_instructor, enrollment = resolve_course_access(user, course)
    if is_instructor:
        return True
    if enrollment is not None:
        return False
    raise DiscussionError(
        'You must be enrolled in this course to access its discussion.',
        http_status=not_found_status,
    )


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

def _resolve_related_content(course: NidusCourse, content_id):
    """Validate an optional related-content id belongs to this course."""
    if content_id in (None, ''):
        return None
    content = (
        SectionContent.objects
        .filter(pk=content_id, section__course_id=course.pk)
        .first()
    )
    if content is None:
        raise DiscussionError('The referenced content does not belong to this course.', http_status=400)
    return content


def create_question(user, course: NidusCourse, validated_data: dict) -> CourseQuestion:
    """Create a question. Caller access is asserted before write (slug → 403)."""
    _assert_access(user, course, not_found_status=403)
    related_content = _resolve_related_content(course, validated_data.get('related_content_id'))

    with transaction.atomic():
        question = CourseQuestion.objects.create(
            course=course,
            author=user,
            related_content=related_content,
            title=validated_data['title'],
            body=validated_data['body'],
        )
        q_pk = question.pk
        c_pk = course.pk
        c_title = course.title
        c_slug = course.slug
        transaction.on_commit(
            lambda: _dispatch_question_posted(c_pk, c_title, c_slug, q_pk, user.pk)
        )

    return question


def list_questions(user, course: NidusCourse, params) -> QuerySet:
    """Return filtered, ordered questions for a course (slug → 403 on no access).

    Supports ?content_id= (only questions anchored to one content item) and
    ?ordering= (-created_at | created_at | -upvote_count | -reply_count).
    """
    _assert_access(user, course, not_found_status=403)

    qs = (
        CourseQuestion.objects
        .filter(course=course, is_deleted=False)
        .select_related('author', 'related_content')
    )

    content_id = params.get('content_id')
    if content_id is not None:
        try:
            qs = qs.filter(related_content_id=int(content_id))
        except (ValueError, TypeError):
            raise DiscussionError(f'"{content_id}" is not a valid content id.', http_status=400)

    _VALID_ORDERINGS = {'-created_at', 'created_at', '-upvote_count', '-reply_count'}
    ordering = params.get('ordering', '-created_at')
    if ordering not in _VALID_ORDERINGS:
        ordering = '-created_at'

    # is_pinned always floats to the top regardless of the secondary ordering.
    return qs.order_by('-is_pinned', ordering, '-id')


def get_question_with_replies(user, question_id: int) -> CourseQuestion:
    """Fetch a question + its non-deleted replies. Numeric id → 404 on no access."""
    question = (
        CourseQuestion.objects
        .filter(pk=question_id, is_deleted=False)
        .select_related('author', 'course', 'related_content')
        .prefetch_related(
            Prefetch(
                'replies',
                queryset=QuestionReply.objects.filter(is_deleted=False).select_related('author'),
            )
        )
        .first()
    )
    if question is None:
        raise DiscussionError('Question not found.', http_status=404)

    _assert_access(user, question.course, not_found_status=404)
    return question


def delete_question(user, question_id: int) -> None:
    """Soft-delete a question. Author or a course instructor only (id → 404)."""
    question = (
        CourseQuestion.objects
        .filter(pk=question_id, is_deleted=False)
        .select_related('course')
        .first()
    )
    if question is None:
        raise DiscussionError('Question not found.', http_status=404)

    is_instructor = _assert_access(user, question.course, not_found_status=404)
    if question.author_id != user.pk and not is_instructor:
        raise DiscussionError('You can only delete your own question.', http_status=403)

    CourseQuestion.objects.filter(pk=question.pk).update(
        is_deleted=True, updated_at=timezone.now()
    )


def toggle_pin(user, question_id: int) -> CourseQuestion:
    """Pin / unpin a question. Course instructors only (id → 404)."""
    question = (
        CourseQuestion.objects
        .filter(pk=question_id, is_deleted=False)
        .select_related('course')
        .first()
    )
    if question is None:
        raise DiscussionError('Question not found.', http_status=404)

    is_instructor = _assert_access(user, question.course, not_found_status=404)
    if not is_instructor:
        raise DiscussionError('Only instructors can pin a question.', http_status=403)

    question.is_pinned = not question.is_pinned
    question.save(update_fields=['is_pinned', 'updated_at'])
    return question


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------

def create_reply(user, question_id: int, validated_data: dict) -> QuestionReply:
    """Post a reply to a question. Numeric id → 404 on no access."""
    question = (
        CourseQuestion.objects
        .filter(pk=question_id, is_deleted=False)
        .select_related('course')
        .first()
    )
    if question is None:
        raise DiscussionError('Question not found.', http_status=404)

    is_instructor = _assert_access(user, question.course, not_found_status=404)

    with transaction.atomic():
        reply = QuestionReply.objects.create(
            question=question,
            author=user,
            body=validated_data['body'],
            is_instructor_reply=is_instructor,
        )
        CourseQuestion.objects.filter(pk=question.pk).update(
            reply_count=F('reply_count') + 1, updated_at=timezone.now()
        )
        q_pk = question.pk
        q_title = question.title
        c_slug = question.course.slug
        author_id = question.author_id
        transaction.on_commit(
            lambda: _dispatch_question_replied(q_pk, q_title, c_slug, author_id, user.pk, is_instructor)
        )

    return reply


def delete_reply(user, reply_id: int) -> None:
    """Soft-delete a reply. Author or a course instructor only (id → 404)."""
    reply = (
        QuestionReply.objects
        .filter(pk=reply_id, is_deleted=False)
        .select_related('question', 'question__course')
        .first()
    )
    if reply is None:
        raise DiscussionError('Reply not found.', http_status=404)

    is_instructor = _assert_access(user, reply.question.course, not_found_status=404)
    if reply.author_id != user.pk and not is_instructor:
        raise DiscussionError('You can only delete your own reply.', http_status=403)

    with transaction.atomic():
        QuestionReply.objects.filter(pk=reply.pk).update(
            is_deleted=True, updated_at=timezone.now()
        )
        CourseQuestion.objects.filter(pk=reply.question_id, reply_count__gt=0).update(
            reply_count=F('reply_count') - 1, updated_at=timezone.now()
        )


# ---------------------------------------------------------------------------
# Upvotes (counter-only — no per-user vote record; see docs/architecture/26)
# ---------------------------------------------------------------------------

def upvote_question(user, question_id: int) -> dict:
    """Increment a question's upvote counter. Numeric id → 404 on no access.

    Counter-only by design: there is no per-user vote row, so this does not
    dedup repeat calls and there is no un-upvote. Kept deliberately simple.
    """
    question = (
        CourseQuestion.objects
        .filter(pk=question_id, is_deleted=False)
        .select_related('course')
        .first()
    )
    if question is None:
        raise DiscussionError('Question not found.', http_status=404)

    _assert_access(user, question.course, not_found_status=404)
    return _increment_upvote(CourseQuestion, question.pk)


def upvote_reply(user, reply_id: int) -> dict:
    """Increment a reply's upvote counter. Numeric id → 404 on no access."""
    reply = (
        QuestionReply.objects
        .filter(pk=reply_id, is_deleted=False)
        .select_related('question', 'question__course')
        .first()
    )
    if reply is None:
        raise DiscussionError('Reply not found.', http_status=404)

    _assert_access(user, reply.question.course, not_found_status=404)
    return _increment_upvote(QuestionReply, reply.pk)


def _increment_upvote(model, pk: int) -> dict:
    """Atomic F()+1 bump of the denormalized upvote counter."""
    model.objects.filter(pk=pk).update(
        upvote_count=F('upvote_count') + 1, updated_at=timezone.now()
    )
    return {'upvote_count': model.objects.values_list('upvote_count', flat=True).get(pk=pk)}


# ---------------------------------------------------------------------------
# Notifications (in-app only — skip_email, mirrors review_service)
# ---------------------------------------------------------------------------

def _dispatch_question_posted(course_pk, course_title, course_slug, question_id, author_id):
    from notifications.models import NotificationEventType
    from notifications.services.dispatcher import dispatch
    try:
        course = NidusCourse.objects.prefetch_related('instructors').get(pk=course_pk)
        # Notify every instructor except the person who asked (an instructor
        # can also start a thread).
        recipients = [u for u in course.instructors.all() if u.pk != author_id]
        if course.created_by_id and course.created_by_id != author_id:
            if all(u.pk != course.created_by_id for u in recipients):
                recipients.append(course.created_by)
        if recipients:
            dispatch(
                NotificationEventType.QUESTION_POSTED,
                recipients,
                context={
                    'course_title': course_title,
                    'course_slug': course_slug,
                    'question_id': question_id,
                },
                skip_email=True,
            )
    except Exception:
        logger.warning('QUESTION_POSTED dispatch failed for course=%s', course_pk)


def _dispatch_question_replied(question_id, question_title, course_slug, question_author_id, replier_id, is_instructor):
    from notifications.models import NotificationEventType
    from notifications.services.dispatcher import dispatch
    try:
        User = _user_model()
        recipient_ids = set()
        # Always notify the question author (unless they replied to themselves).
        if question_author_id != replier_id:
            recipient_ids.add(question_author_id)
        # Notify prior participants in the thread (exclude the replier).
        prior = (
            QuestionReply.objects
            .filter(question_id=question_id, is_deleted=False)
            .exclude(author_id=replier_id)
            .values_list('author_id', flat=True)
        )
        recipient_ids.update(prior)
        recipient_ids.discard(replier_id)
        if not recipient_ids:
            return
        recipients = list(User.objects.filter(pk__in=recipient_ids))
        if recipients:
            dispatch(
                NotificationEventType.QUESTION_REPLIED,
                recipients,
                context={
                    'question_title': question_title,
                    'course_slug': course_slug,
                    'question_id': question_id,
                    'is_instructor_reply': is_instructor,
                },
                skip_email=True,
            )
    except Exception:
        logger.warning('QUESTION_REPLIED dispatch failed for question=%s', question_id)


def _user_model():
    from django.contrib.auth import get_user_model
    return get_user_model()

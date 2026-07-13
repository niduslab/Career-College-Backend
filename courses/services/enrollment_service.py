import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, F, IntegerField, Q, QuerySet, When
from django.utils import timezone

from courses.models import (
    AssignmentSubmission,
    CodingSubmission,
    Enrollment,
    NidusCourse,
    QuizAttempt,
    SectionContent,
    WatchProgress,
)

logger = logging.getLogger(__name__)

# 5-minute debounce so every consumption GET doesn't write a DB row.
LAST_ACCESSED_DEBOUNCE = timedelta(minutes=5)


def get_catalog_courses() -> QuerySet[NidusCourse]:
    """Return published courses for the public catalog."""
    return (
        NidusCourse.objects
        .filter(is_published=True)
        .select_related('created_by', 'category')
        .prefetch_related('instructors')
        .order_by('-published_at')
    )


CATALOG_SORT_OPTIONS = frozenset({
    'relevance',
    'newest',
    'popularity',
    'price_asc',
    'price_desc',
    'rating',
})


def _csv_param(params, key):
    """Split a comma-separated query param into a deduped, order-preserving list."""
    raw = params.get(key)
    if not raw:
        return []
    return list(dict.fromkeys(
        token.strip() for token in raw.split(',') if token.strip()
    ))


def _decimal_or_none(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        return None


def _positive_int_or_none(value):
    if value in (None, ''):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _validate_catalog_params(params):
    """Validate catalog filter/sort params. Raises ValidationError with all field errors at once."""
    errors = {}
    raw_sort = params.get('sort')
    if raw_sort and raw_sort not in CATALOG_SORT_OPTIONS:
        errors['sort'] = (
            f'Invalid sort "{raw_sort}". Must be one of: '
            f'{", ".join(sorted(CATALOG_SORT_OPTIONS))}.'
        )

    valid_levels = set(NidusCourse.CourseLevel.values)
    bad_levels = [lvl for lvl in _csv_param(params, 'level') if lvl not in valid_levels]
    if bad_levels:
        errors['level'] = (
            f'Invalid level(s): {", ".join(bad_levels)}. '
            f'Must be one of: {", ".join(sorted(valid_levels))}.'
        )

    for key in ('price_min', 'price_max'):
        raw = params.get(key)
        if raw in (None, ''):
            continue
        try:
            parsed = Decimal(raw)
        except (InvalidOperation, TypeError):
            errors[key] = f'"{raw}" is not a valid number.'
            continue
        if parsed < 0:
            errors[key] = 'Must be non-negative.'

    for key in ('duration_min', 'duration_max'):
        raw = params.get(key)
        if raw in (None, ''):
            continue
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            errors[key] = f'"{raw}" is not a valid integer.'
            continue
        if parsed < 0:
            errors[key] = 'Must be non-negative.'

    raw_rating_min = params.get('rating_min')
    if raw_rating_min not in (None, ''):
        try:
            parsed = Decimal(raw_rating_min)
        except (InvalidOperation, TypeError):
            errors['rating_min'] = f'"{raw_rating_min}" is not a valid number.'
        else:
            if not (Decimal('1') <= parsed <= Decimal('5')):
                errors['rating_min'] = 'Must be between 1 and 5.'

    raw_min_reviews = params.get('min_reviews')
    if raw_min_reviews not in (None, ''):
        try:
            parsed_int = int(raw_min_reviews)
        except (TypeError, ValueError):
            errors['min_reviews'] = f'"{raw_min_reviews}" is not a valid integer.'
        else:
            if parsed_int < 0:
                errors['min_reviews'] = 'Must be non-negative.'

    if errors:
        raise ValidationError(errors)


def filter_catalog_courses(queryset: QuerySet[NidusCourse], params) -> QuerySet[NidusCourse]:
    """Apply filtering and sorting to a catalog queryset. Raises ValidationError on bad params."""
    _validate_catalog_params(params)

    category_slug = params.get('category')
    subcategory_slug = params.get('subcategory')
    if subcategory_slug and category_slug:
        queryset = queryset.filter(
            category__slug=subcategory_slug,
            category__parent__slug=category_slug,
        )
    elif subcategory_slug:
        queryset = queryset.filter(category__slug=subcategory_slug)
    elif category_slug:
        queryset = queryset.filter(
            Q(category__slug=category_slug)
            | Q(category__parent__slug=category_slug)
        )

    levels = _csv_param(params, 'level')
    if levels:
        queryset = queryset.filter(level__in=levels)

    languages = _csv_param(params, 'language')
    if languages:
        lang_q = Q()
        for lang in languages:
            lang_q |= Q(language__iexact=lang)
        queryset = queryset.filter(lang_q)

    price_type = params.get('price_type')
    if price_type == 'free':
        queryset = queryset.filter(price=0)
    elif price_type == 'paid':
        queryset = queryset.filter(price__gt=0)

    price_min = _decimal_or_none(params.get('price_min'))
    if price_min is not None:
        queryset = queryset.filter(price__gte=price_min)

    price_max = _decimal_or_none(params.get('price_max'))
    if price_max is not None:
        queryset = queryset.filter(price__lte=price_max)

    duration_min = _positive_int_or_none(params.get('duration_min'))
    if duration_min is not None:
        queryset = queryset.filter(duration_minutes__gte=duration_min)

    duration_max = _positive_int_or_none(params.get('duration_max'))
    if duration_max is not None:
        queryset = queryset.filter(duration_minutes__lte=duration_max)

    # Search: use full_name — first_name/last_name are never populated.
    search = params.get('search')
    if search:
        queryset = queryset.filter(
            Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(instructors__full_name__icontains=search)
        ).distinct()

    rating_min = _decimal_or_none(params.get('rating_min'))
    if rating_min is not None:
        queryset = queryset.filter(avg_rating__gte=rating_min)

    min_reviews = _positive_int_or_none(params.get('min_reviews'))
    if min_reviews is not None:
        queryset = queryset.filter(review_count__gte=min_reviews)

    sort = params.get('sort')
    if sort not in CATALOG_SORT_OPTIONS:
        sort = 'relevance' if search else 'newest'

    return _apply_catalog_sort(queryset, sort, search)


def _apply_catalog_sort(queryset, sort, search):
    if sort == 'popularity':
        return queryset.annotate(
            _enrollment_count=Count(
                'enrollments',
                filter=Q(enrollments__is_active=True),
                distinct=True,
            )
        ).order_by('-_enrollment_count', F('published_at').desc(nulls_last=True), '-id')

    if sort == 'price_asc':
        return queryset.order_by('price', F('published_at').desc(nulls_last=True), '-id')

    if sort == 'price_desc':
        return queryset.order_by('-price', F('published_at').desc(nulls_last=True), '-id')

    if sort == 'rating':
        return queryset.order_by(F('avg_rating').desc(nulls_last=True), F('published_at').desc(nulls_last=True), '-id')

    if sort == 'relevance' and search:
        return queryset.annotate(
            _relevance=Case(
                When(title__istartswith=search, then=2),
                When(title__icontains=search, then=1),
                default=0,
                output_field=IntegerField(),
            )
        ).order_by('-_relevance', F('published_at').desc(nulls_last=True), '-id')

    # newest (and relevance without a search term)
    return queryset.order_by(F('published_at').desc(nulls_last=True), '-id')


def get_learner_enrollments(user) -> QuerySet[Enrollment]:
    """Return active enrollments for a learner, most recently accessed first."""
    return (
        Enrollment.objects
        .filter(user=user, is_active=True)
        .select_related('course__created_by', 'course__category')
        .prefetch_related('course__instructors')
        .order_by(F('last_accessed_at').desc(nulls_last=True), '-created_at')
    )


def _assert_schedule_enrollable(schedule, *, enforce=True):
    """Validate that a cohort schedule accepts enrollments right now.

    Locks the schedule row (`select_for_update`) before counting seats so two
    concurrent first-time enrollees can't both pass the capacity check — same
    race fix as webinar registration. Returns the locked schedule row.

    `enforce=False` is the paid-finalize path: a validated payment must be
    honored even if the cohort's window closed, it auto-advanced to `ongoing`,
    or it filled up between checkout and payment completion (money already
    moved — mirror `register_for_webinar(via_payment=True)`). Over-capacity is
    logged as an overshoot rather than refused.
    """
    from courses.all_models.schedule_models import CourseSchedule

    schedule = CourseSchedule.objects.select_for_update().get(pk=schedule.pk)

    if not enforce:
        if schedule.max_seats is not None:
            taken = Enrollment.objects.filter(schedule=schedule, is_active=True).count()
            if taken >= schedule.max_seats:
                logger.warning(
                    'Paid cohort enrollment exceeds capacity: schedule=%s taken=%s cap=%s',
                    schedule.pk, taken, schedule.max_seats,
                )
        return schedule

    if schedule.status != CourseSchedule.Status.SCHEDULED:
        raise ValidationError('Enrollment for this cohort is not open.')

    now = timezone.now()
    if not (schedule.enrollment_opens_at <= now <= schedule.enrollment_closes_at):
        raise ValidationError('Enrollment for this cohort is not open.')

    if schedule.max_seats is not None:
        taken = Enrollment.objects.filter(schedule=schedule, is_active=True).count()
        if taken >= schedule.max_seats:
            raise ValidationError('This cohort is full.')

    return schedule


@transaction.atomic
def enroll_learner(
    user,
    course: NidusCourse,
    *,
    enrollment_type: str = Enrollment.EnrollmentType.FREE,
    allow_unpublished: bool = False,
    schedule=None,
    via_payment: bool = False,
) -> Enrollment:
    """Enroll a learner in a published course. Raises ValidationError on duplicate or unpublished.

    `enrollment_type` records how access was obtained (free/paid). `allow_unpublished`
    is reserved for the payment finalize path — a validated payment must be honored
    even if the course was unpublished mid-transaction. `schedule` (a CourseSchedule
    of this course) enrolls the learner into that cohort: the enrollment window and
    seat cap are enforced, and the created row carries `schedule` so the learner's
    access follows the cohort's release timeline. `schedule=None` is the self-paced
    path, byte-for-byte unchanged.

    `via_payment=True` (paid finalize only) relaxes the cohort gate the same way
    `allow_unpublished` relaxes the publish check: a validated payment is honored
    even if the cohort's enrollment window closed, it auto-advanced to `ongoing`,
    or it filled up between checkout and payment completion. Without this, a
    finalize that lands after the window closes (the common last-minute case,
    since `enrollment_closes_at <= start_date` and the beat task flips the
    status at `start_date`) would take the money and grant nothing.
    """
    if user.user_type != 'learner':
        raise ValidationError('Only learners can enroll in courses.')

    if not course.is_published and not allow_unpublished:
        raise ValidationError('Enrollment is only allowed for published courses.')

    if schedule is not None:
        schedule = _assert_schedule_enrollable(schedule, enforce=not via_payment)

    existing = (
        Enrollment.objects
        .select_for_update()
        .filter(user=user, course=course, schedule=schedule)
        .first()
    )
    now = timezone.now()
    if existing:
        if existing.is_active:
            raise ValidationError('You are already enrolled in this course.')
        existing.is_active = True
        existing.last_accessed_at = now
        update_fields = ['is_active', 'last_accessed_at', 'updated_at']
        # Upgrade only: a paid reactivation records the purchase; a free call
        # never downgrades an enrollment the learner already paid for.
        if (
            enrollment_type == Enrollment.EnrollmentType.PAID
            and existing.enrollment_type != Enrollment.EnrollmentType.PAID
        ):
            existing.enrollment_type = Enrollment.EnrollmentType.PAID
            update_fields.append('enrollment_type')
        existing.save(update_fields=update_fields)
        logger.info('Enrollment reactivated: user=%s course=%s', user.pk, course.pk)
        _dispatch_enrollment_notifications(user, course, existing)
        return existing

    try:
        enrollment = Enrollment.objects.create(
            user=user,
            course=course,
            schedule=schedule,
            enrollment_type=enrollment_type,
            is_active=True,
            last_accessed_at=now,
        )
    except IntegrityError as exc:
        raise ValidationError('You are already enrolled in this course.') from exc

    logger.info('Enrollment created: user=%s course=%s', user.pk, course.pk)
    _dispatch_enrollment_notifications(user, course, enrollment)
    return enrollment


@transaction.atomic
def unenroll_learner(user, course: NidusCourse) -> Enrollment:
    """Soft-deactivate a learner's enrollment; preserves progress. Raises ValidationError if not enrolled."""
    enrollment = Enrollment.objects.select_for_update().filter(
        user=user, course=course, is_active=True,
    ).first()

    if not enrollment:
        raise ValidationError('You are not enrolled in this course.')

    enrollment.is_active = False
    enrollment.save(update_fields=['is_active', 'updated_at'])

    logger.info('Enrollment deactivated: user=%s course=%s', user.pk, course.pk)
    return enrollment


def recalculate_progress(enrollment: Enrollment) -> Enrollment:
    """Recompute progress_percent from actual completion data. Issues certificate at 100%."""
    course = enrollment.course

    content_rows = list(
        SectionContent.objects
        .filter(section__course=course)
        .values_list('item_type', 'object_id')
    )
    total_items = len(content_rows)

    if total_items == 0:
        enrollment.progress_percent = 0
        enrollment.save(update_fields=['progress_percent', 'updated_at'])
        return enrollment

    lecture_ids = {
        object_id
        for item_type, object_id in content_rows
        if item_type == SectionContent.ItemType.LECTURE
    }
    if lecture_ids:
        completed_lecture_ids = set(
            WatchProgress.objects.filter(
                user=enrollment.user,
                lecture_id__in=lecture_ids,
                is_completed=True,
            ).values_list('lecture_id', flat=True)
        )
        completed_lectures = len(completed_lecture_ids)
    else:
        completed_lectures = 0

    quiz_ids = {
        object_id
        for item_type, object_id in content_rows
        if item_type == SectionContent.ItemType.QUIZ
    }
    if quiz_ids:
        completed_quiz_ids = set(
            QuizAttempt.objects.filter(
                user=enrollment.user,
                quiz_id__in=quiz_ids,
            ).values_list('quiz_id', flat=True)
        )
        completed_quizzes = len(completed_quiz_ids)
    else:
        completed_quizzes = 0

    assignment_ids = {
        object_id
        for item_type, object_id in content_rows
        if item_type == SectionContent.ItemType.ASSIGNMENT
    }
    if assignment_ids:
        completed_assignment_ids = set(
            AssignmentSubmission.objects.filter(
                user=enrollment.user,
                assignment_id__in=assignment_ids,
                status=AssignmentSubmission.Status.PASSED,
            ).values_list('assignment_id', flat=True)
        )
        completed_assignments = len(completed_assignment_ids)
    else:
        completed_assignments = 0

    coding_ids = {
        object_id
        for item_type, object_id in content_rows
        if item_type == SectionContent.ItemType.CODING
    }
    if coding_ids:
        completed_coding_ids = set(
            CodingSubmission.objects.filter(
                user=enrollment.user,
                exercise_id__in=coding_ids,
                status=CodingSubmission.Status.PASSED,
            ).values_list('exercise_id', flat=True)
        )
        completed_coding = len(completed_coding_ids)
    else:
        completed_coding = 0

    completed_items = (
        completed_lectures
        + completed_quizzes
        + completed_assignments
        + completed_coding
    )
    progress = min(int((completed_items / total_items) * 100), 100)

    update_fields = ['progress_percent', 'updated_at']
    enrollment.progress_percent = progress
    newly_completed = False
    if progress >= 100 and enrollment.completed_at is None:
        enrollment.completed_at = timezone.now()
        update_fields.append('completed_at')
        newly_completed = True
    elif progress < 100 and enrollment.completed_at is not None:
        enrollment.completed_at = None
        update_fields.append('completed_at')

    enrollment.save(update_fields=update_fields)

    if newly_completed:
        enrollment_pk = enrollment.pk
        transaction.on_commit(lambda: _issue_certificate_and_notify(enrollment_pk))

    return enrollment


def _issue_certificate_and_notify(enrollment_pk: int) -> None:
    """Issue certificate and fire course.completed notification. Called via on_commit only."""
    from courses.services.certificate_service import issue_certificate
    from notifications.models import NotificationEventType
    from notifications.services.dispatcher import dispatch

    try:
        enrollment = Enrollment.objects.select_related('user', 'course').get(pk=enrollment_pk)
        logger.debug(
            '_issue_certificate_and_notify: enrollment=%s user=%s course=%s '
            'progress=%s completed_at=%s is_active=%s',
            enrollment_pk,
            enrollment.user_id,
            enrollment.course_id,
            enrollment.progress_percent,
            enrollment.completed_at,
            enrollment.is_active,
        )
        certificate = issue_certificate(enrollment)
        dispatch(
            NotificationEventType.COURSE_COMPLETED,
            [enrollment.user],
            context={
                'course_title': enrollment.course.title,
                'course_slug': enrollment.course.slug,
                'enrollment_id': enrollment.pk,
                'certificate_uid': str(certificate.certificate_uid),
            },
        )
    except Exception:
        logger.exception('_issue_certificate_and_notify failed for enrollment=%s', enrollment_pk)


def _dispatch_enrollment_notifications(user, course, enrollment) -> None:
    """Fire ENROLLMENT_CREATED and LEARNER_ENROLLED notifications via on_commit."""
    from notifications.models import NotificationEventType
    from notifications.services.dispatcher import dispatch

    ctx_learner = {'course_title': course.title, 'course_slug': course.slug}
    transaction.on_commit(
        lambda: dispatch(NotificationEventType.ENROLLMENT_CREATED, [user], context=ctx_learner)
    )

    instructors = list(course.instructors.all())
    if instructors:
        ctx_instructor = {
            'course_title': course.title,
            'course_slug': course.slug,
            'learner_name': user.get_full_name() or user.email,
        }
        transaction.on_commit(
            lambda: dispatch(
                NotificationEventType.LEARNER_ENROLLED,
                instructors,
                context=ctx_instructor,
                skip_email=True,
            )
        )


def update_last_accessed(enrollment: Enrollment):
    """Touch last_accessed_at, skipping the write if within LAST_ACCESSED_DEBOUNCE."""
    now = timezone.now()
    if enrollment.last_accessed_at is not None and (
        now - enrollment.last_accessed_at < LAST_ACCESSED_DEBOUNCE
    ):
        return enrollment.last_accessed_at

    Enrollment.objects.filter(pk=enrollment.pk).update(
        last_accessed_at=now,
        updated_at=now,
    )
    enrollment.last_accessed_at = now
    return now

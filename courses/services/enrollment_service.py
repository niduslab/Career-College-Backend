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

# `update_last_accessed` is called on every learner consumption GET; debounce
# so we don't write a row on every page refresh. 5 minutes of staleness is
# acceptable for "last opened the course" — nobody needs second-level precision.
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


# Catalog sort keys exposed to the public API. Treat any other value as the
# default ("relevance" when ?search= is supplied, "newest" otherwise).
CATALOG_SORT_OPTIONS = frozenset({
    'relevance',
    'newest',
    'popularity',
    'price_asc',
    'price_desc',
    'rating',
})


def _csv_param(params, key):
    """Split a comma-separated query param into a deduped list of non-empty tokens.

    Order is preserved — ``dict.fromkeys`` keeps first-seen insertion order
    so the validator's error messages echo the user's input order back.
    """
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
    """Validate catalog filter/sort params; raise ValidationError if any bad.

    Collects every problem before raising so the frontend can fix all bad
    fields in one round-trip rather than one-at-a-time.
    """
    errors = {}

    # ── M4: ?sort= must be a known key (unknown values used to silently
    # fall back to newest, hiding both backend typos and frontend bugs).
    raw_sort = params.get('sort')
    if raw_sort and raw_sort not in CATALOG_SORT_OPTIONS:
        errors['sort'] = (
            f'Invalid sort "{raw_sort}". Must be one of: '
            f'{", ".join(sorted(CATALOG_SORT_OPTIONS))}.'
        )

    # ── M6: every ?level= token must be a real CourseLevel choice.
    valid_levels = set(NidusCourse.CourseLevel.values)
    bad_levels = [lvl for lvl in _csv_param(params, 'level') if lvl not in valid_levels]
    if bad_levels:
        errors['level'] = (
            f'Invalid level(s): {", ".join(bad_levels)}. '
            f'Must be one of: {", ".join(sorted(valid_levels))}.'
        )

    # ── M5: numeric range params must parse and be non-negative.
    # The model itself constrains price with MinValueValidator(0); the API
    # should mirror that, not silently accept impossible ranges.
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

    if errors:
        raise ValidationError(errors)


def filter_catalog_courses(queryset: QuerySet[NidusCourse], params) -> QuerySet[NidusCourse]:
    """
    Apply multi-criteria filtering and sorting to a catalog queryset.

    ``params`` is request.query_params (or any mapping with .get()).

    Supported filters (all optional, AND-combined):
        category=<slug>                top-level CourseCategory.slug
        subcategory=<slug>             child CourseCategory.slug (parent should be `category`)
        level=<csv>                    e.g. ?level=beginner,intermediate
        language=<csv>                 case-insensitive; e.g. ?language=english,bangla
        price_type=free|paid           shortcut for price=0 / price>0
        price_min=<decimal>            inclusive lower bound on price
        price_max=<decimal>            inclusive upper bound on price
        duration_min=<minutes>         inclusive lower bound on duration_minutes
        duration_max=<minutes>         inclusive upper bound on duration_minutes
        search=<text>                  matches title / description / instructor name
        rating_min=<1-5>               course rating — REQUIRES new model field
        min_reviews=<int>              minimum review count — REQUIRES Review model
        instructor_rating_min=<1-5>    — REQUIRES instructor rating field

    Supported sorts via ?sort=<key>:
        relevance      title match rank (default when ?search= present)
        newest         -published_at (default otherwise)
        popularity     by active enrollment count desc
        price_asc      price ascending
        price_desc     price descending
        rating         course rating desc — REQUIRES new model field

    Raises ``django.core.exceptions.ValidationError`` with a field-keyed
    error dict when any of the validated params is malformed. The catalog
    view turns that into a 400 response. Validated params:
        sort, level, price_min, price_max, duration_min, duration_max.
    """
    _validate_catalog_params(params)

    # ── Category / subcategory ───────────────────────────────────────────
    # Category is a single FK on NidusCourse; a course points at exactly one
    # CourseCategory row, which itself may be a child (subcategory) via the
    # self-FK `parent`. Three valid input shapes:
    #   - subcategory only      → exact match on the subcategory slug
    #   - category only         → match the category itself OR any of its children
    #   - category + subcategory → match the subcategory, but only if its parent
    #                              actually is the given category (rejects mismatched pairs)
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

    # ── Skill level (multi-select via CSV) ───────────────────────────────
    levels = _csv_param(params, 'level')
    if levels:
        queryset = queryset.filter(level__in=levels)

    # ── Language (multi-select, case-insensitive) ────────────────────────
    languages = _csv_param(params, 'language')
    if languages:
        lang_q = Q()
        for lang in languages:
            lang_q |= Q(language__iexact=lang)
        queryset = queryset.filter(lang_q)

    # ── Price (free / paid / range) ──────────────────────────────────────
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

    # ── Duration (in minutes — frontend converts hours/weeks/months) ─────
    duration_min = _positive_int_or_none(params.get('duration_min'))
    if duration_min is not None:
        queryset = queryset.filter(duration_minutes__gte=duration_min)

    duration_max = _positive_int_or_none(params.get('duration_max'))
    if duration_max is not None:
        queryset = queryset.filter(duration_minutes__lte=duration_max)

    # ── Search across title, description, instructor name ────────────────
    # The custom User model stores the human name in `full_name`; the
    # AbstractUser-inherited first_name / last_name columns exist but are
    # never populated in this project, so they must not be searched.
    search = params.get('search')
    if search:
        queryset = queryset.filter(
            Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(instructors__full_name__icontains=search)
        ).distinct()

    # ── TODO: filters that need new model fields ─────────────────────────
    # Course rating (avg of CourseReview.rating, or a denormalized column).
    #
    #   rating_min = _decimal_or_none(params.get('rating_min'))
    #   if rating_min is not None:
    #       queryset = queryset.filter(avg_rating__gte=rating_min)
    #
    # Review count (requires a CourseReview model with FK 'reviews').
    #
    #   min_reviews = _positive_int_or_none(params.get('min_reviews'))
    #   if min_reviews is not None:
    #       queryset = queryset.annotate(
    #           _review_count=Count('reviews')
    #       ).filter(_review_count__gte=min_reviews)
    #
    # Instructor rating (requires InstructorProfile.avg_rating or similar).
    #
    #   instructor_rating_min = _decimal_or_none(params.get('instructor_rating_min'))
    #   if instructor_rating_min is not None:
    #       queryset = queryset.filter(
    #           instructors__instructor_profile__avg_rating__gte=instructor_rating_min
    #       ).distinct()
    # ─────────────────────────────────────────────────────────────────────

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
        # TODO: enable once NidusCourse.avg_rating (or aggregate) exists.
        #   return queryset.order_by(F('avg_rating').desc(nulls_last=True), '-published_at', '-id')
        return queryset.order_by(F('published_at').desc(nulls_last=True), '-id')

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


@transaction.atomic
def enroll_learner(user, course: NidusCourse) -> Enrollment:
    """
    Enroll a learner in a published course.

    Raises ``ValidationError`` if the learner is already enrolled or if
    the course is not published.
    """
    if user.user_type != 'learner':
        raise ValidationError('Only learners can enroll in courses.')

    if not course.is_published:
        raise ValidationError('Enrollment is only allowed for published courses.')

    existing = (
        Enrollment.objects
        .select_for_update()
        .filter(user=user, course=course)
        .first()
    )
    now = timezone.now()
    if existing:
        if existing.is_active:
            raise ValidationError('You are already enrolled in this course.')
        existing.is_active = True
        existing.last_accessed_at = now
        existing.save(update_fields=['is_active', 'last_accessed_at', 'updated_at'])
        logger.info('Enrollment reactivated: user=%s course=%s', user.pk, course.pk)
        return existing

    try:
        enrollment = Enrollment.objects.create(
            user=user,
            course=course,
            # Payment is not integrated yet; all published courses enroll as free for now.
            enrollment_type=Enrollment.EnrollmentType.FREE,
            is_active=True,
            last_accessed_at=now,
        )
    except IntegrityError as exc:
        raise ValidationError('You are already enrolled in this course.') from exc

    logger.info('Enrollment created: user=%s course=%s', user.pk, course.pk)
    return enrollment


@transaction.atomic
def unenroll_learner(user, course: NidusCourse) -> Enrollment:
    """
    Soft-deactivate a learner's enrollment. Progress is preserved.

    Raises ``ValidationError`` if no active enrollment exists.
    """
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
    """
    Recompute ``progress_percent`` from the actual content completion data.

    Formula: (completed content items / total content items) * 100
    Completion rules:
    - lecture: WatchProgress.is_completed=True
    - quiz: at least one QuizAttempt exists for the learner
    - assignment: AssignmentSubmission(status='passed')
    - coding: CodingSubmission(status='passed') — distinct per exercise so
      multiple PASSED attempts on the same exercise count once.

    Uses grouped queries + set intersections to avoid N+1 behavior.
    """
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
    """Issue a certificate and queue the congratulations email on first completion.

    Called via transaction.on_commit so it never fires for rolled-back
    transactions. Local imports break the circular dependency between
    enrollment_service ↔ certificate_service ↔ tasks.
    """
    from courses.services.certificate_service import issue_certificate
    from courses.tasks import send_certificate_email_task

    try:
        enrollment = Enrollment.objects.select_related('user', 'course').get(pk=enrollment_pk)
        certificate = issue_certificate(enrollment)
        send_certificate_email_task.delay(certificate.pk)
    except Exception:
        logger.exception('_issue_certificate_and_notify failed for enrollment=%s', enrollment_pk)


def update_last_accessed(enrollment: Enrollment):
    """Touch the last_accessed_at timestamp, debounced to LAST_ACCESSED_DEBOUNCE.

    Skips the write when the previous touch is younger than the debounce
    window. Avoids a row-level UPDATE on every page refresh / progress ping.
    """
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

from django.core.exceptions import ValidationError
from django.db.models import Q, QuerySet

from courses.all_models.content_models import Lecture
from courses.all_models.course_models import NidusCourse
from courses.all_models.note_models import LearnerNote

MAX_NOTE_TAGS = 10

NOTE_ORDERING_OPTIONS = frozenset({
    '-updated_at',
    'updated_at',
    '-created_at',
    'created_at',
    'title',
    '-title',
})


class NoteError(Exception):
    """Domain failure carrying the HTTP status the view should return.

    Mirrors ReviewError / AssignmentSubmissionError.
    """

    def __init__(self, message: str, http_status: int = 422):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def _csv_param(params, key) -> list[str]:
    """Collect a repeatable or comma-separated query param into a flat list."""
    values = []
    raw_list = params.getlist(key) if hasattr(params, 'getlist') else [params.get(key)]
    for raw in raw_list:
        if not raw:
            continue
        values.extend(part.strip() for part in raw.split(',') if part.strip())
    return values


def _bool_param(value):
    if value in (None, ''):
        return None
    lowered = str(value).strip().lower()
    if lowered in ('true', '1'):
        return True
    if lowered in ('false', '0'):
        return False
    return NotImplemented


def _validate_note_params(params) -> None:
    """Validate list filter params. Collects every field error before raising."""
    errors = {}

    raw_ordering = params.get('ordering')
    if raw_ordering and raw_ordering not in NOTE_ORDERING_OPTIONS:
        errors['ordering'] = (
            f'Invalid ordering "{raw_ordering}". Must be one of: '
            f'{", ".join(sorted(NOTE_ORDERING_OPTIONS))}.'
        )

    raw_lecture_id = params.get('lecture_id')
    if raw_lecture_id not in (None, ''):
        try:
            int(raw_lecture_id)
        except (TypeError, ValueError):
            errors['lecture_id'] = f'"{raw_lecture_id}" is not a valid integer.'

    if _bool_param(params.get('is_pinned')) is NotImplemented:
        errors['is_pinned'] = 'Must be "true" or "false".'

    if errors:
        raise ValidationError(errors)


def get_learner_notes(user, params) -> QuerySet[LearnerNote]:
    """Filtered, ordered note list for the caller.

    Params: `course` (slug), `lecture_id` (int), `tag` (repeatable or CSV),
    `is_pinned` (true/false), `search` (icontains over title and body),
    `ordering` (NOTE_ORDERING_OPTIONS). Multiple tags AND together — a note
    must carry all of them. Pinned notes always sort first, whatever
    `ordering` says. Raises ValidationError with a per-field dict on bad input.
    """
    _validate_note_params(params)

    queryset = (
        LearnerNote.objects
        .filter(user=user)
        .select_related('course', 'lecture__section__course')
    )

    course_slug = params.get('course')
    if course_slug:
        queryset = queryset.filter(course__slug=course_slug)

    lecture_id = params.get('lecture_id')
    if lecture_id not in (None, ''):
        queryset = queryset.filter(lecture_id=int(lecture_id))

    for tag in _csv_param(params, 'tag'):
        queryset = queryset.filter(tags__contains=[tag.lower()])

    is_pinned = _bool_param(params.get('is_pinned'))
    if is_pinned is not None:
        queryset = queryset.filter(is_pinned=is_pinned)

    search = (params.get('search') or '').strip()
    if search:
        queryset = queryset.filter(Q(title__icontains=search) | Q(body__icontains=search))

    ordering = params.get('ordering') or '-updated_at'
    return queryset.order_by('-is_pinned', ordering, '-id')


def _resolve_targets(course_slug, lecture_id):
    """Resolve the (course, lecture) anchor pair from the write payload.

    A lecture alone is enough — the course is derived from it. Raises NoteError
    when either target is missing or the pair is inconsistent.
    """
    lecture = None
    course = None

    if lecture_id is not None:
        lecture = (
            Lecture.objects
            .select_related('section__course')
            .filter(pk=lecture_id)
            .first()
        )
        if lecture is None:
            raise NoteError('Lecture not found.', 404)
        course = lecture.section.course

    if course_slug:
        if lecture is not None:
            if course.slug != course_slug:
                raise NoteError('Lecture does not belong to that course.', 400)
        else:
            course = NidusCourse.objects.filter(slug=course_slug, is_published=True).first()
            if course is None:
                raise NoteError('Course not found.', 404)

    return course, lecture


def create_note(user, data: dict) -> LearnerNote:
    """Create a note for the caller.

    Enrollment is deliberately not required: a note stores only the learner's
    own text, so gating on enrollment would protect nothing while breaking
    note-taking before enrolling or after unenrolling. The course must be
    published for the anchor to be meaningful.
    """
    course, lecture = _resolve_targets(data.get('course_slug'), data.get('lecture_id'))

    return LearnerNote.objects.create(
        user=user,
        course=course,
        lecture=lecture,
        timestamp_seconds=data.get('timestamp_seconds'),
        title=data.get('title', ''),
        body=data['body'],
        tags=data.get('tags', []),
        color=data.get('color', LearnerNote.Color.DEFAULT),
        is_pinned=data.get('is_pinned', False),
    )


def get_note(user, pk: int) -> LearnerNote:
    """Fetch one of the caller's notes.

    Raises LearnerNote.DoesNotExist when missing OR owned by someone else —
    numeric IDs are not enumerable, so the view returns 404, never 403.
    """
    note = (
        LearnerNote.objects
        .select_related('course', 'lecture__section__course')
        .filter(pk=pk, user=user)
        .first()
    )
    if note is None:
        raise LearnerNote.DoesNotExist('Note not found.')
    return note


def update_note(user, pk: int, data: dict) -> LearnerNote:
    """Partial update. Only keys present in `data` are applied."""
    note = get_note(user, pk)

    anchor_touched = 'course_slug' in data or 'lecture_id' in data
    update_fields = []

    if anchor_touched:
        course_slug = data.get('course_slug', note.course.slug if note.course else None)
        lecture_id = data.get('lecture_id', note.lecture_id)
        course, lecture = _resolve_targets(course_slug, lecture_id)
        note.course = course
        note.lecture = lecture
        update_fields += ['course', 'lecture']

    for field in ('timestamp_seconds', 'title', 'body', 'tags', 'color', 'is_pinned'):
        if field in data:
            setattr(note, field, data[field])
            update_fields.append(field)

    if note.timestamp_seconds is not None and note.lecture_id is None:
        raise NoteError('A timestamp requires a lecture.', 400)

    if update_fields:
        note.save(update_fields=update_fields + ['updated_at'])
    return note


def delete_note(user, pk: int) -> None:
    """Hard-delete one of the caller's notes."""
    note = get_note(user, pk)
    note.delete()

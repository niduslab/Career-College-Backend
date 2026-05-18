"""
Learner-facing consumption services.

These power the `/api/v1/courses/learn/...` endpoints and are deliberately
separate from instructor-facing data loaders so that sensitive fields
(model_answer, solution_code, hidden test cases, quiz correctness) can
never accidentally leak into a learner response.

This module covers Phase 1 of the learner consumption surface:
    1. Curriculum outline    -> `load_learner_curriculum`
    2. Lecture detail        -> `get_consumption_lecture`
    3. Watch-progress upsert -> `upsert_watch_progress`

Quiz / assignment / coding consumption services land in later phases.
"""

from collections import defaultdict
from typing import Optional, Tuple

from courses.models import (
    Assignment,
    CodingExercise,
    CourseSection,
    Enrollment,
    Lecture,
    NidusCourse,
    Quiz,
    SectionContent,
    VideoAsset,
    WatchProgress,
)


def resolve_course_access(user, course: NidusCourse) -> Tuple[bool, Optional[Enrollment]]:
    """
    Resolve a user's consumption-side access to a course.

    Returns a (is_instructor, enrollment_or_none) tuple.
    The caller is expected to reject the request when both are falsy.

    The instructor check uses `course.instructors.all()` so callers that
    `prefetch_related('instructors')` (or `'section__course__instructors'`)
    pay zero extra queries here. Without the prefetch this still works,
    but at the cost of loading all instructor rows instead of an EXISTS.
    """
    is_instructor = any(u.pk == user.pk for u in course.instructors.all())
    if is_instructor:
        return True, None

    enrollment = Enrollment.objects.filter(
        user=user, course=course, is_active=True,
    ).first()
    return False, enrollment


def _load_sections_and_contents(course) -> Tuple[list, dict]:
    # `.only(...)` keeps the curriculum outline lightweight: `description`
    # (TextField, often paragraphs) and `created_at`/`updated_at` are not
    # read by the serializer, so we don't pay to fetch them.
    sections = list(
        CourseSection.objects
        .filter(course=course)
        .only('id', 'title', 'position')
        .order_by('position', 'id')
    )
    section_ids = [s.id for s in sections]
    if not section_ids:
        return sections, {}

    contents = list(
        SectionContent.objects
        .filter(section_id__in=section_ids)
        .only('id', 'section_id', 'item_type', 'object_id', 'position')
        .order_by('section_id', 'position', 'id')
    )
    contents_by_section: dict[int, list] = defaultdict(list)
    for row in contents:
        contents_by_section[row.section_id].append(row)
    return sections, dict(contents_by_section)


def _split_object_ids(contents_by_section: dict) -> dict[str, list[int]]:
    by_type: dict[str, list[int]] = {
        SectionContent.ItemType.LECTURE: [],
        SectionContent.ItemType.QUIZ: [],
        SectionContent.ItemType.CODING: [],
        SectionContent.ItemType.ASSIGNMENT: [],
    }
    for rows in contents_by_section.values():
        for row in rows:
            bucket = by_type.get(row.item_type)
            if bucket is not None:
                bucket.append(row.object_id)
    return by_type


def _lecture_durations(lecture_ids: list[int]) -> dict[int, Optional[int]]:
    if not lecture_ids:
        return {}
    rows = (
        VideoAsset.objects
        .filter(lecture_id__in=lecture_ids, is_active=True)
        .values('lecture_id', 'duration_seconds')
    )
    return {row['lecture_id']: row['duration_seconds'] for row in rows}


def load_learner_curriculum(course: NidusCourse, user, is_instructor: bool) -> dict:
    """
    Light-weight curriculum outline for the learner sidebar.

    Loads only the minimum fields needed to render the curriculum tree:
    lecture titles + type + duration + completion marker, quiz/assignment
    titles, coding exercise title + difficulty. Heavy payloads (HLS URLs,
    quiz questions, article text) come from the per-item detail endpoints.

    For non-instructor callers, the learner's `WatchProgress.is_completed`
    flags are loaded so each lecture row can show a checked/unchecked marker.
    """
    sections, contents_by_section = _load_sections_and_contents(course)
    ids_by_type = _split_object_ids(contents_by_section)

    lectures: dict[int, Lecture] = {
        lec.id: lec
        for lec in Lecture.objects.filter(
            id__in=ids_by_type[SectionContent.ItemType.LECTURE]
        ).only('id', 'title', 'lecture_type')
    } if ids_by_type[SectionContent.ItemType.LECTURE] else {}

    quizzes: dict[int, Quiz] = {
        q.id: q for q in Quiz.objects.filter(
            id__in=ids_by_type[SectionContent.ItemType.QUIZ]
        ).only('id', 'title')
    } if ids_by_type[SectionContent.ItemType.QUIZ] else {}

    coding_exercises: dict[int, CodingExercise] = {
        ex.id: ex for ex in CodingExercise.objects.filter(
            id__in=ids_by_type[SectionContent.ItemType.CODING]
        ).only('id', 'title', 'difficulty')
    } if ids_by_type[SectionContent.ItemType.CODING] else {}

    assignments: dict[int, Assignment] = {
        a.id: a for a in Assignment.objects.filter(
            id__in=ids_by_type[SectionContent.ItemType.ASSIGNMENT]
        ).only('id', 'title')
    } if ids_by_type[SectionContent.ItemType.ASSIGNMENT] else {}

    completed_lecture_ids: set[int] = set()
    if not is_instructor and lectures:
        completed_lecture_ids = set(
            WatchProgress.objects
            .filter(user=user, lecture_id__in=lectures.keys(), is_completed=True)
            .values_list('lecture_id', flat=True)
        )

    durations = _lecture_durations(list(lectures.keys()))

    sections_payload = []
    for section in sections:
        items_payload = []
        for row in contents_by_section.get(section.id, []):
            item = {
                'content_id': row.id,
                'object_id': row.object_id,
                'item_type': row.item_type,
                'position': row.position,
            }
            if row.item_type == SectionContent.ItemType.LECTURE:
                lec = lectures.get(row.object_id)
                if lec is None:
                    continue
                item['title'] = lec.title
                item['lecture_type'] = lec.lecture_type
                item['duration_seconds'] = durations.get(lec.id)
                if not is_instructor:
                    item['is_completed'] = lec.id in completed_lecture_ids
            elif row.item_type == SectionContent.ItemType.QUIZ:
                quiz = quizzes.get(row.object_id)
                if quiz is None:
                    continue
                item['title'] = quiz.title
            elif row.item_type == SectionContent.ItemType.CODING:
                ex = coding_exercises.get(row.object_id)
                if ex is None:
                    continue
                item['title'] = ex.title
                item['difficulty'] = ex.difficulty
            elif row.item_type == SectionContent.ItemType.ASSIGNMENT:
                a = assignments.get(row.object_id)
                if a is None:
                    continue
                item['title'] = a.title
            else:
                continue
            items_payload.append(item)

        sections_payload.append({
            'id': section.id,
            'title': section.title,
            'position': section.position,
            'items': items_payload,
        })

    return {
        'course': {
            'id': course.id,
            'slug': course.slug,
            'title': course.title,
        },
        'sections': sections_payload,
    }


def get_consumption_lecture(user, lecture_id: int):
    """
    Fetch a lecture and verify the user can consume it.

    Returns (lecture, course, is_instructor, watch_progress_or_none).
    Raises Lecture.DoesNotExist if the lecture is missing OR the user has
    no consumption-side access to its course. (404, not 403, so that
    lecture existence isn't leaked to non-members.)
    """
    lecture = (
        Lecture.objects
        .select_related('section__course')
        .prefetch_related('section__course__instructors')
        .filter(pk=lecture_id)
        .first()
    )
    if lecture is None:
        raise Lecture.DoesNotExist
    course = lecture.section.course

    is_instructor, enrollment = resolve_course_access(user, course)
    if not is_instructor and enrollment is None:
        raise Lecture.DoesNotExist

    watch_progress = None
    if not is_instructor:
        watch_progress = WatchProgress.objects.filter(user=user, lecture=lecture).first()

    return lecture, course, is_instructor, watch_progress


def upsert_watch_progress(
    user,
    lecture: Lecture,
    watched_seconds: int,
    is_completed: bool,
) -> WatchProgress:
    """
    Idempotent upsert of a learner's watch progress for a lecture.

    The post_save signal on WatchProgress recalculates the enrollment's
    progress_percent when `is_completed` transitions, so this function
    intentionally does not touch the enrollment row itself.
    """
    wp, _ = WatchProgress.objects.update_or_create(
        user=user,
        lecture=lecture,
        defaults={
            'watched_seconds': watched_seconds,
            'is_completed': is_completed,
        },
    )
    return wp

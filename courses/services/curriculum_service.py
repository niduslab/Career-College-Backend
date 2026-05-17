"""
Bulk-loader for the public catalog curriculum tree.

`load_catalog_curriculum` returns just the fields the catalog detail page
needs — section/item titles, lecture durations, and the master playlist
URL for `is_preview=True` lectures. Curriculum loading for enrolled
learners lives in `courses/services/learner_service.py`
(`load_learner_curriculum`).
"""

from collections import defaultdict
from typing import Optional, Tuple

from courses.models import (
    Assignment,
    CodingExercise,
    CourseSection,
    Lecture,
    Quiz,
    SectionContent,
    VideoAsset,
)


def _load_sections_and_contents(course) -> Tuple[list, dict]:
    """Return ordered sections + a {section_id -> [SectionContent, ...]} map."""
    sections = list(
        CourseSection.objects.filter(course=course).order_by('position', 'id')
    )
    section_ids = [s.id for s in sections]
    if not section_ids:
        return sections, {}

    contents = list(
        SectionContent.objects
        .filter(section_id__in=section_ids)
        .order_by('section_id', 'position', 'id')
    )
    contents_by_section: dict[int, list] = defaultdict(list)
    for row in contents:
        contents_by_section[row.section_id].append(row)
    return sections, dict(contents_by_section)


def _split_object_ids(contents_by_section: dict) -> dict[str, list[int]]:
    """Bucket SectionContent object_ids by item_type for bulk loading."""
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
    """Return {lecture_id -> duration_seconds} from the active video asset."""
    if not lecture_ids:
        return {}
    rows = (
        VideoAsset.objects
        .filter(lecture_id__in=lecture_ids, is_active=True)
        .values('lecture_id', 'duration_seconds')
    )
    return {row['lecture_id']: row['duration_seconds'] for row in rows}


def load_catalog_curriculum(course) -> dict:
    """
    Build the catalog-safe context dict consumed by CatalogCourseDetailSerializer.

    Returns a dict with: sections, contents_by_section, lectures, quizzes,
    coding_exercises, assignments, lecture_durations.
    Only fields needed for the catalog outline + preview lectures are loaded.
    """
    sections, contents_by_section = _load_sections_and_contents(course)
    ids_by_type = _split_object_ids(contents_by_section)

    lectures: dict[int, Lecture] = {}
    if ids_by_type[SectionContent.ItemType.LECTURE]:
        # Only the fields the catalog serializer reads.
        for lec in Lecture.objects.filter(id__in=ids_by_type[SectionContent.ItemType.LECTURE]).only(
            'id', 'title', 'lecture_type', 'is_preview',
            'stream_master_playlist', 'stream_renditions',
        ):
            lectures[lec.id] = lec

    quizzes = {
        q.id: q for q in Quiz.objects.filter(
            id__in=ids_by_type[SectionContent.ItemType.QUIZ]
        ).only('id', 'title')
    } if ids_by_type[SectionContent.ItemType.QUIZ] else {}

    coding_exercises = {
        ex.id: ex for ex in CodingExercise.objects.filter(
            id__in=ids_by_type[SectionContent.ItemType.CODING]
        ).only('id', 'title', 'difficulty')
    } if ids_by_type[SectionContent.ItemType.CODING] else {}

    assignments = {
        a.id: a for a in Assignment.objects.filter(
            id__in=ids_by_type[SectionContent.ItemType.ASSIGNMENT]
        ).only('id', 'title')
    } if ids_by_type[SectionContent.ItemType.ASSIGNMENT] else {}

    return {
        'sections': sections,
        'contents_by_section': contents_by_section,
        'lectures': lectures,
        'quizzes': quizzes,
        'coding_exercises': coding_exercises,
        'assignments': assignments,
        'lecture_durations': _lecture_durations(list(lectures.keys())),
    }

"""
Flatten a section's lectures into the prompt text an AI generator is grounded in.

Shared by every AI feature that writes content *about* a module — quiz questions
and coding exercises today. Django owns this rather than the browser: the builder
holds one section's content list and fetches article bodies lazily, so letting the
client post them would let it choose what the model sees.
"""

import html
import re

from django.utils.html import strip_tags

from courses.models import Lecture, SectionContent

# Mirrors MAX_SOURCE_CHARS in the AI services' schemas; move them together.
MAX_SOURCE_CHARS = 8000

_WHITESPACE = re.compile(r'[ \t]+')
_BLANK_LINES = re.compile(r'\n{3,}')
_TRUNCATION_MARKER = '\n\n[Material truncated.]'


def html_to_text(markup: str) -> str:
    """Flatten editor HTML to plain text. Prompt input only — not a sanitizer.

    Block tags become newlines first so paragraphs stay separated; entities are
    decoded afterwards, since `strip_tags` leaves them encoded.
    """
    if not markup:
        return ''
    text = re.sub(r'(?i)</(p|div|h[1-6]|li|tr|blockquote|pre)>', '\n\n', markup)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = html.unescape(strip_tags(text))
    text = _WHITESPACE.sub(' ', text)
    return _BLANK_LINES.sub('\n\n', text).strip()


def _ordered_section_lectures(section) -> list:
    """The section's lectures in the order a learner meets them.

    `Lecture` has no position of its own — curriculum order lives in
    `SectionContent`. One with no such row is appended rather than dropped.
    """
    lectures = {lecture.id: lecture for lecture in Lecture.objects.filter(section=section)}
    ordered, seen = [], set()
    positions = (
        SectionContent.objects
        .filter(section=section, item_type=SectionContent.ItemType.LECTURE)
        .order_by('position', 'id')
        .values_list('object_id', flat=True)
    )
    for object_id in positions:
        lecture = lectures.get(object_id)
        if lecture is not None and lecture.id not in seen:
            ordered.append(lecture)
            seen.add(lecture.id)
    ordered.extend(
        lecture for lecture_id, lecture in sorted(lectures.items())
        if lecture_id not in seen
    )
    return ordered


def _truncate(text: str, limit: int = MAX_SOURCE_CHARS) -> str:
    """Cut at a paragraph boundary and say so.

    Stopping mid-sentence invites a question about a fact the material no longer
    finishes stating.
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    boundary = head.rfind('\n\n')
    if boundary > limit // 2:
        head = head[:boundary]
    return head.rstrip() + _TRUNCATION_MARKER


def build_section_source_material(section) -> tuple[str, bool]:
    """Assemble the text generated content must be answerable from.

    Returns `(source_material, grounded)`. `grounded` is False when the section
    has no written lecture content: the material is then titles only, and the UI
    warns rather than passing it off as drawn from the lectures.
    """
    parts = []
    if section.description.strip():
        parts.append(f'Module: {section.title}\n{section.description.strip()}')
    else:
        parts.append(f'Module: {section.title}')

    grounded = False
    for lecture in _ordered_section_lectures(section):
        body = ''
        if lecture.lecture_type == Lecture.LectureType.ARTICLE:
            body = html_to_text(lecture.article_content)
        if body:
            grounded = True
            parts.append(f'Lesson: {lecture.title}\n{body}')
        else:
            # Weak grounding, but honest — a video lecture has no text to offer.
            parts.append(f'Lesson: {lecture.title}')

    return _truncate('\n\n'.join(parts)), grounded

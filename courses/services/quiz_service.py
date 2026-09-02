"""
Quiz-domain operations that are not HTTP.

`bulk_create_quiz_questions` is the transactional write behind
`POST quizzes/<id>/questions/bulk/`; nothing about it is AI-specific.
`build_quiz_source_material` and `collect_avoid_questions` are the read side used
by the AI preview endpoint — they live here because Django owns this data, not
the browser.

See docs/architecture/35-ai-quiz-question-generator.md.
"""

import html
import re

from django.db import transaction
from django.db.models import Max
from django.utils.html import strip_tags

from courses.models import Lecture, QuizAnswer, QuizQuestion, SectionContent

# Both mirror caps in the AI service's schema; move them together.
MAX_SOURCE_CHARS = 8000
MAX_AVOID_QUESTIONS = 30

_WHITESPACE = re.compile(r'[ \t]+')
_BLANK_LINES = re.compile(r'\n{3,}')
_TRUNCATION_MARKER = '\n\n[Material truncated.]'


# ---------------------------------------------------------------------------
# Source material for AI generation
# ---------------------------------------------------------------------------

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


def build_quiz_source_material(quiz) -> tuple[str, bool]:
    """Assemble the text a generated question must be answerable from.

    Returns `(source_material, grounded)`. `grounded` is False when the section
    has no written lecture content: the material is then titles only, and the UI
    warns rather than passing it off as drawn from the lectures.
    """
    section = quiz.section
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


def collect_avoid_questions(quiz, extra=None) -> list[str]:
    """Question texts a generation must not produce again.

    The quiz's own questions, plus anything the caller passes — on a regenerate
    that is the unsaved draft on screen, invisible to this query.
    """
    existing = list(
        quiz.questions.order_by('position', 'id').values_list('question_text', flat=True)
    )
    seen, avoid = set(), []
    for text in existing + list(extra or []):
        key = ' '.join(text.split()).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        avoid.append(text.strip())
    return avoid[:MAX_AVOID_QUESTIONS]


# ---------------------------------------------------------------------------
# Bulk write
# ---------------------------------------------------------------------------

def bulk_create_quiz_questions(quiz, questions, user) -> list[QuizQuestion]:
    """Append a batch of questions, with their options, in one transaction.

    `questions` arrives validated by the serializer; `bulk_create` skips
    `Model.clean()`, so `uniq_correct_answer_per_question` is what backs the
    single-correct rule here.

    Positions are read inside the transaction. Two applies racing for the same
    slot hit `uniq_quizquestion_quiz_position`, and the whole batch rolls back.

    Only `Quiz` carries authorship — the sub-rows have no author fields.
    """
    with transaction.atomic():
        start = (
            QuizQuestion.objects.filter(quiz=quiz).aggregate(Max('position'))['position__max']
            or 0
        )

        created = []
        options = []
        for offset, item in enumerate(questions, start=1):
            question = QuizQuestion.objects.create(
                quiz=quiz,
                question_text=item['question_text'],
                position=start + offset,
            )
            created.append(question)
            options.extend(
                QuizAnswer(
                    question=question,
                    answer_text=option['answer_text'],
                    is_correct=option['is_correct'],
                )
                for option in item['options']
            )

        QuizAnswer.objects.bulk_create(options)

        quiz.last_edited_by = user
        quiz.save(update_fields=['last_edited_by', 'updated_at'])

    # Re-read with options attached; `created` alone would N+1 on serialization.
    return list(
        QuizQuestion.objects
        .filter(id__in=[question.id for question in created])
        .prefetch_related('answers')
        .order_by('position', 'id')
    )

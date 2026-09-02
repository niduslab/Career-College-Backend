"""
Quiz-domain operations that are not HTTP.

`bulk_create_quiz_questions` is the transactional write behind
`POST quizzes/<id>/questions/bulk/`; nothing about it is AI-specific.
`collect_avoid_questions` is the read side used by the AI preview endpoint;
source material comes from `section_context_service`, shared with the coding
exercise generator.

See docs/architecture/35-ai-quiz-question-generator.md.
"""

from django.db import transaction
from django.db.models import Max

from courses.models import QuizAnswer, QuizQuestion
from courses.services.section_context_service import build_section_source_material

# Mirrors MAX_AVOID in the AI service's schema; move them together.
MAX_AVOID_QUESTIONS = 30


def build_quiz_source_material(quiz) -> tuple[str, bool]:
    """The quiz's own section, flattened for the prompt."""
    return build_section_source_material(quiz.section)


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

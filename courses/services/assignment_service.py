from django.db import transaction
from django.db.models import Case, F, IntegerField, Max, When
from django.shortcuts import get_object_or_404

from courses.models import (
    Assignment,
    AssignmentQuestion,
)
from courses.services.section_service import reorder_section_content


# ---------------------------------------------------------------------------
# Ownership helpers
# ---------------------------------------------------------------------------

def _get_owned_assignment(assignment_id, user) -> Assignment:
    return get_object_or_404(
        Assignment.objects.select_related('section__course'),
        pk=assignment_id,
        section__course__instructors=user,
    )


def _get_owned_question(question_id, user) -> AssignmentQuestion:
    return get_object_or_404(
        AssignmentQuestion.objects.select_related('assignment__section__course'),
        pk=question_id,
        assignment__section__course__instructors=user,
    )


# ---------------------------------------------------------------------------
# Assignment CRUD
# Creation is handled by SectionContentListCreateAPIView (item_type=assignment),
# which inlines Assignment.objects.create() + SectionContent slot creation.
# ---------------------------------------------------------------------------

@transaction.atomic
def update_assignment(assignment_id, user, validated_data) -> Assignment:
    assignment = _get_owned_assignment(assignment_id, user)

    # Only allow these four fields through partial update.
    allowed_fields = {'title', 'description', 'instructions', 'passing_score'}
    for field in allowed_fields:
        if field in validated_data:
            setattr(assignment, field, validated_data[field])
    assignment.save()
    return assignment


@transaction.atomic
def delete_assignment(assignment_id, user) -> None:
    assignment = _get_owned_assignment(assignment_id, user)
    # Assignment has GenericRelation to SectionContent → cascade removes the slot.
    # Question rows cascade-delete via the Assignment FK.
    assignment.delete()


# ---------------------------------------------------------------------------
# AssignmentQuestion CRUD
# ---------------------------------------------------------------------------

@transaction.atomic
def add_question(assignment_id, user, validated_data) -> AssignmentQuestion:
    # Lock the assignment row so concurrent add_question calls cannot collide
    # on the (assignment, position) unique constraint.
    locked_assignment = (
        Assignment.objects
        .select_for_update()
        .select_related('section__course')
        .filter(
            pk=assignment_id,
            section__course__instructors=user,
        )
        .first()
    )
    if locked_assignment is None:
        # Fall back to standard 404 semantics for unauthorized / missing.
        _get_owned_assignment(assignment_id, user)

    next_position = (
        AssignmentQuestion.objects
        .filter(assignment_id=assignment_id)
        .aggregate(Max('position'))['position__max']
        or 0
    ) + 1

    return AssignmentQuestion.objects.create(
        assignment_id=assignment_id,
        position=next_position,
        **validated_data,
    )


@transaction.atomic
def update_question(question_id, user, validated_data) -> AssignmentQuestion:
    question = _get_owned_question(question_id, user)

    allowed_fields = {'question_text', 'model_answer', 'points', 'hint'}
    for field in allowed_fields:
        if field in validated_data:
            setattr(question, field, validated_data[field])
    question.save()
    return question


@transaction.atomic
def delete_question(question_id, user) -> None:
    question = _get_owned_question(question_id, user)

    deleted_position = question.position
    assignment_id = question.assignment_id
    question.delete()

    # Compact: shift every later question up by one in a single bulk UPDATE.
    AssignmentQuestion.objects.filter(
        assignment_id=assignment_id,
        position__gt=deleted_position,
    ).update(position=F('position') - 1)


@transaction.atomic
def reorder_questions(assignment_id, user, ordered_ids) -> list[AssignmentQuestion]:
    assignment = _get_owned_assignment(assignment_id, user)

    # Lock all questions for this assignment so reorder is consistent.
    existing_qs = (
        AssignmentQuestion.objects
        .select_for_update()
        .filter(assignment=assignment)
    )
    existing_ids = list(existing_qs.values_list('id', flat=True))

    if len(ordered_ids) != len(existing_ids):
        raise ValueError('ordered_ids must contain every question for this assignment exactly once.')
    if len(set(ordered_ids)) != len(ordered_ids):
        raise ValueError('ordered_ids contains duplicates.')
    if set(ordered_ids) != set(existing_ids):
        raise ValueError('ordered_ids must match the questions belonging to this assignment.')

    total = len(ordered_ids)

    # Two-phase shift mirrors reorder_section_content() to avoid transient
    # unique-constraint collisions on backends like SQLite.
    offset = total + 1
    AssignmentQuestion.objects.filter(assignment=assignment).update(
        position=F('position') + offset
    )
    when_clauses = [
        When(pk=question_id, then=new_position)
        for new_position, question_id in enumerate(ordered_ids, start=1)
    ]
    AssignmentQuestion.objects.filter(
        assignment=assignment, pk__in=ordered_ids
    ).update(
        position=Case(*when_clauses, output_field=IntegerField())
    )

    return list(
        AssignmentQuestion.objects
        .filter(assignment=assignment)
        .order_by('position', 'id')
    )


# Re-export reorder_section_content so callers can keep all assignment-related
# imports in one place if needed.
__all__ = [
    'update_assignment',
    'delete_assignment',
    'add_question',
    'update_question',
    'delete_question',
    'reorder_questions',
    'reorder_section_content',
]

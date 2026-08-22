"""
Learning path service — curated multi-course roadmaps.

Progress is always derived from the learner's real course Enrollment rows,
never stored on a separate ledger — see docs/architecture/28-learning-paths.md
§4. This keeps a path's progress permanently consistent with My Courses and
the dashboard summary; there is nothing here that can drift out of sync.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import QuerySet

from courses.all_models.course_models import NidusCourse
from courses.all_models.enrollment_models import Enrollment
from courses.all_models.learning_path_models import (
    LearningPath,
    LearningPathEnrollment,
    LearningPathMilestone,
)


class LearningPathError(Exception):
    """Raised by learning-path service functions for domain-rule violations."""

    def __init__(self, message: str, http_status: int = 422):
        self.message = message
        self.http_status = http_status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Public browse (learner / guest)
# ---------------------------------------------------------------------------

def get_published_paths() -> QuerySet[LearningPath]:
    """Published paths, prefetched so the list card can show milestone count
    and course thumbnails without N+1."""
    return (
        LearningPath.objects
        .filter(status=LearningPath.Status.PUBLISHED)
        .prefetch_related('milestones__course')
        .order_by('-created_at')
    )


def get_published_path_by_slug(slug: str) -> LearningPath:
    """Raises LearningPath.DoesNotExist if missing or not published — slug
    entry point, caller maps this to 403 per the project's access-denied
    policy (slugs are public once published)."""
    return (
        LearningPath.objects
        .prefetch_related('milestones__course')
        .get(slug=slug, status=LearningPath.Status.PUBLISHED)
    )


# ---------------------------------------------------------------------------
# Derived progress
# ---------------------------------------------------------------------------

MILESTONE_LOCKED = 'locked'
MILESTONE_AVAILABLE = 'available'
MILESTONE_IN_PROGRESS = 'in_progress'
MILESTONE_COMPLETED = 'completed'


def build_milestone_progress(user, milestones: list[LearningPathMilestone]) -> list[dict]:
    """One status per milestone, in position order. One query total.

    Status rules (see docs/architecture/28-learning-paths.md §4):
      - completed:   the learner's Enrollment for that course has completed_at
      - in_progress: an Enrollment exists (active or not) but isn't completed
      - available:   no enrollment yet, but every prior milestone is completed
      - locked:      no enrollment yet, and some prior milestone isn't completed
    """
    course_ids = [m.course_id for m in milestones]
    enrollments_by_course = {
        e.course_id: e
        for e in Enrollment.objects.filter(user=user, course_id__in=course_ids)
    }

    results = []
    all_prior_completed = True
    for m in milestones:
        enrollment = enrollments_by_course.get(m.course_id)
        if enrollment is not None and enrollment.completed_at is not None:
            status = MILESTONE_COMPLETED
        elif enrollment is not None:
            status = MILESTONE_IN_PROGRESS
            all_prior_completed = False
        elif all_prior_completed:
            status = MILESTONE_AVAILABLE
            all_prior_completed = False
        else:
            status = MILESTONE_LOCKED

        results.append({
            'milestone': m,
            'status': status,
            'enrollment': enrollment,
        })
    return results


def is_enrolled_in_path(user, path: LearningPath) -> bool:
    """Real LearningPathEnrollment lookup — never derive this from milestone
    status. A learner can complete a milestone's course entirely outside the
    path (independently, before ever joining it), so milestone #1 reading
    'available' does not imply the learner has joined this path."""
    return LearningPathEnrollment.objects.filter(user=user, path=path).exists()


def get_path_progress_percent(progress_rows: list[dict]) -> int:
    if not progress_rows:
        return 0
    completed = sum(1 for row in progress_rows if row['status'] == MILESTONE_COMPLETED)
    return round(completed / len(progress_rows) * 100)


# ---------------------------------------------------------------------------
# Learner enrollment (opt into a path)
# ---------------------------------------------------------------------------

def enroll_in_path(user, path: LearningPath) -> tuple[LearningPathEnrollment, bool]:
    """Idempotent add. Returns (enrollment, created) — mirrors add_to_wishlist."""
    if user.user_type != 'learner':
        raise ValidationError('Only learners can enroll in a learning path.')
    if path.status != LearningPath.Status.PUBLISHED:
        raise ValidationError('Only published learning paths can be enrolled in.')

    try:
        return LearningPathEnrollment.objects.get_or_create(user=user, path=path)
    except IntegrityError:
        return LearningPathEnrollment.objects.get(user=user, path=path), False


def leave_path(user, path: LearningPath) -> bool:
    """Returns True when a row was deleted. Never touches course Enrollment
    rows — leaving a path doesn't unenroll the learner from its courses."""
    deleted, _ = LearningPathEnrollment.objects.filter(user=user, path=path).delete()
    return bool(deleted)


def get_my_paths(user) -> QuerySet[LearningPathEnrollment]:
    return (
        LearningPathEnrollment.objects
        .filter(user=user)
        .select_related('path')
        .prefetch_related('path__milestones__course')
        .order_by('-created_at')
    )


# ---------------------------------------------------------------------------
# Authoring (instructor / admin)
# ---------------------------------------------------------------------------

def get_owned_paths(user) -> QuerySet[LearningPath]:
    return (
        LearningPath.objects
        .filter(created_by=user)
        .prefetch_related('milestones__course')
        .order_by('-created_at')
    )


def get_owned_path_or_404(user, pk: int) -> LearningPath:
    """Numeric-ID entry point — caller maps DoesNotExist to 404 (not-own is
    not distinguished from not-found, per the project's access-denied policy)."""
    return LearningPath.objects.prefetch_related('milestones__course').get(
        pk=pk, created_by=user,
    )


def add_milestone(path: LearningPath, course: NidusCourse, title: str = '') -> LearningPathMilestone:
    if not course.is_published:
        raise LearningPathError('Only published courses can be added as milestones.', 422)
    if LearningPathMilestone.objects.filter(path=path, course=course).exists():
        raise LearningPathError('This course is already a milestone on this path.', 422)

    next_position = (
        LearningPathMilestone.objects.filter(path=path).count() + 1
    )
    return LearningPathMilestone.objects.create(
        path=path, course=course, position=next_position, title=title,
    )


def remove_milestone(path: LearningPath, milestone_id: int) -> None:
    try:
        milestone = LearningPathMilestone.objects.get(pk=milestone_id, path=path)
    except LearningPathMilestone.DoesNotExist:
        raise LearningPathError('Milestone not found.', 404)
    milestone.delete()
    _reindex_positions(path)


def reorder_milestones(path: LearningPath, ordered_milestone_ids: list[int]) -> None:
    """Accepts the full ordered list of milestone ids and reassigns
    `position` in one pass.

    Two phases, both required: `uq_lpath_milestone_position` is checked
    per-statement (not deferred), so writing final positions directly can
    collide mid-loop whenever two rows swap slots — e.g. moving position 1
    to 2 write while position 2 still holds 2. `position` is also a
    PositiveIntegerField, so the parking slot can't be negative — phase 1
    parks every changed row above the current max position (a range no live
    row occupies, so it can never collide); phase 2 assigns the real final
    positions once no row holds a live target slot.
    """
    milestones_by_id = {m.pk: m for m in path.milestones.all()}
    if set(ordered_milestone_ids) != set(milestones_by_id.keys()):
        raise LearningPathError(
            'ordered_milestone_ids must include every milestone on this path exactly once.',
            400,
        )

    changed = [
        (milestone_id, index)
        for index, milestone_id in enumerate(ordered_milestone_ids, start=1)
        if milestones_by_id[milestone_id].position != index
    ]
    if not changed:
        return

    park_base = len(milestones_by_id)
    for offset, (milestone_id, _) in enumerate(changed, start=1):
        milestone = milestones_by_id[milestone_id]
        milestone.position = park_base + offset
        milestone.save(update_fields=['position'])

    for milestone_id, index in changed:
        milestone = milestones_by_id[milestone_id]
        milestone.position = index
        milestone.save(update_fields=['position'])


def _reindex_positions(path: LearningPath) -> None:
    """Closes any gap left by a deleted milestone so positions stay
    contiguous (1..N) without needing a separate reorder call."""
    for index, milestone in enumerate(path.milestones.order_by('position'), start=1):
        if milestone.position != index:
            milestone.position = index
            milestone.save(update_fields=['position'])

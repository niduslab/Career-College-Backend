"""Recording of learner activity days — the source of truth for the streak.

One entry point, `record_learner_activity`. It is called from the learner
consumption and submission paths in `learner_service.py`, never from a view,
and never for instructor preview.
"""

import logging

from django.db import IntegrityError
from django.utils import timezone

from courses.all_models.activity_models import LearnerActivityDay

logger = logging.getLogger(__name__)


def record_learner_activity(user, *, when=None) -> None:
    """Mark today as an active study day for `user`. Idempotent.

    Called on every learner-side content read and every submission, so it
    fires very often — hence the cheap shape: one `get_or_create` against
    `uq_activity_day_user_date`, which is a no-op insert after the first hit
    of the day.

    Never raises. This is bookkeeping hung off the side of real requests;
    a failure here must not turn a working lecture fetch into a 500. A lost
    row costs at most one day of streak, and the next action that day
    re-records it.

    Non-learners are skipped — instructor preview must not build a streak.
    """
    if user is None or not user.is_authenticated or user.user_type != 'learner':
        return

    activity_date = timezone.localdate(when or timezone.now())
    try:
        LearnerActivityDay.objects.get_or_create(
            user=user, activity_date=activity_date,
        )
    except IntegrityError:
        # Concurrent first-request-of-the-day lost the race. The row exists,
        # which is the whole point — nothing to do.
        pass
    except Exception:
        logger.exception(
            'Failed to record activity day for user=%s date=%s',
            user.pk, activity_date,
        )


def get_activity_dates(user, since) -> set:
    """Distinct local dates on which `user` studied, on or after `since`.

    One index-only scan of `idx_activity_user_date`. The unique constraint
    already guarantees one row per day, so no DISTINCT is needed.
    """
    return set(
        LearnerActivityDay.objects
        .filter(user=user, activity_date__gte=since)
        .values_list('activity_date', flat=True)
    )

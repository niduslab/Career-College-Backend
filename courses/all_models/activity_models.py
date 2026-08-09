from django.conf import settings
from django.db import models


class LearnerActivityDay(models.Model):
    """
    One row per learner per day on which they actually studied.

    Exists because the day-streak cannot be computed honestly from the
    consumption tables. `WatchProgress.last_watched_at` is `auto_now`, so it
    holds only the most recent touch per lecture — re-opening an old lecture
    *erases* the historical date it used to carry, and a learner who
    re-watches the same lecture daily for a month looks like a one-day
    streak. This table is the missing event record.

    Deliberately day-granular, not event-granular. A streak only ever asks
    "did anything happen on date D", and a video player POSTs progress every
    few seconds — logging each tick would be thousands of rows per lecture.
    The unique constraint collapses all of it to one row.

    Append-only by construction: `record_learner_activity` is the only writer
    and it only ever inserts. Nothing updates or deletes these rows.

    NOTE: this is *not* an XP ledger. XP needs one row per scoring event with
    a points value; the two have opposite de-duplication requirements, so
    they must stay separate models. See docs/architecture/27-learner-dashboard.md.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='activity_days',
        help_text='Learner who was active.',
    )
    activity_date = models.DateField(
        help_text=(
            'Local date of the activity, frozen at write time in '
            'settings.TIME_ZONE. Stored rather than derived so a later '
            'TIME_ZONE change cannot retroactively shift historical days.'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'learner_activity_days'
        verbose_name = 'Learner Activity Day'
        verbose_name_plural = 'Learner Activity Days'
        ordering = ['-activity_date']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'activity_date'],
                name='uq_activity_day_user_date',
            ),
        ]
        indexes = [
            # Covers the streak scan: WHERE user_id = ? AND activity_date >= ?
            # ordered descending — an index-only scan, no sort, no DISTINCT.
            models.Index(fields=['user', '-activity_date'], name='idx_activity_user_date'),
        ]

    def __str__(self):
        return f'{self.user_id} active on {self.activity_date}'

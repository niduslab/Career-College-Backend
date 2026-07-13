from django.conf import settings
from django.db import models

from courses.all_models.course_models import NidusCourse, TimestampedModel


class Order(TimestampedModel):
    """A learner's payment attempt for one paid course OR one paid webinar.

    Exactly one of `course` / `webinar` is set (DB check constraint). One row
    per gateway session. Re-checkout creates a new row and cancels stale
    pending ones, so `tran_id` ↔ gateway session is always 1:1 and the audit
    trail is never overwritten. `amount` snapshots the target's price at
    checkout time — later price edits never change what validation is
    compared against.
    """

    class Status(models.TextChoices):
        INITIATED = 'initiated', 'Initiated'    # row created, gateway not yet called
        PROCESSING = 'processing', 'Processing'  # GatewayPageURL issued, awaiting payment
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'     # user cancelled or superseded by a newer checkout

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='payment_orders',
        help_text='Learner who initiated the payment.',
    )
    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.PROTECT,
        related_name='payment_orders',
        null=True,
        blank=True,
        help_text='Course being purchased (mutually exclusive with webinar).',
    )
    schedule = models.ForeignKey(
        'courses.CourseSchedule',
        on_delete=models.PROTECT,
        related_name='payment_orders',
        null=True,
        blank=True,
        help_text='Cohort schedule being purchased, if this is a cohort seat. Null = self-paced.',
    )
    webinar = models.ForeignKey(
        'webinars.Webinar',
        on_delete=models.PROTECT,
        related_name='payment_orders',
        null=True,
        blank=True,
        help_text='Webinar being purchased (mutually exclusive with course).',
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text='Price snapshot at checkout time.',
    )
    currency = models.CharField(max_length=3, default='BDT')
    tran_id = models.CharField(
        max_length=30,
        unique=True,
        help_text='Our transaction id sent to the gateway (SSLCommerz max 30 chars).',
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.INITIATED,
        db_index=True,
    )
    val_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text='SSLCommerz validation id recorded after successful validation.',
    )
    gateway_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text='Raw gateway session + validation responses, kept for audit.',
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'payment_orders'
        verbose_name = 'Payment Order'
        verbose_name_plural = 'Payment Orders'
        ordering = ['-created_at']
        constraints = [
            # Exactly one purchase target per order.
            models.CheckConstraint(
                check=(
                    models.Q(course__isnull=False, webinar__isnull=True)
                    | models.Q(course__isnull=True, webinar__isnull=False)
                ),
                name='chk_order_exactly_one_target',
            ),
            # At most one successful purchase per (user, target); pending /
            # failed / cancelled attempts may pile up freely.
            # Self-paced course purchase: one PAID order per (user, course) ever.
            models.UniqueConstraint(
                fields=['user', 'course'],
                condition=models.Q(status='paid', schedule__isnull=True),
                name='uniq_paid_order_user_course_selfpaced',
            ),
            # Cohort seat purchase: one PAID order per (user, schedule); a learner
            # may buy into a different cohort of the same course later.
            models.UniqueConstraint(
                fields=['user', 'schedule'],
                condition=models.Q(status='paid', schedule__isnull=False),
                name='uniq_paid_order_user_schedule',
            ),
            models.UniqueConstraint(
                fields=['user', 'webinar'],
                condition=models.Q(status='paid'),
                name='uniq_paid_order_user_webinar',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'status'], name='idx_order_user_status'),
            models.Index(fields=['course', 'status'], name='idx_order_course_status'),
            models.Index(fields=['webinar', 'status'], name='idx_order_webinar_status'),
        ]

    @property
    def item(self):
        """The purchase target — the course or the webinar."""
        return self.course if self.course_id else self.webinar

    @property
    def item_type(self):
        return 'course' if self.course_id else 'webinar'

    def __str__(self):
        return f'Order {self.tran_id} ({self.status}) — {self.user_id} → {self.item_type} {self.course_id or self.webinar_id}'

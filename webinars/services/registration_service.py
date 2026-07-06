import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from webinars.models import Webinar, WebinarRegistration

from .webinar_service import WebinarError

logger = logging.getLogger(__name__)


def _active_registration_count(webinar):
    return WebinarRegistration.objects.filter(webinar=webinar, is_active=True).count()


@transaction.atomic
def register_for_webinar(user, webinar, *, via_payment=False):
    """
    Register a learner for a published webinar.

    Paid webinars (`price > 0`) require a completed payment order; the payment
    finalize path calls with `via_payment=True`, which also bypasses the
    published/capacity checks — money already moved, so access is honored even
    if the webinar was archived or filled mid-payment (overshoot is logged).
    Reactivates a cancelled row, enforces capacity, and dispatches
    WEBINAR_REGISTERED on commit. Raises WebinarError on any violation.
    """
    if user.user_type != 'learner':
        raise WebinarError('Only learners can register for webinars.', http_status=422)

    if not webinar.is_published and not via_payment:
        raise WebinarError('Registration is only allowed for published webinars.', http_status=422)

    if webinar.price > 0 and not via_payment:
        # Direct registration on a paid webinar is only for learners who
        # already purchased (covers cancel → re-register without a second
        # charge). Local import — keeps webinars→payments off module load.
        from payments.all_models.order_models import Order
        has_paid = Order.objects.filter(
            user=user, webinar=webinar, status=Order.Status.PAID,
        ).exists()
        if not has_paid:
            raise WebinarError(
                'This is a paid webinar. Complete payment via the checkout endpoint to register.',
                http_status=422,
            )

    # When the webinar is capacity-limited, lock its row so the capacity check
    if webinar.max_capacity is not None:
        Webinar.objects.select_for_update().filter(pk=webinar.pk).first()

    existing = (
        WebinarRegistration.objects
        .select_for_update()
        .filter(user=user, webinar=webinar)
        .first()
    )

    if existing and existing.is_active:
        raise WebinarError('You are already registered for this webinar.', http_status=422)

    # Capacity check (only counts active registrations). A validated payment
    # is honored even at capacity — overshoot from concurrent checkouts is
    # possible and logged rather than refused after money moved.
    if webinar.max_capacity is not None:
        active = _active_registration_count(webinar)
        if active >= webinar.max_capacity:
            if via_payment:
                logger.warning(
                    'Paid registration exceeds capacity: webinar=%s user=%s active=%s cap=%s',
                    webinar.pk, user.pk, active, webinar.max_capacity,
                )
            else:
                raise WebinarError('This webinar has reached its capacity.', http_status=422)

    if existing:
        existing.is_active = True
        existing.save(update_fields=['is_active', 'updated_at'])
        registration = existing
        logger.info('Webinar registration reactivated: user=%s webinar=%s', user.pk, webinar.pk)
    else:
        try:
            registration = WebinarRegistration.objects.create(
                user=user,
                webinar=webinar,
                is_active=True,
            )
        except IntegrityError as exc:
            raise WebinarError('You are already registered for this webinar.', http_status=422) from exc
        logger.info('Webinar registration created: user=%s webinar=%s', user.pk, webinar.pk)

    _dispatch_registration_notification(user, webinar)
    return registration


def _dispatch_registration_notification(user, webinar):
    _user_id = user.pk
    _webinar_title = webinar.title
    _webinar_slug = webinar.slug

    def _notify():
        from authentication.models import User
        from notifications.models import NotificationEventType
        from notifications.services.dispatcher import dispatch
        recipient = User.objects.filter(pk=_user_id).first()
        if recipient:
            dispatch(
                NotificationEventType.WEBINAR_REGISTERED,
                [recipient],
                context={'webinar_title': _webinar_title, 'webinar_slug': _webinar_slug},
            )

    transaction.on_commit(_notify)

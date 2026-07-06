from django.template.loader import render_to_string

from notifications.models import NotificationEventType

_ET = NotificationEventType

_EVENT_TEMPLATE_MAP = {
    _ET.ENROLLMENT_CREATED:      'notifications/emails/enrollment_created.html',
    _ET.COURSE_COMPLETED:        'notifications/emails/course_completed.html',
    _ET.COURSE_SUBMITTED:        'notifications/emails/course_submitted.html',
    _ET.COURSE_APPROVED:         'notifications/emails/course_approved.html',
    _ET.COURSE_REJECTED:         'notifications/emails/course_rejected.html',
    _ET.COURSE_MARKED_FINISHED:  'notifications/emails/course_marked_finished.html',
    _ET.COURSE_SENT_BACK:        'notifications/emails/course_sent_back.html',
    _ET.INVITE_SENT:             'notifications/emails/invite_sent.html',
    _ET.INVITE_ACCEPTED:         'notifications/emails/invite_accepted.html',
    _ET.INVITE_DECLINED:         'notifications/emails/invite_declined.html',
    _ET.VERIFICATION_SUBMITTED:  'notifications/emails/verification_submitted.html',
    _ET.VERIFICATION_APPROVED:   'notifications/emails/verification_approved.html',
    _ET.VERIFICATION_REJECTED:   'notifications/emails/verification_rejected.html',
    _ET.VERIFICATION_ACTION_REQ: 'notifications/emails/verification_action_required.html',
    _ET.INST_VERIFICATION_SUBMITTED:  'notifications/emails/institution_verification_submitted.html',
    _ET.INST_VERIFICATION_APPROVED:   'notifications/emails/institution_verification_approved.html',
    _ET.INST_VERIFICATION_REJECTED:   'notifications/emails/institution_verification_rejected.html',
    _ET.INST_VERIFICATION_ACTION_REQ: 'notifications/emails/institution_verification_action_required.html',
    _ET.WEBINAR_PUBLISHED:       'notifications/emails/webinar_published.html',
    _ET.WEBINAR_REGISTERED:      'notifications/emails/webinar_registered.html',
    _ET.PAYMENT_SUCCESSFUL:      'notifications/emails/payment_successful.html',
    _ET.PAYMENT_FAILED:          'notifications/emails/payment_failed.html',
}


def render_notification_email(notification) -> tuple[str, str, str]:
    """Return (subject, html_body, text_body) for the notification.

    Returns (None, None, None) if no email template is registered for this event.
    """
    template = _EVENT_TEMPLATE_MAP.get(notification.event_type)
    if not template:
        return None, None, None

    context = {
        'notification': notification,
        'recipient': notification.recipient,
        'title': notification.title,
        'body': notification.body,
        'data': notification.data,
    }
    html_body = render_to_string(template, context)
    text_body = render_to_string('notifications/emails/base_notification.txt', context)
    return notification.title, html_body, text_body

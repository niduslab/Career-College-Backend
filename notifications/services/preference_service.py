from notifications.models import NotificationCategory, NotificationEventType, NotificationPreference

# Maps each event type to its category so preference lookups are O(1).
EVENT_TO_CATEGORY: dict[str, str] = {
    NotificationEventType.ENROLLMENT_CREATED:      NotificationCategory.COURSE_ACTIVITY,
    NotificationEventType.LECTURE_COMPLETED:       NotificationCategory.COURSE_ACTIVITY,
    NotificationEventType.COURSE_COMPLETED:        NotificationCategory.COURSE_ACTIVITY,
    NotificationEventType.COURSE_SUBMITTED:        NotificationCategory.COURSE_MANAGEMENT,
    NotificationEventType.COURSE_APPROVED:         NotificationCategory.COURSE_MANAGEMENT,
    NotificationEventType.COURSE_REJECTED:         NotificationCategory.COURSE_MANAGEMENT,
    NotificationEventType.COURSE_ARCHIVED:         NotificationCategory.COURSE_MANAGEMENT,
    NotificationEventType.COURSE_MARKED_FINISHED:  NotificationCategory.COURSE_MANAGEMENT,
    NotificationEventType.COURSE_SENT_BACK:        NotificationCategory.COURSE_MANAGEMENT,
    NotificationEventType.COURSE_SCHEDULE_NEEDS_ATTENTION: NotificationCategory.COURSE_MANAGEMENT,
    NotificationEventType.VIDEO_READY:             NotificationCategory.COURSE_MANAGEMENT,
    NotificationEventType.VIDEO_FAILED:            NotificationCategory.COURSE_MANAGEMENT,
    NotificationEventType.INVITE_SENT:             NotificationCategory.COLLABORATION,
    NotificationEventType.INVITE_ACCEPTED:         NotificationCategory.COLLABORATION,
    NotificationEventType.INVITE_DECLINED:         NotificationCategory.COLLABORATION,
    NotificationEventType.REVIEW_RECEIVED:         NotificationCategory.COURSE_ACTIVITY,
    NotificationEventType.LEARNER_ENROLLED:        NotificationCategory.COURSE_ACTIVITY,
    NotificationEventType.VERIFICATION_SUBMITTED:  NotificationCategory.VERIFICATION,
    NotificationEventType.VERIFICATION_APPROVED:   NotificationCategory.VERIFICATION,
    NotificationEventType.VERIFICATION_REJECTED:   NotificationCategory.VERIFICATION,
    NotificationEventType.VERIFICATION_ACTION_REQ: NotificationCategory.VERIFICATION,
    NotificationEventType.INST_VERIFICATION_SUBMITTED:  NotificationCategory.VERIFICATION,
    NotificationEventType.INST_VERIFICATION_APPROVED:   NotificationCategory.VERIFICATION,
    NotificationEventType.INST_VERIFICATION_REJECTED:   NotificationCategory.VERIFICATION,
    NotificationEventType.INST_VERIFICATION_ACTION_REQ: NotificationCategory.VERIFICATION,
    NotificationEventType.EXPERT_ONBOARDED:        NotificationCategory.COLLABORATION,
    NotificationEventType.MESSAGE_RECEIVED:        NotificationCategory.MESSAGING,
    NotificationEventType.WEBINAR_REGISTERED:      NotificationCategory.COURSE_ACTIVITY,
    NotificationEventType.WEBINAR_PUBLISHED:       NotificationCategory.COURSE_MANAGEMENT,
    NotificationEventType.PAYMENT_SUCCESSFUL:      NotificationCategory.COURSE_ACTIVITY,
    NotificationEventType.PAYMENT_FAILED:          NotificationCategory.COURSE_ACTIVITY,
    NotificationEventType.QUESTION_POSTED:         NotificationCategory.COURSE_ACTIVITY,
    NotificationEventType.QUESTION_REPLIED:        NotificationCategory.COURSE_ACTIVITY,
}


def get_email_preference(user, event_type: str) -> bool:
    """Return True if email is enabled for this user+event combo.

    Uses get_or_create with default=True so new users get email opted-in
    without requiring a setup step.
    """
    category = EVENT_TO_CATEGORY.get(event_type)
    if not category:
        return True
    pref, _ = NotificationPreference.objects.get_or_create(
        user=user,
        category=category,
        defaults={'email_enabled': True, 'push_enabled': True},
    )
    return pref.email_enabled



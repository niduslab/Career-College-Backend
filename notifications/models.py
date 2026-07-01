from django.conf import settings
from django.db import models


class NotificationEventType(models.TextChoices):
    ENROLLMENT_CREATED       = 'enrollment.created',       'Enrollment Created'
    LECTURE_COMPLETED        = 'lecture.completed',        'Lecture Completed'
    COURSE_COMPLETED         = 'course.completed',         'Course Completed'
    COURSE_SUBMITTED         = 'course.submitted_for_review', 'Course Submitted for Review'
    COURSE_APPROVED          = 'course.approved',          'Course Approved'
    COURSE_REJECTED          = 'course.rejected',          'Course Rejected'
    COURSE_ARCHIVED          = 'course.archived',          'Course Archived'
    COURSE_MARKED_FINISHED   = 'course.marked_finished',   'Course Marked Finished'
    COURSE_SENT_BACK         = 'course.sent_back',         'Course Sent Back'
    VIDEO_READY              = 'video.transcoding_completed', 'Video Transcoding Completed'
    VIDEO_FAILED             = 'video.transcoding_failed', 'Video Transcoding Failed'
    INVITE_SENT              = 'invite.sent',              'Invite Sent'
    INVITE_ACCEPTED          = 'invite.accepted',          'Invite Accepted'
    INVITE_DECLINED          = 'invite.declined',          'Invite Declined'
    REVIEW_RECEIVED          = 'review.received',          'Review Received'
    LEARNER_ENROLLED         = 'learner.enrolled',         'Learner Enrolled'
    VERIFICATION_SUBMITTED   = 'verification.submitted',   'Verification Submitted'
    VERIFICATION_APPROVED    = 'verification.approved',    'Verification Approved'
    VERIFICATION_REJECTED    = 'verification.rejected',    'Verification Rejected'
    VERIFICATION_ACTION_REQ  = 'verification.action_required', 'Verification Action Required'
    INST_VERIFICATION_SUBMITTED  = 'institution_verification.submitted',  'Institution Verification Submitted'
    INST_VERIFICATION_APPROVED   = 'institution_verification.approved',   'Institution Verification Approved'
    INST_VERIFICATION_REJECTED   = 'institution_verification.rejected',   'Institution Verification Rejected'
    INST_VERIFICATION_ACTION_REQ = 'institution_verification.action_required', 'Institution Verification Action Required'
    EXPERT_ONBOARDED         = 'expert.onboarded',         'Expert Onboarded'
    MESSAGE_RECEIVED         = 'message.received',         'Message Received'
    WEBINAR_PUBLISHED        = 'webinar.published',        'Webinar Published'
    WEBINAR_REGISTERED       = 'webinar.registered',       'Webinar Registration Confirmed'


class NotificationCategory(models.TextChoices):
    COURSE_ACTIVITY   = 'course_activity',   'Course Activity'
    ASSESSMENTS       = 'assessments',       'Assessments'
    COURSE_MANAGEMENT = 'course_management', 'Course Management'
    COLLABORATION     = 'collaboration',     'Collaboration'
    VERIFICATION      = 'verification',      'Verification'
    MESSAGING         = 'messaging',         'Messaging'


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
        db_index=True,
    )
    event_type = models.CharField(
        max_length=64,
        choices=NotificationEventType.choices,
        db_index=True,
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    data = models.JSONField(default=dict)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    deduplication_key = models.CharField(max_length=128, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', 'created_at']),
        ]

    def __str__(self):
        return f'[{self.event_type}] → {self.recipient_id} ({self.created_at})'


class NotificationPreference(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_preferences',
    )
    category = models.CharField(max_length=64, choices=NotificationCategory.choices)
    email_enabled = models.BooleanField(default=True)
    push_enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = [('user', 'category')]

    def __str__(self):
        return f'{self.user_id} / {self.category}'

from notifications.models import NotificationEventType

# Each builder receives (recipient, context) and returns a dict with keys:
#   title, body, data, deduplication_key (None if not needed)

_ET = NotificationEventType


def _build(title, body, data=None, dedup_key=None):
    return {'title': title, 'body': body, 'data': data or {}, 'deduplication_key': dedup_key}


def _enrollment_created(recipient, ctx):
    return _build(
        title=f'Enrolled in {ctx["course_title"]}',
        body=f'You are now enrolled in "{ctx["course_title"]}". Start learning!',
        data={'course_slug': ctx['course_slug']},
    )


def _lecture_completed(recipient, ctx):
    return _build(
        title='Lecture completed',
        body=f'You completed "{ctx["lecture_title"]}" in {ctx["course_title"]}.',
        data={'course_slug': ctx['course_slug'], 'lecture_id': ctx['lecture_id']},
    )


def _course_completed(recipient, ctx):
    return _build(
        title='Course completed!',
        body=f'Congratulations! You have completed "{ctx["course_title"]}". Your certificate is ready.',
        data={'course_slug': ctx['course_slug'], 'certificate_uid': ctx.get('certificate_uid', '')},
        dedup_key=f'course.completed:{recipient.id}:{ctx["enrollment_id"]}',
    )


def _course_submitted(recipient, ctx):
    return _build(
        title='Course submitted for review',
        body=f'"{ctx["course_title"]}" by {ctx["instructor_name"]} has been submitted for review.',
        data={'course_id': ctx['course_id']},
    )


def _course_approved(recipient, ctx):
    return _build(
        title='Course approved',
        body=f'Your course "{ctx["course_title"]}" has been approved and is now published.',
        data={'course_slug': ctx['course_slug']},
    )


def _course_rejected(recipient, ctx):
    reason = ctx.get('rejection_reason', '')
    body = f'Your course "{ctx["course_title"]}" was rejected.'
    if reason:
        body += f' Reason: {reason}'
    return _build(
        title='Course rejected',
        body=body,
        data={'course_slug': ctx['course_slug']},
    )


def _course_archived(recipient, ctx):
    return _build(
        title='Course archived',
        body=f'Your course "{ctx["course_title"]}" has been archived.',
        data={'course_slug': ctx['course_slug']},
    )


def _course_marked_finished(recipient, ctx):
    return _build(
        title='Course ready for your review',
        body=f'{ctx["expert_name"]} marked "{ctx["course_title"]}" as finished. '
             'Review it and submit to the platform admin, or send it back for changes.',
        data={'course_id': ctx['course_id']},
    )


def _course_sent_back(recipient, ctx):
    reason = ctx.get('rejection_reason', '')
    body = f'{ctx["institution_name"]} sent "{ctx["course_title"]}" back for changes.'
    if reason:
        body += f' Reason: {reason}'
    return _build(
        title='Course sent back for changes',
        body=body,
        data={'course_slug': ctx['course_slug']},
    )


def _course_schedule_needs_attention(recipient, ctx):
    labels = ', '.join(ctx['schedule_labels'])
    return _build(
        title='Schedule needs date fixes',
        body=f'"{ctx["course_title"]}" was approved and published, but its schedule(s) '
             f'({labels}) could not auto-activate because the dates no longer make sense. '
             'Update the dates and activate the schedule manually.',
        data={'course_slug': ctx['course_slug']},
    )


def _video_ready(recipient, ctx):
    return _build(
        title='Video ready',
        body=f'Video for "{ctx["lecture_title"]}" has finished processing.',
        data={'course_slug': ctx['course_slug'], 'lecture_id': ctx['lecture_id']},
    )


def _video_failed(recipient, ctx):
    return _build(
        title='Video processing failed',
        body=f'Video processing failed for "{ctx["lecture_title"]}". Please re-upload.',
        data={'course_slug': ctx['course_slug'], 'lecture_id': ctx['lecture_id']},
        dedup_key=f'video.transcoding_failed:{ctx.get("course_id")}:{ctx.get("video_asset_id")}',
    )


def _invite_sent(recipient, ctx):
    return _build(
        title='Co-instructor invitation',
        body=f'You have been invited to co-instruct "{ctx["course_title"]}".',
        data={'invite_id': ctx['invite_id'], 'course_slug': ctx['course_slug']},
    )


def _invite_accepted(recipient, ctx):
    return _build(
        title='Invitation accepted',
        body=f'{ctx["invitee_name"]} accepted your invitation to co-instruct "{ctx["course_title"]}".',
        data={'course_slug': ctx['course_slug']},
    )


def _invite_declined(recipient, ctx):
    return _build(
        title='Invitation declined',
        body=f'{ctx["invitee_name"]} declined your invitation to co-instruct "{ctx["course_title"]}".',
        data={'course_slug': ctx['course_slug']},
    )


def _review_received(recipient, ctx):
    return _build(
        title='New course review',
        body=f'"{ctx["course_title"]}" received a {ctx["rating"]}-star review.',
        data={'course_slug': ctx['course_slug'], 'review_id': ctx['review_id']},
    )


def _learner_enrolled(recipient, ctx):
    return _build(
        title='New learner enrolled',
        body=f'{ctx["learner_name"]} enrolled in your course "{ctx["course_title"]}".',
        data={'course_slug': ctx['course_slug']},
    )


def _verification_submitted(recipient, ctx):
    return _build(
        title='Identity verification submitted',
        body=f'{ctx["instructor_name"]} submitted an identity verification request.',
        data={'verification_id': ctx['verification_id']},
    )


def _verification_approved(recipient, ctx):
    return _build(
        title='Identity verification approved',
        body='Your identity has been verified. You can now publish courses.',
        data={},
    )


def _verification_rejected(recipient, ctx):
    return _build(
        title='Identity verification rejected',
        body='Your identity verification was rejected. Please re-submit with valid documents.',
        data={},
    )


def _verification_action_required(recipient, ctx):
    note = ctx.get('admin_note', '')
    body = 'Additional action is required for your identity verification.'
    if note:
        body += f' Note: {note}'
    return _build(
        title='Action required for verification',
        body=body,
        data={},
    )


def _inst_verification_submitted(recipient, ctx):
    return _build(
        title='Institution verification submitted',
        body=f'{ctx["institution_name"]} submitted a credential-verification request.',
        data={'verification_id': ctx['verification_id']},
    )


def _inst_verification_approved(recipient, ctx):
    return _build(
        title='Institution verified',
        body='Your institution has been verified. You can now onboard experts and publish courses.',
        data={},
    )


def _inst_verification_rejected(recipient, ctx):
    reason = ctx.get('rejection_reason', '')
    body = 'Your institution verification was rejected. Please re-submit with valid documents.'
    if reason:
        body += f' Reason: {reason}'
    return _build(
        title='Institution verification rejected',
        body=body,
        data={},
    )


def _inst_verification_action_required(recipient, ctx):
    note = ctx.get('admin_note', '')
    body = 'Additional action is required for your institution verification.'
    if note:
        body += f' Note: {note}'
    return _build(
        title='Action required for institution verification',
        body=body,
        data={},
    )


def _expert_onboarded(recipient, ctx):
    return _build(
        title='You have been added as an expert',
        body=f'{ctx["institution_name"]} added you as an expert. '
             'Verify your email to activate your account and start authoring courses.',
        data={'institution_name': ctx['institution_name']},
    )


def _message_received(recipient, ctx):
    preview = ctx.get('body_preview', '')
    if len(preview) > 120:
        preview = preview[:117] + '...'
    course_title = ctx.get('course_title')
    body = f'In {course_title}: {preview}' if course_title else preview
    return _build(
        title=f'New message from {ctx["sender_name"]}',
        body=body,
        data={
            'conversation_id': ctx['conversation_id'],
            'course_slug': ctx.get('course_slug'),
        },
    )


def _webinar_published(recipient, ctx):
    return _build(
        title='Webinar published',
        body=f'"{ctx["webinar_title"]}" is now published and live in the catalog.',
        data={'webinar_slug': ctx['webinar_slug']},
    )


def _webinar_registered(recipient, ctx):
    return _build(
        title=f'Registered for {ctx["webinar_title"]}',
        body=f'You are registered for "{ctx["webinar_title"]}". We will remind you before it starts.',
        data={'webinar_slug': ctx['webinar_slug']},
    )


def _payment_successful(recipient, ctx):
    access_note = 'You are now enrolled.' if ctx['item_type'] == 'course' else 'You are now registered.'
    return _build(
        title='Payment successful',
        body=(
            f'Your payment of {ctx["amount"]} {ctx["currency"]} for '
            f'"{ctx["item_title"]}" was successful. {access_note}'
        ),
        data={'item_type': ctx['item_type'], 'item_slug': ctx['item_slug'], 'tran_id': ctx['tran_id']},
        dedup_key=f'payment.successful:{recipient.id}:{ctx["tran_id"]}',
    )


def _payment_failed(recipient, ctx):
    return _build(
        title='Payment failed',
        body=(
            f'Your payment for "{ctx["item_title"]}" could not be completed. '
            f'If money was deducted it will be reversed by your payment provider. '
            f'You can try again from the {ctx["item_type"]} page.'
        ),
        data={'item_type': ctx['item_type'], 'item_slug': ctx['item_slug'], 'tran_id': ctx['tran_id']},
        dedup_key=f'payment.failed:{recipient.id}:{ctx["tran_id"]}',
    )


_MAX_SUSPENSION_REASON_LEN = 500


def _account_suspended(recipient, ctx):
    reason = (ctx.get('reason') or '').strip()
    body = 'Your Career College account has been suspended by an administrator.'
    if reason:
        # Reason is admin free-text and user-facing; cap its length so a huge
        # note can't bloat the email. Django templates autoescape it on render.
        body += f' Reason: {reason[:_MAX_SUSPENSION_REASON_LEN]}'
    body += ' You can no longer sign in. If you believe this is a mistake, contact support.'
    return _build(title='Account suspended', body=body, data={})


def _account_reactivated(recipient, ctx):
    return _build(
        title='Account reactivated',
        body='Your Career College account has been reactivated. You can sign in again.',
        data={},
    )


def _question_posted(recipient, ctx):
    return _build(
        title='New question in your course',
        body=f'A learner asked a question in "{ctx["course_title"]}".',
        data={'course_slug': ctx['course_slug'], 'question_id': ctx['question_id']},
    )


def _question_replied(recipient, ctx):
    who = 'An instructor' if ctx.get('is_instructor_reply') else 'Someone'
    return _build(
        title='New reply to a question',
        body=f'{who} replied to "{ctx["question_title"]}".',
        data={'course_slug': ctx.get('course_slug'), 'question_id': ctx['question_id']},
    )


_BUILDERS = {
    _ET.ENROLLMENT_CREATED:      _enrollment_created,
    _ET.LECTURE_COMPLETED:       _lecture_completed,
    _ET.COURSE_COMPLETED:        _course_completed,
    _ET.COURSE_SUBMITTED:        _course_submitted,
    _ET.COURSE_APPROVED:         _course_approved,
    _ET.COURSE_REJECTED:         _course_rejected,
    _ET.COURSE_ARCHIVED:         _course_archived,
    _ET.COURSE_MARKED_FINISHED:  _course_marked_finished,
    _ET.COURSE_SENT_BACK:        _course_sent_back,
    _ET.COURSE_SCHEDULE_NEEDS_ATTENTION: _course_schedule_needs_attention,
    _ET.VIDEO_READY:             _video_ready,
    _ET.VIDEO_FAILED:            _video_failed,
    _ET.INVITE_SENT:             _invite_sent,
    _ET.INVITE_ACCEPTED:         _invite_accepted,
    _ET.INVITE_DECLINED:         _invite_declined,
    _ET.REVIEW_RECEIVED:         _review_received,
    _ET.LEARNER_ENROLLED:        _learner_enrolled,
    _ET.VERIFICATION_SUBMITTED:  _verification_submitted,
    _ET.VERIFICATION_APPROVED:   _verification_approved,
    _ET.VERIFICATION_REJECTED:   _verification_rejected,
    _ET.VERIFICATION_ACTION_REQ: _verification_action_required,
    _ET.INST_VERIFICATION_SUBMITTED:  _inst_verification_submitted,
    _ET.INST_VERIFICATION_APPROVED:   _inst_verification_approved,
    _ET.INST_VERIFICATION_REJECTED:   _inst_verification_rejected,
    _ET.INST_VERIFICATION_ACTION_REQ: _inst_verification_action_required,
    _ET.EXPERT_ONBOARDED:        _expert_onboarded,
    _ET.MESSAGE_RECEIVED:        _message_received,
    _ET.WEBINAR_PUBLISHED:       _webinar_published,
    _ET.WEBINAR_REGISTERED:      _webinar_registered,
    _ET.PAYMENT_SUCCESSFUL:      _payment_successful,
    _ET.PAYMENT_FAILED:          _payment_failed,
    _ET.ACCOUNT_SUSPENDED:       _account_suspended,
    _ET.ACCOUNT_REACTIVATED:     _account_reactivated,
    _ET.QUESTION_POSTED:         _question_posted,
    _ET.QUESTION_REPLIED:        _question_replied,
}


def build_notification_payload(event_type: str, recipient, context: dict) -> dict:
    """Return {title, body, data, deduplication_key} for an event+recipient.

    Raises KeyError if event_type has no registered builder.
    """
    builder = _BUILDERS[event_type]
    return builder(recipient, context)

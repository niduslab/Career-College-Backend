"""Tests for the messaging service layer."""

from unittest.mock import patch

from django.test import TestCase

from authentication.models import User
from courses.models import Enrollment, NidusCourse
from messaging.models import Conversation, Message
from messaging.services.messaging_service import (
    MessagingError,
    get_conversation_for_participant,
    get_messages,
    get_or_create_conversation,
    get_unread_conversation_count,
    get_unread_counts,
    list_conversations,
    mark_read,
    send_message,
)


def _make_user(email, user_type, full_name='Test User'):
    return User.objects.create_user(
        email=email,
        password='testpass123',
        user_type=user_type,
        full_name=full_name,
        is_email_verified=True,
    )


def _make_course(creator, slug='test-course'):
    return NidusCourse.objects.create(
        title='Test Course',
        slug=slug,
        created_by=creator,
        status='published',
    )


def _enroll(learner, course, is_active=True):
    return Enrollment.objects.create(user=learner, course=course, is_active=is_active)


class GetOrCreateConversationTest(TestCase):
    def setUp(self):
        self.learner = _make_user('learner@s.com', 'learner', 'Alice')
        self.instructor = _make_user('instructor@s.com', 'instructor', 'Bob')
        self.course = _make_course(self.instructor)
        self.course.instructors.add(self.instructor)
        _enroll(self.learner, self.course)

    @patch('messaging.services.messaging_service._push_ws_and_notify')
    def test_creates_conversation_and_message(self, mock_dispatch):
        conv, created = get_or_create_conversation(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
            opener_body='Hello!',
        )
        self.assertTrue(created)
        self.assertEqual(conv.learner, self.learner)
        self.assertEqual(Message.objects.filter(conversation=conv).count(), 1)

    @patch('messaging.services.messaging_service._push_ws_and_notify')
    def test_idempotent_second_call(self, mock_dispatch):
        get_or_create_conversation(self.learner, self.instructor, self.course, 'First')
        conv2, created = get_or_create_conversation(self.learner, self.instructor, self.course, 'Second')
        self.assertFalse(created)
        # Second call must NOT create another message.
        self.assertEqual(Message.objects.filter(conversation=conv2).count(), 1)

    def test_blocked_without_enrollment(self):
        learner2 = _make_user('learner2@s.com', 'learner', 'Charlie')
        with self.assertRaises(MessagingError) as ctx:
            get_or_create_conversation(learner2, self.instructor, self.course, 'Hi')
        self.assertEqual(ctx.exception.http_status, 403)

    def test_blocked_if_enrollment_inactive(self):
        learner3 = _make_user('learner3@s.com', 'learner', 'Diana')
        _enroll(learner3, self.course, is_active=False)
        with self.assertRaises(MessagingError) as ctx:
            get_or_create_conversation(learner3, self.instructor, self.course, 'Hi')
        self.assertEqual(ctx.exception.http_status, 403)

    def test_blocked_if_instructor_not_on_course(self):
        other_instructor = _make_user('other@s.com', 'instructor', 'Eve')
        with self.assertRaises(MessagingError) as ctx:
            get_or_create_conversation(self.learner, other_instructor, self.course, 'Hi')
        self.assertEqual(ctx.exception.http_status, 403)


class SendMessageTest(TestCase):
    def setUp(self):
        self.learner = _make_user('learner@sm.com', 'learner', 'Frank')
        self.instructor = _make_user('instructor@sm.com', 'instructor', 'Grace')
        self.course = _make_course(self.instructor, 'sm-course')
        self.course.instructors.add(self.instructor)
        _enroll(self.learner, self.course)
        self.conv = Conversation.objects.create(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
        )

    @patch('messaging.services.messaging_service._push_ws_and_notify')
    def test_learner_can_send(self, mock_dispatch):
        msg = send_message(self.learner, self.conv.pk, 'Question!')
        self.assertEqual(msg.sender, self.learner)
        self.assertFalse(msg.is_deleted)

    @patch('messaging.services.messaging_service._push_ws_and_notify')
    def test_instructor_can_reply(self, mock_dispatch):
        msg = send_message(self.instructor, self.conv.pk, 'Answer!')
        self.assertEqual(msg.sender, self.instructor)

    def test_blocked_if_learner_unenrolled(self):
        Enrollment.objects.filter(user=self.learner, course=self.course).update(is_active=False)
        with self.assertRaises(MessagingError) as ctx:
            send_message(self.learner, self.conv.pk, 'Still here?')
        self.assertEqual(ctx.exception.http_status, 403)

    def test_blocked_if_instructor_removed(self):
        self.course.instructors.remove(self.instructor)
        with self.assertRaises(MessagingError) as ctx:
            send_message(self.instructor, self.conv.pk, 'Reply')
        self.assertEqual(ctx.exception.http_status, 403)

    def test_nonparticipant_raises_does_not_exist(self):
        outsider = _make_user('outsider@sm.com', 'learner', 'Oscar')
        with self.assertRaises(Conversation.DoesNotExist):
            send_message(outsider, self.conv.pk, 'Hack')

    def test_unknown_conversation_raises_does_not_exist(self):
        with self.assertRaises(Conversation.DoesNotExist):
            send_message(self.learner, 99999, 'Hi')


class MarkReadTest(TestCase):
    def setUp(self):
        self.learner = _make_user('learner@mr.com', 'learner', 'Heidi')
        self.instructor = _make_user('instructor@mr.com', 'instructor', 'Ivan')
        self.course = _make_course(self.instructor, 'mr-course')
        self.course.instructors.add(self.instructor)
        _enroll(self.learner, self.course)
        self.conv = Conversation.objects.create(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
        )

    def test_learner_mark_read_updates_learner_timestamp(self):
        self.assertIsNone(Conversation.objects.get(pk=self.conv.pk).learner_last_read_at)
        mark_read(self.learner, self.conv.pk)
        self.assertIsNotNone(Conversation.objects.get(pk=self.conv.pk).learner_last_read_at)
        # Instructor timestamp must remain untouched.
        self.assertIsNone(Conversation.objects.get(pk=self.conv.pk).instructor_last_read_at)

    def test_instructor_mark_read_updates_instructor_timestamp(self):
        mark_read(self.instructor, self.conv.pk)
        self.assertIsNotNone(Conversation.objects.get(pk=self.conv.pk).instructor_last_read_at)
        self.assertIsNone(Conversation.objects.get(pk=self.conv.pk).learner_last_read_at)

    def test_nonparticipant_raises_does_not_exist(self):
        outsider = _make_user('outsider@mr.com', 'learner', 'Judy')
        with self.assertRaises(Conversation.DoesNotExist):
            mark_read(outsider, self.conv.pk)


class GetConversationForParticipantTest(TestCase):
    def setUp(self):
        self.learner = _make_user('learner@gp.com', 'learner', 'Karl')
        self.instructor = _make_user('instructor@gp.com', 'instructor', 'Lara')
        self.course = _make_course(self.instructor, 'gp-course')
        self.course.instructors.add(self.instructor)
        self.conv = Conversation.objects.create(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
        )

    def test_learner_can_fetch(self):
        conv = get_conversation_for_participant(self.learner, self.conv.pk)
        self.assertEqual(conv.pk, self.conv.pk)

    def test_instructor_can_fetch(self):
        conv = get_conversation_for_participant(self.instructor, self.conv.pk)
        self.assertEqual(conv.pk, self.conv.pk)

    def test_outsider_raises_404(self):
        outsider = _make_user('outsider@gp.com', 'learner', 'Mike')
        with self.assertRaises(Conversation.DoesNotExist):
            get_conversation_for_participant(outsider, self.conv.pk)

    def test_nonexistent_id_raises_404(self):
        with self.assertRaises(Conversation.DoesNotExist):
            get_conversation_for_participant(self.learner, 99999)


class GetUnreadCountsTest(TestCase):
    def setUp(self):
        self.learner = _make_user('learner@uc.com', 'learner', 'Nina')
        self.instructor = _make_user('instructor@uc.com', 'instructor', 'Olaf')
        self.course = _make_course(self.instructor, 'uc-course')
        self.course.instructors.add(self.instructor)
        self.conv = Conversation.objects.create(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
        )

    def test_unread_count_before_any_read(self):
        Message.objects.create(conversation=self.conv, sender=self.instructor, body='Hi')
        counts = get_unread_counts(self.learner)
        self.assertEqual(len(counts), 1)
        self.assertEqual(counts[0]['conversation_id'], self.conv.pk)
        self.assertEqual(counts[0]['unread_count'], 1)

    def test_unread_count_zero_after_mark_read(self):
        Message.objects.create(conversation=self.conv, sender=self.instructor, body='Hi')
        mark_read(self.learner, self.conv.pk)
        counts = get_unread_counts(self.learner)
        self.assertEqual(len(counts), 0)

    def test_soft_deleted_messages_excluded(self):
        msg = Message.objects.create(conversation=self.conv, sender=self.instructor, body='Gone')
        msg.is_deleted = True
        msg.save()
        counts = get_unread_counts(self.learner)
        self.assertEqual(len(counts), 0)


class GetUnreadConversationCountTest(TestCase):
    def setUp(self):
        self.learner = _make_user('learner@ucc.com', 'learner', 'Peggy')
        self.instructor = _make_user('instructor@ucc.com', 'instructor', 'Quinn')
        self.course = _make_course(self.instructor, 'ucc-course')
        self.course.instructors.add(self.instructor)
        self.conv = Conversation.objects.create(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
        )

    def test_zero_when_no_messages(self):
        self.assertEqual(get_unread_conversation_count(self.learner), 0)
        self.assertEqual(get_unread_conversation_count(self.instructor), 0)

    def test_counts_never_opened_conversation_with_message(self):
        # last_read is NULL → a visible message makes the conversation unread.
        Message.objects.create(conversation=self.conv, sender=self.instructor, body='Hi')
        self.assertEqual(get_unread_conversation_count(self.learner), 1)

    def test_zero_after_mark_read(self):
        Message.objects.create(conversation=self.conv, sender=self.instructor, body='Hi')
        mark_read(self.learner, self.conv.pk)
        self.assertEqual(get_unread_conversation_count(self.learner), 0)

    def test_soft_deleted_message_does_not_count(self):
        msg = Message.objects.create(conversation=self.conv, sender=self.instructor, body='Gone')
        msg.is_deleted = True
        msg.save()
        self.assertEqual(get_unread_conversation_count(self.learner), 0)

    def test_counts_distinct_conversations_not_messages(self):
        # Three unread messages in ONE conversation → count is 1, not 3.
        for _ in range(3):
            Message.objects.create(conversation=self.conv, sender=self.instructor, body='msg')
        self.assertEqual(get_unread_conversation_count(self.learner), 1)

    def test_counts_multiple_conversations(self):
        course2 = _make_course(self.instructor, 'ucc-course-2')
        course2.instructors.add(self.instructor)
        conv2 = Conversation.objects.create(
            learner=self.learner, instructor=self.instructor, course=course2,
        )
        Message.objects.create(conversation=self.conv, sender=self.instructor, body='one')
        Message.objects.create(conversation=conv2, sender=self.instructor, body='two')
        self.assertEqual(get_unread_conversation_count(self.learner), 2)

    def test_matches_len_of_unread_counts(self):
        # The REST endpoint (this fn) and the WS unread_summary list length must agree.
        Message.objects.create(conversation=self.conv, sender=self.instructor, body='Hi')
        self.assertEqual(
            get_unread_conversation_count(self.learner),
            len(get_unread_counts(self.learner)),
        )

    def test_instructor_side_counted_independently(self):
        # A message from the learner is unread for the instructor.
        Message.objects.create(conversation=self.conv, sender=self.learner, body='Question')
        self.assertEqual(get_unread_conversation_count(self.instructor), 1)
        mark_read(self.instructor, self.conv.pk)
        self.assertEqual(get_unread_conversation_count(self.instructor), 0)

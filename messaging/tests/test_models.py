"""Tests for Conversation and Message model constraints."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from authentication.models import User
from courses.models import NidusCourse
from messaging.models import Conversation, ConversationParticipant, Message
from messaging.services.messaging_service import _pair_key


def _make_user(email, user_type, full_name='Test User'):
    return User.objects.create_user(
        email=email,
        password='testpass123',
        user_type=user_type,
        full_name=full_name,
        is_email_verified=True,
    )


def _make_course(creator):
    return NidusCourse.objects.create(
        title='Test Course',
        slug='test-course',
        created_by=creator,
        status='published',
    )


def _make_conversation(user_a, user_b, course, conversation_type='learner_instructor'):
    conv = Conversation.objects.create(
        conversation_type=conversation_type, course=course,
        participant_key=_pair_key(user_a.id, user_b.id),
    )
    ConversationParticipant.objects.create(conversation=conv, user=user_a)
    ConversationParticipant.objects.create(conversation=conv, user=user_b)
    return conv


class ConversationModelTest(TestCase):
    def setUp(self):
        self.learner = _make_user('learner@test.com', 'learner', 'Alice')
        self.instructor = _make_user('instructor@test.com', 'instructor', 'Bob')
        self.course = _make_course(self.instructor)
        self.course.instructors.add(self.instructor)

    def test_create_conversation(self):
        conv = _make_conversation(self.learner, self.instructor, self.course)
        self.assertIsNotNone(conv.pk)
        self.assertEqual(conv.participants.count(), 2)
        self.assertTrue(all(p.last_read_at is None for p in conv.participants.all()))

    def test_unique_constraint(self):
        _make_conversation(self.learner, self.instructor, self.course)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Conversation.objects.create(
                    conversation_type='learner_instructor',
                    course=self.course,
                    participant_key=_pair_key(self.learner.id, self.instructor.id),
                )

    def test_cascade_delete_with_course(self):
        conv = _make_conversation(self.learner, self.instructor, self.course)
        Message.objects.create(conversation=conv, sender=self.learner, body='Hi')
        self.course.delete()
        self.assertFalse(Conversation.objects.filter(pk=conv.pk).exists())
        self.assertFalse(Message.objects.filter(conversation=conv).exists())

    def test_str(self):
        conv = _make_conversation(self.learner, self.instructor, self.course)
        self.assertIn(str(conv.pk), str(conv))


class MessageModelTest(TestCase):
    def setUp(self):
        self.learner = _make_user('learner2@test.com', 'learner', 'Carol')
        self.instructor = _make_user('instructor2@test.com', 'instructor', 'Dave')
        self.course = _make_course(self.instructor)
        self.course.instructors.add(self.instructor)
        self.conv = _make_conversation(self.learner, self.instructor, self.course)

    def test_create_message(self):
        msg = Message.objects.create(
            conversation=self.conv,
            sender=self.learner,
            body='Hello instructor!',
        )
        self.assertFalse(msg.is_deleted)
        self.assertIsNotNone(msg.created_at)

    def test_soft_delete_flag(self):
        msg = Message.objects.create(
            conversation=self.conv,
            sender=self.learner,
            body='Delete me',
        )
        msg.is_deleted = True
        msg.save()
        self.assertTrue(Message.objects.get(pk=msg.pk).is_deleted)

    def test_str(self):
        msg = Message.objects.create(
            conversation=self.conv,
            sender=self.learner,
            body='Test',
        )
        self.assertIn(str(msg.pk), str(msg))

    def test_ordering_oldest_first(self):
        for i in range(3):
            Message.objects.create(conversation=self.conv, sender=self.learner, body=f'msg {i}')
        ids = list(Message.objects.filter(conversation=self.conv).values_list('id', flat=True))
        self.assertEqual(ids, sorted(ids))

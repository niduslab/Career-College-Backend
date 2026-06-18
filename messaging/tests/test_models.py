"""Tests for Conversation and Message model constraints."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from authentication.models import User
from courses.models import NidusCourse
from messaging.models import Conversation, Message


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


class ConversationModelTest(TestCase):
    def setUp(self):
        self.learner = _make_user('learner@test.com', 'learner', 'Alice')
        self.instructor = _make_user('instructor@test.com', 'instructor', 'Bob')
        self.course = _make_course(self.instructor)
        self.course.instructors.add(self.instructor)

    def test_create_conversation(self):
        conv = Conversation.objects.create(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
        )
        self.assertIsNotNone(conv.pk)
        self.assertIsNone(conv.learner_last_read_at)
        self.assertIsNone(conv.instructor_last_read_at)

    def test_unique_constraint(self):
        Conversation.objects.create(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Conversation.objects.create(
                    learner=self.learner,
                    instructor=self.instructor,
                    course=self.course,
                )

    def test_cascade_delete_with_course(self):
        conv = Conversation.objects.create(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
        )
        Message.objects.create(conversation=conv, sender=self.learner, body='Hi')
        self.course.delete()
        self.assertFalse(Conversation.objects.filter(pk=conv.pk).exists())
        self.assertFalse(Message.objects.filter(conversation=conv).exists())

    def test_str(self):
        conv = Conversation.objects.create(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
        )
        self.assertIn(str(conv.pk), str(conv))


class MessageModelTest(TestCase):
    def setUp(self):
        self.learner = _make_user('learner2@test.com', 'learner', 'Carol')
        self.instructor = _make_user('instructor2@test.com', 'instructor', 'Dave')
        self.course = _make_course(self.instructor)
        self.course.instructors.add(self.instructor)
        self.conv = Conversation.objects.create(
            learner=self.learner,
            instructor=self.instructor,
            course=self.course,
        )

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

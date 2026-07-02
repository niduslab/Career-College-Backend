"""Tests for the generalized conversation types: co_instructor + institution_expert."""

from unittest.mock import patch

from django.test import TestCase

from authentication.models import InstructorProfile, PartnerInstitutionProfile, User
from courses.models import NidusCourse
from messaging.models import Conversation, Message
from messaging.services.messaging_service import (
    MessagingError,
    send_message,
    start_conversation,
)

_CType = Conversation.ConversationType


def _make_user(email, user_type, full_name='User'):
    return User.objects.create_user(
        email=email, password='pw12345!', user_type=user_type,
        full_name=full_name, is_email_verified=True,
    )


def _make_institution(email, name):
    user = _make_user(email, 'partner_institution', name)
    PartnerInstitutionProfile.objects.filter(user=user).update(
        institution_name=name, is_verified=True, is_active=True,
    )
    return user, user.partner_institution_profile


def _make_expert(email, institution, name='Expert'):
    user = _make_user(email, 'instructor', name)
    InstructorProfile.objects.filter(user=user).update(
        is_verified=True, affiliated_institution=institution,
        affiliation_status='active', onboarding_source='institution',
    )
    return user


@patch('messaging.services.messaging_service._push_ws_and_notify')
class CoInstructorConversationTest(TestCase):
    def setUp(self):
        self.owner = _make_user('owner@ci.com', 'instructor', 'Owner')
        self.peer = _make_user('peer@ci.com', 'instructor', 'Peer')
        self.course = NidusCourse.objects.create(
            title='CI Course', slug='ci-course', created_by=self.owner, status='published',
        )
        self.course.instructors.add(self.owner, self.peer)

    def _start(self):
        return start_conversation(
            conversation_type=_CType.CO_INSTRUCTOR,
            initiator=self.owner, target=self.peer, course=self.course,
            opener_body='Can you review section 3?',
        )

    def test_create_and_reply(self, _mock):
        conv, created = self._start()
        self.assertTrue(created)
        self.assertEqual(conv.conversation_type, _CType.CO_INSTRUCTOR)
        # Peer (the other co-instructor) can reply.
        msg = send_message(self.peer, conv.pk, 'Sure, on it.')
        self.assertEqual(msg.sender, self.peer)

    def test_blocked_if_target_not_on_course(self, _mock):
        outsider = _make_user('outsider@ci.com', 'instructor', 'Outsider')
        with self.assertRaises(MessagingError) as ctx:
            start_conversation(
                conversation_type=_CType.CO_INSTRUCTOR,
                initiator=self.owner, target=outsider, course=self.course,
                opener_body='Hi',
            )
        self.assertEqual(ctx.exception.http_status, 403)

    def test_removed_coinstructor_cannot_send(self, _mock):
        conv, _ = self._start()
        self.course.instructors.remove(self.peer)
        with self.assertRaises(MessagingError) as ctx:
            send_message(self.peer, conv.pk, 'Still here?')
        self.assertEqual(ctx.exception.http_status, 403)

    def test_idempotent(self, _mock):
        conv1, c1 = self._start()
        conv2, c2 = self._start()
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(conv1.pk, conv2.pk)


@patch('messaging.services.messaging_service._push_ws_and_notify')
class InstitutionExpertConversationTest(TestCase):
    def setUp(self):
        self.inst_user, self.institution = _make_institution('inst@ie.com', 'Acme Institute')
        self.expert = _make_expert('expert@ie.com', self.institution)

    def _start(self, course=None):
        return start_conversation(
            conversation_type=_CType.INSTITUTION_EXPERT,
            initiator=self.inst_user, target=self.expert, course=course,
            opener_body='Welcome aboard.',
        )

    def test_create_courseless_and_both_can_send(self, _mock):
        conv, created = self._start()
        self.assertTrue(created)
        self.assertIsNone(conv.course_id)
        # Expert (affiliate) can reply; institution can send too.
        self.assertEqual(send_message(self.expert, conv.pk, 'Thank you!').sender, self.expert)
        self.assertEqual(send_message(self.inst_user, conv.pk, 'Your first course is X.').sender, self.inst_user)

    def test_blocked_if_not_affiliate(self, _mock):
        stranger = _make_user('stranger@ie.com', 'instructor', 'Stranger')
        with self.assertRaises(MessagingError) as ctx:
            start_conversation(
                conversation_type=_CType.INSTITUTION_EXPERT,
                initiator=self.inst_user, target=stranger, course=None,
                opener_body='Hi',
            )
        self.assertEqual(ctx.exception.http_status, 403)

    def test_deactivated_expert_cannot_send_but_institution_can(self, _mock):
        conv, _ = self._start()
        InstructorProfile.objects.filter(user=self.expert).update(affiliation_status='removed')
        with self.assertRaises(MessagingError) as ctx:
            send_message(self.expert, conv.pk, 'Am I still here?')
        self.assertEqual(ctx.exception.http_status, 403)
        # Institution party is never gated by affiliation.
        self.assertEqual(send_message(self.inst_user, conv.pk, 'Update.').sender, self.inst_user)

    def test_courseless_pair_unique(self, _mock):
        conv1, c1 = self._start()
        conv2, c2 = self._start()
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(conv1.pk, conv2.pk)

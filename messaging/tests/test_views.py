"""Tests for messaging REST API views."""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import (
    InstructorProfile,
    PartnerInstitutionProfile,
    User,
)
from courses.models import Enrollment, NidusCourse
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


def _make_course(creator, slug='v-course'):
    return NidusCourse.objects.create(
        title='View Test Course',
        slug=slug,
        created_by=creator,
        status='published',
    )


def _enroll(learner, course, is_active=True):
    return Enrollment.objects.create(user=learner, course=course, is_active=is_active)


def _make_conversation(user_a, user_b, course, conversation_type='learner_instructor'):
    conv = Conversation.objects.create(
        conversation_type=conversation_type, course=course,
        participant_key=_pair_key(user_a.id, user_b.id),
    )
    ConversationParticipant.objects.create(conversation=conv, user=user_a)
    ConversationParticipant.objects.create(conversation=conv, user=user_b)
    return conv


def _auth(client, user):
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(token.access_token)}')


class ConversationListViewTest(APITestCase):
    def setUp(self):
        self.learner = _make_user('learner@v.com', 'learner', 'Alice')
        self.instructor = _make_user('instructor@v.com', 'instructor', 'Bob')
        self.course = _make_course(self.instructor)
        self.course.instructors.add(self.instructor)
        _enroll(self.learner, self.course)
        self.conv = _make_conversation(self.learner, self.instructor, self.course)
        self.url = reverse('messaging:conversation-list')

    def test_unauthenticated_returns_401(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_learner_sees_own_conversations(self):
        _auth(self.client, self.learner)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.data['success'])
        ids = [c['id'] for c in r.data['data']['results']]
        self.assertIn(self.conv.pk, ids)

    def test_instructor_sees_own_conversations(self):
        _auth(self.client, self.instructor)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        ids = [c['id'] for c in r.data['data']['results']]
        self.assertIn(self.conv.pk, ids)

    def test_other_learner_does_not_see_conversation(self):
        other = _make_user('other@v.com', 'learner', 'Carol')
        _auth(self.client, other)
        r = self.client.get(self.url)
        ids = [c['id'] for c in r.data['data']['results']]
        self.assertNotIn(self.conv.pk, ids)


class UnreadConversationCountViewTest(APITestCase):
    def setUp(self):
        self.learner = _make_user('lcount@v.com', 'learner', 'Una')
        self.instructor = _make_user('icount@v.com', 'instructor', 'Vic')
        self.course = _make_course(self.instructor, 'count-course')
        self.course.instructors.add(self.instructor)
        _enroll(self.learner, self.course)
        self.conv = _make_conversation(self.learner, self.instructor, self.course)
        self.url = reverse('messaging:unread-conversation-count')

    def test_unauthenticated_returns_401(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_zero_when_no_unread(self):
        _auth(self.client, self.learner)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.data['success'])
        self.assertEqual(r.data['data']['unread_conversations'], 0)

    def test_counts_unread_conversation(self):
        Message.objects.create(conversation=self.conv, sender=self.instructor, body='Hi')
        _auth(self.client, self.learner)
        r = self.client.get(self.url)
        self.assertEqual(r.data['data']['unread_conversations'], 1)

    def test_instructor_side_counted(self):
        Message.objects.create(conversation=self.conv, sender=self.learner, body='Question')
        _auth(self.client, self.instructor)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['data']['unread_conversations'], 1)

    def test_admin_forbidden(self):
        admin = _make_user('admincount@v.com', 'admin', 'Wally')
        _auth(self.client, admin)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)


class ConversationCreateViewTest(APITestCase):
    def setUp(self):
        self.learner = _make_user('lcreate@v.com', 'learner', 'Dave')
        self.instructor = _make_user('icreate@v.com', 'instructor', 'Eve')
        self.course = _make_course(self.instructor, 'create-course')
        self.course.instructors.add(self.instructor)
        _enroll(self.learner, self.course)
        self.url = reverse('messaging:conversation-create')

    @patch('messaging.services.messaging_service._push_ws_and_notify')
    def test_learner_creates_conversation(self, mock_dispatch):
        _auth(self.client, self.learner)
        r = self.client.post(self.url, {
            'course_id': self.course.pk,
            'instructor_id': self.instructor.pk,
            'body': 'Hello!',
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertTrue(r.data['success'])

    @patch('messaging.services.messaging_service._push_ws_and_notify')
    def test_duplicate_returns_200(self, mock_dispatch):
        _auth(self.client, self.learner)
        self.client.post(self.url, {
            'course_id': self.course.pk,
            'instructor_id': self.instructor.pk,
            'body': 'First message',
        })
        r = self.client.post(self.url, {
            'course_id': self.course.pk,
            'instructor_id': self.instructor.pk,
            'body': 'Second message',
        })
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_instructor_cannot_initiate(self):
        _auth(self.client, self.instructor)
        r = self.client.post(self.url, {
            'course_id': self.course.pk,
            'instructor_id': self.instructor.pk,
            'body': 'Hello learner',
        })
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_unenrolled_learner_gets_403(self):
        unenrolled = _make_user('unenrolled@v.com', 'learner', 'Frank')
        _auth(self.client, unenrolled)
        r = self.client.post(self.url, {
            'course_id': self.course.pk,
            'instructor_id': self.instructor.pk,
            'body': 'Sneaky',
        })
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_inactive_enrollment_gets_403(self):
        inactive = _make_user('inactive@v.com', 'learner', 'Grace')
        _enroll(inactive, self.course, is_active=False)
        _auth(self.client, inactive)
        r = self.client.post(self.url, {
            'course_id': self.course.pk,
            'instructor_id': self.instructor.pk,
            'body': 'Hello?',
        })
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_nonexistent_course_returns_404(self):
        _auth(self.client, self.learner)
        r = self.client.post(self.url, {
            'course_id': 99999,
            'instructor_id': self.instructor.pk,
            'body': 'Hello',
        })
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_blank_body_returns_400(self):
        _auth(self.client, self.learner)
        r = self.client.post(self.url, {
            'course_id': self.course.pk,
            'instructor_id': self.instructor.pk,
            'body': '   ',
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


class ConversationDetailViewTest(APITestCase):
    def setUp(self):
        self.learner = _make_user('ldetail@v.com', 'learner', 'Heidi')
        self.instructor = _make_user('idetail@v.com', 'instructor', 'Ivan')
        self.course = _make_course(self.instructor, 'detail-course')
        self.course.instructors.add(self.instructor)
        _enroll(self.learner, self.course)
        self.conv = _make_conversation(self.learner, self.instructor, self.course)
        Message.objects.create(conversation=self.conv, sender=self.learner, body='First msg')
        self.url = reverse('messaging:conversation-detail', args=[self.conv.pk])

    def test_learner_can_get_detail(self):
        _auth(self.client, self.learner)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('conversation', r.data['data'])
        self.assertIn('messages', r.data['data'])

    def test_instructor_can_get_detail(self):
        _auth(self.client, self.instructor)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_outsider_gets_404(self):
        outsider = _make_user('outsider@v.com', 'learner', 'Judy')
        _auth(self.client, outsider)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_soft_deleted_messages_excluded(self):
        msg = Message.objects.create(
            conversation=self.conv, sender=self.learner, body='Delete me'
        )
        msg.is_deleted = True
        msg.save()
        _auth(self.client, self.learner)
        r = self.client.get(self.url)
        bodies = [m['body'] for m in r.data['data']['messages']['results']]
        self.assertNotIn('Delete me', bodies)


class MarkConversationReadViewTest(APITestCase):
    def setUp(self):
        self.learner = _make_user('lmark@v.com', 'learner', 'Nina')
        self.instructor = _make_user('imark@v.com', 'instructor', 'Olaf')
        self.course = _make_course(self.instructor, 'mark-course')
        self.course.instructors.add(self.instructor)
        _enroll(self.learner, self.course)
        self.conv = _make_conversation(self.learner, self.instructor, self.course)
        Message.objects.create(conversation=self.conv, sender=self.instructor, body='Hi')
        self.url = reverse('messaging:mark-read', args=[self.conv.pk])

    def test_learner_mark_read_succeeds(self):
        _auth(self.client, self.learner)
        r = self.client.post(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.data['success'])
        cursor = ConversationParticipant.objects.get(
            conversation=self.conv, user=self.learner,
        ).last_read_at
        self.assertIsNotNone(cursor)

    def test_outsider_gets_404(self):
        outsider = _make_user('outsider3@v.com', 'learner', 'Paula')
        _auth(self.client, outsider)
        r = self.client.post(self.url)
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)


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


class CoInstructorCreateViewTest(APITestCase):
    """POST conversations/create/ with conversation_type=co_instructor."""

    def setUp(self):
        self.owner = _make_user('cvowner@v.com', 'instructor', 'Owner')
        self.peer = _make_user('cvpeer@v.com', 'instructor', 'Peer')
        self.course = _make_course(self.owner, 'coinstr-course')
        self.course.instructors.add(self.owner, self.peer)
        self.url = reverse('messaging:conversation-create')

    @patch('messaging.services.messaging_service._push_ws_and_notify')
    def test_instructor_creates_co_instructor_thread(self, _mock):
        _auth(self.client, self.owner)
        r = self.client.post(self.url, {
            'conversation_type': 'co_instructor',
            'course_id': self.course.pk,
            'peer_instructor_id': self.peer.pk,
            'body': 'Review section 3?',
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['data']['conversation_type'], 'co_instructor')

    def test_missing_peer_id_returns_400(self):
        _auth(self.client, self.owner)
        r = self.client.post(self.url, {
            'conversation_type': 'co_instructor',
            'course_id': self.course.pk,
            'body': 'No peer id',
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_peer_not_on_course_returns_403(self):
        stranger = _make_user('cvstranger@v.com', 'instructor', 'Stranger')
        _auth(self.client, self.owner)
        r = self.client.post(self.url, {
            'conversation_type': 'co_instructor',
            'course_id': self.course.pk,
            'peer_instructor_id': stranger.pk,
            'body': 'Hi',
        })
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_peer_not_found_returns_404(self):
        _auth(self.client, self.owner)
        r = self.client.post(self.url, {
            'conversation_type': 'co_instructor',
            'course_id': self.course.pk,
            'peer_instructor_id': 99999,
            'body': 'Hi',
        })
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)


class InstitutionExpertCreateViewTest(APITestCase):
    """POST conversations/create/ with conversation_type=institution_expert."""

    def setUp(self):
        self.inst_user, self.institution = _make_institution('cvinst@v.com', 'Acme Institute')
        self.expert = _make_expert('cvexpert@v.com', self.institution)
        self.url = reverse('messaging:conversation-create')

    @patch('messaging.services.messaging_service._push_ws_and_notify')
    def test_institution_creates_courseless_thread(self, _mock):
        _auth(self.client, self.inst_user)
        r = self.client.post(self.url, {
            'conversation_type': 'institution_expert',
            'expert_user_id': self.expert.pk,
            'body': 'Welcome aboard.',
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['data']['conversation_type'], 'institution_expert')
        self.assertIsNone(r.data['data']['course_id'])

    def test_non_institution_caller_forbidden(self):
        # An instructor cannot open an institution_expert thread.
        _auth(self.client, self.expert)
        r = self.client.post(self.url, {
            'conversation_type': 'institution_expert',
            'expert_user_id': self.expert.pk,
            'body': 'Let me in',
        })
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_expert_not_found_returns_404(self):
        _auth(self.client, self.inst_user)
        r = self.client.post(self.url, {
            'conversation_type': 'institution_expert',
            'expert_user_id': 99999,
            'body': 'Hi',
        })
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_affiliate_target_forbidden(self):
        other_inst = _make_user('cvother@v.com', 'partner_institution', 'Other')
        PartnerInstitutionProfile.objects.filter(user=other_inst).update(
            institution_name='Other', is_verified=True, is_active=True,
        )
        stranger = _make_user('cvstrangerx@v.com', 'instructor', 'Stranger')
        _auth(self.client, self.inst_user)
        r = self.client.post(self.url, {
            'conversation_type': 'institution_expert',
            'expert_user_id': stranger.pk,
            'body': 'Hi',
        })
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)


class MessagingPermissionTest(APITestCase):
    """The broadened _MESSAGING_USERS: partner institutions allowed, admins not."""

    def setUp(self):
        self.inst_user, _ = _make_institution('perminst@v.com', 'Perm Institute')
        self.url = reverse('messaging:conversation-list')

    def test_partner_institution_can_list(self):
        _auth(self.client, self.inst_user)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.data['success'])

    def test_admin_forbidden(self):
        admin = _make_user('permadmin@v.com', 'admin', 'Admin')
        _auth(self.client, admin)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

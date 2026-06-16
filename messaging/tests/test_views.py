"""Tests for messaging REST API views."""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import User
from courses.models import Enrollment, NidusCourse
from messaging.models import Conversation, Message


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
        self.conv = Conversation.objects.create(
            learner=self.learner, instructor=self.instructor, course=self.course
        )
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
        self.conv = Conversation.objects.create(
            learner=self.learner, instructor=self.instructor, course=self.course
        )
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


class SendMessageViewTest(APITestCase):
    def setUp(self):
        self.learner = _make_user('lsend@v.com', 'learner', 'Karl')
        self.instructor = _make_user('isend@v.com', 'instructor', 'Lara')
        self.course = _make_course(self.instructor, 'send-course')
        self.course.instructors.add(self.instructor)
        _enroll(self.learner, self.course)
        self.conv = Conversation.objects.create(
            learner=self.learner, instructor=self.instructor, course=self.course
        )
        self.url = reverse('messaging:send-message', args=[self.conv.pk])

    @patch('messaging.services.messaging_service._push_ws_and_notify')
    def test_learner_can_send(self, mock_dispatch):
        _auth(self.client, self.learner)
        r = self.client.post(self.url, {'body': 'A question'})
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertTrue(r.data['success'])
        self.assertTrue(r.data['data']['is_own'])

    @patch('messaging.services.messaging_service._push_ws_and_notify')
    def test_instructor_can_reply(self, mock_dispatch):
        _auth(self.client, self.instructor)
        r = self.client.post(self.url, {'body': 'An answer'})
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)

    def test_outsider_gets_404(self):
        outsider = _make_user('outsider2@v.com', 'learner', 'Mike')
        _auth(self.client, outsider)
        r = self.client.post(self.url, {'body': 'Sneaky send'})
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_blank_body_returns_400(self):
        _auth(self.client, self.learner)
        r = self.client.post(self.url, {'body': ''})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unenrolled_learner_send_blocked(self):
        Enrollment.objects.filter(user=self.learner, course=self.course).update(is_active=False)
        _auth(self.client, self.learner)
        r = self.client.post(self.url, {'body': 'Still here?'})
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)


class MarkConversationReadViewTest(APITestCase):
    def setUp(self):
        self.learner = _make_user('lmark@v.com', 'learner', 'Nina')
        self.instructor = _make_user('imark@v.com', 'instructor', 'Olaf')
        self.course = _make_course(self.instructor, 'mark-course')
        self.course.instructors.add(self.instructor)
        _enroll(self.learner, self.course)
        self.conv = Conversation.objects.create(
            learner=self.learner, instructor=self.instructor, course=self.course
        )
        Message.objects.create(conversation=self.conv, sender=self.instructor, body='Hi')
        self.url = reverse('messaging:mark-read', args=[self.conv.pk])

    def test_learner_mark_read_succeeds(self):
        _auth(self.client, self.learner)
        r = self.client.post(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.data['success'])
        self.assertIsNotNone(
            Conversation.objects.get(pk=self.conv.pk).learner_last_read_at
        )

    def test_outsider_gets_404(self):
        outsider = _make_user('outsider3@v.com', 'learner', 'Paula')
        _auth(self.client, outsider)
        r = self.client.post(self.url)
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

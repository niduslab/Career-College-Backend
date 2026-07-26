"""Covers the project-wide envelope exception handler.

Every DRF-raised error must carry `success` + `message`; `detail` stays for
clients written against DRF's default shape.
"""

from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.all_views.discussion_views import DiscussionUpvoteThrottle
from courses.all_models.discussion_models import CourseQuestion
from courses.models import (
    CourseSection,
    Enrollment,
    Lecture,
    NidusCourse,
    SectionContent,
)


class EnvelopeExceptionHandlerTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='exc_instructor@example.com', password='pw12345!',
            full_name='Exc Instructor', user_type='instructor', is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='exc_learner@example.com', password='pw12345!',
            full_name='Exc Learner', user_type='learner', is_email_verified=True,
        )
        cls.unverified = User.objects.create_user(
            email='exc_unverified@example.com', password='pw12345!',
            full_name='Exc Unverified', user_type='learner', is_email_verified=False,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Envelope Course',
            slug='envelope-course',
            description='Course used by exception-handler tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
            price='0.00',
        )
        cls.course.instructors.add(cls.instructor)
        cls.section = CourseSection.objects.create(course=cls.course, title='Intro')
        cls.lecture = Lecture.objects.create(
            section=cls.section, title='L1', lecture_type=Lecture.LectureType.VIDEO,
        )
        SectionContent.objects.create(
            section=cls.section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=cls.lecture.pk,
            position=1,
        )
        Enrollment.objects.create(user=cls.learner, course=cls.course, is_active=True)
        cls.question = CourseQuestion.objects.create(
            course=cls.course, author=cls.learner, title='Q', body='B',
        )

    def setUp(self):
        cache.clear()

    def _upvote_url(self):
        return reverse('courses:question-upvote', kwargs={'question_id': self.question.pk})

    def test_throttled_response_uses_envelope(self):
        self.client.force_authenticate(user=self.learner)
        url = self._upvote_url()

        with patch.object(DiscussionUpvoteThrottle, 'rate', '1/min'):
            self.assertEqual(self.client.post(url).status_code, status.HTTP_200_OK)
            resp = self.client.post(url)

        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertFalse(resp.data['success'])
        self.assertIn('throttled', resp.data['message'].lower())
        # `detail` preserved for clients written against DRF's default shape.
        self.assertIn('detail', resp.data)

    def test_unauthenticated_response_uses_envelope(self):
        resp = self.client.post(self._upvote_url())

        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(resp.data['success'])
        self.assertTrue(resp.data['message'])

    def test_permission_denied_response_uses_envelope(self):
        # IsEmailVerified rejects this caller -> DRF raises PermissionDenied.
        self.client.force_authenticate(user=self.unverified)
        resp = self.client.post(self._upvote_url())

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(resp.data['success'])
        self.assertTrue(resp.data['message'])

    def test_method_not_allowed_uses_envelope(self):
        self.client.force_authenticate(user=self.learner)
        resp = self.client.get(self._upvote_url())

        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertFalse(resp.data['success'])
        self.assertTrue(resp.data['message'])

    def test_view_built_envelopes_are_untouched(self):
        """A view returning its own envelope must not be rewritten."""
        self.client.force_authenticate(user=self.learner)
        resp = self.client.get(
            reverse('courses:course-question-detail', kwargs={'question_id': 999999})
        )

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp.data['message'], 'Question not found.')
        self.assertNotIn('detail', resp.data)

    def test_success_responses_are_untouched(self):
        self.client.force_authenticate(user=self.learner)
        resp = self.client.get(
            reverse('courses:course-question-list', kwargs={'slug': self.course.slug})
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['success'])
        self.assertIn('results', resp.data['data'])

"""AI outline-preview endpoint and its AI-service client.

Nothing here touches the network: view tests patch `generate_course_outline`
where the view imported it, and client tests patch `requests.post`.
"""

from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.all_views.ai_views import AIOutlineThrottle
from courses.services.ai_outline_service import (
    REQUEST_TIMEOUT,
    AIOutlineError,
    generate_course_outline,
)

URL = reverse('courses:ai-outline-preview')

_SERVICE_RESULT = {
    'modules': [
        {
            'title': 'Foundations of Machine Learning',
            'summary': 'Core vocabulary and the supervised/unsupervised split.',
            'learning_outcomes': ['Explain supervised vs unsupervised learning'],
            'topics': ['What is ML?', 'Types of learning'],
            'estimated_duration_minutes': 90,
        },
    ],
    'outline_text': 'Module 1: Foundations of Machine Learning (90 min)',
}


def _body(**overrides):
    payload = {
        'title': 'Introduction to Machine Learning',
        'description': 'A hands-on course covering supervised and unsupervised learning.',
        'audience': 'Undergraduate CS students',
    }
    payload.update(overrides)
    return payload


class CourseOutlinePreviewAPITests(APITestCase):
    """POST /api/v1/courses/ai/outline-preview/"""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='ai_instructor@example.com', password='pw12345!',
            full_name='AI Instructor', user_type='instructor', is_email_verified=True,
        )
        cls.institution = User.objects.create_user(
            email='ai_institution@example.com', password='pw12345!',
            full_name='AI Institution', user_type='partner_institution',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='ai_learner@example.com', password='pw12345!',
            full_name='AI Learner', user_type='learner', is_email_verified=True,
        )
        cls.unverified = User.objects.create_user(
            email='ai_unverified@example.com', password='pw12345!',
            full_name='AI Unverified', user_type='instructor', is_email_verified=False,
        )

    def setUp(self):
        # Throttle counters live in the default cache and would otherwise leak
        # between tests in the same process.
        cache.clear()

    def auth(self, user):
        self.client.force_authenticate(user=user)

    # ---------------------------------------------------------------- success

    @patch('courses.all_views.ai_views.generate_course_outline', return_value=_SERVICE_RESULT)
    def test_instructor_can_generate_an_outline(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, _body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['success'])
        self.assertEqual(resp.data['data'], _SERVICE_RESULT)
        mock_generate.assert_called_once()

    @patch('courses.all_views.ai_views.generate_course_outline', return_value=_SERVICE_RESULT)
    def test_partner_institution_can_generate_an_outline(self, mock_generate):
        """IsCourseCreator, not IsInstructorUser — institutions author courses too."""
        self.auth(self.institution)
        resp = self.client.post(URL, _body(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('courses.all_views.ai_views.generate_course_outline', return_value=_SERVICE_RESULT)
    def test_optional_fields_are_defaulted_not_required(self, mock_generate):
        self.auth(self.instructor)
        self.client.post(URL, _body(), format='json')

        kwargs = mock_generate.call_args.kwargs
        self.assertEqual(kwargs['language'], 'English')
        self.assertEqual(kwargs['prerequisites'], '')
        self.assertEqual(kwargs['level'], '')
        self.assertIsNone(kwargs['duration_minutes'])
        self.assertEqual(kwargs['category'], '')
        self.assertEqual(kwargs['extra_instructions'], '')

    @patch('courses.all_views.ai_views.generate_course_outline', return_value=_SERVICE_RESULT)
    def test_every_supplied_field_is_forwarded(self, mock_generate):
        self.auth(self.instructor)
        self.client.post(URL, _body(
            prerequisites='Python basics',
            level='intermediate',
            language='Bangla',
            duration_minutes=600,
            category='Data Science',
            extra_instructions='Focus on hands-on labs.',
        ), format='json')

        kwargs = mock_generate.call_args.kwargs
        self.assertEqual(kwargs['level'], 'intermediate')
        self.assertEqual(kwargs['language'], 'Bangla')
        self.assertEqual(kwargs['duration_minutes'], 600)
        self.assertEqual(kwargs['category'], 'Data Science')
        self.assertEqual(kwargs['extra_instructions'], 'Focus on hands-on labs.')

    @patch('courses.all_views.ai_views.generate_course_outline', return_value=_SERVICE_RESULT)
    def test_nothing_is_persisted(self, mock_generate):
        """The endpoint is a suggestion generator — it must not create a course."""
        from courses.models import NidusCourse

        self.auth(self.instructor)
        before = NidusCourse.objects.count()
        self.client.post(URL, _body(), format='json')
        self.assertEqual(NidusCourse.objects.count(), before)

    # ------------------------------------------------------------- validation

    @patch('courses.all_views.ai_views.generate_course_outline')
    def test_missing_title_returns_400(self, mock_generate):
        self.auth(self.instructor)
        payload = _body()
        del payload['title']
        resp = self.client.post(URL, payload, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('title', resp.data['errors'])
        # A malformed request must never reach the paid service.
        mock_generate.assert_not_called()

    @patch('courses.all_views.ai_views.generate_course_outline')
    def test_missing_description_and_audience_return_400(self, mock_generate):
        self.auth(self.instructor)
        for field in ('description', 'audience'):
            payload = _body()
            del payload[field]
            resp = self.client.post(URL, payload, format='json')
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(field, resp.data['errors'])
        mock_generate.assert_not_called()

    @patch('courses.all_views.ai_views.generate_course_outline')
    def test_unknown_level_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, _body(level='wizard'), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('level', resp.data['errors'])
        mock_generate.assert_not_called()

    @patch('courses.all_views.ai_views.generate_course_outline')
    def test_negative_duration_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, _body(duration_minutes=-1), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('duration_minutes', resp.data['errors'])

    # ----------------------------------------------------------- permissions

    def test_unauthenticated_returns_401(self):
        resp = self.client.post(URL, _body(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_learner_returns_403(self):
        """No resource id in the URL, so permission denial is 403, never 404."""
        self.auth(self.learner)
        resp = self.client.post(URL, _body(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unverified_email_returns_403(self):
        self.auth(self.unverified)
        resp = self.client.post(URL, _body(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ------------------------------------------------------------- throttling

    @patch('courses.all_views.ai_views.generate_course_outline', return_value=_SERVICE_RESULT)
    def test_generation_is_throttled(self, mock_generate):
        self.auth(self.instructor)
        # `rate` is read at class-definition time, so override_settings can't
        # reach it — patch the parsed limit on the throttle class instead.
        with patch.object(AIOutlineThrottle, 'rate', '2/min'):
            self.assertEqual(
                self.client.post(URL, _body(), format='json').status_code,
                status.HTTP_200_OK,
            )
            self.assertEqual(
                self.client.post(URL, _body(), format='json').status_code,
                status.HTTP_200_OK,
            )
            self.assertEqual(
                self.client.post(URL, _body(), format='json').status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
            )

    # --------------------------------------------------------- service errors

    @patch(
        'courses.all_views.ai_views.generate_course_outline',
        side_effect=AIOutlineError(
            'Outline generation is temporarily unavailable. Please try again.', 503,
        ),
    )
    def test_service_down_returns_503(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, _body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(resp.data['success'])
        self.assertIn('temporarily unavailable', resp.data['message'])

    @patch(
        'courses.all_views.ai_views.generate_course_outline',
        side_effect=RuntimeError('kaboom'),
    )
    def test_unexpected_error_returns_500_without_leaking_details(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, _body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotIn('kaboom', str(resp.data))


@override_settings(
    AI_SERVICES_BASE_URL='http://ai-services:8001',
    AI_SERVICES_KEY='shared-secret',
)
class AIOutlineServiceClientTests(APITestCase):
    """courses/services/ai_outline_service.py — pure HTTP transport."""

    def _call(self, **overrides):
        kwargs = {
            'title': 'Introduction to Machine Learning',
            'description': 'A hands-on course.',
            'audience': 'Undergraduate CS students',
        }
        kwargs.update(overrides)
        return generate_course_outline(**kwargs)

    @patch('courses.services.ai_outline_service.requests.post')
    def test_posts_to_the_configured_service_with_the_shared_key(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self.assertEqual(self._call(), _SERVICE_RESULT)

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], 'http://ai-services:8001/v1/course-outline/')
        self.assertEqual(kwargs['headers']['X-Service-Key'], 'shared-secret')
        self.assertEqual(kwargs['timeout'], REQUEST_TIMEOUT)
        # Pinned deliberately: the read leg must stay ABOVE the AI service's own
        # LLM timeout (40s) or Django gives up first and every slow generation
        # looks like a 503.
        self.assertEqual(kwargs['timeout'], (5, 45))

    @patch('courses.services.ai_outline_service.requests.post')
    def test_blank_optional_values_are_sent_as_null(self, mock_post):
        """The AI service treats these as Optional; '' would fail its schema."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self._call(level='', category='')

        payload = mock_post.call_args.kwargs['json']
        self.assertIsNone(payload['level'])
        self.assertIsNone(payload['category'])

    @patch(
        'courses.services.ai_outline_service.requests.post',
        side_effect=requests.ConnectionError('refused'),
    )
    def test_unreachable_service_raises_503(self, mock_post):
        with self.assertRaises(AIOutlineError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

    @patch(
        'courses.services.ai_outline_service.requests.post',
        side_effect=requests.Timeout('too slow'),
    )
    def test_timeout_raises_503(self, mock_post):
        with self.assertRaises(AIOutlineError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

    @patch('courses.services.ai_outline_service.requests.post')
    def test_non_200_raises_503_without_leaking_upstream_detail(self, mock_post):
        mock_post.return_value.status_code = 401
        mock_post.return_value.text = 'Invalid service key.'

        with self.assertRaises(AIOutlineError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)
        self.assertNotIn('service key', ctx.exception.message)

    @patch('courses.services.ai_outline_service.requests.post')
    def test_malformed_json_raises_503(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.side_effect = ValueError('not json')

        with self.assertRaises(AIOutlineError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

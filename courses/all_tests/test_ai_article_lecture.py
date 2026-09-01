"""AI article-lecture preview endpoint and its AI-service client.

Nothing here touches the network: view tests patch `generate_article_lecture`
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
from courses.all_views.ai_views import AIArticleThrottle
from courses.services.ai_article_service import (
    REQUEST_TIMEOUT,
    AIArticleError,
    generate_article_lecture,
)

URL = reverse('courses:ai-article-lecture-preview')

_SERVICE_RESULT = {
    'summary': 'What a gradient measures, in one paragraph.',
    'sections': [
        {
            'heading': 'Slope in one dimension',
            'paragraphs': ['A derivative is the slope of a curve at a point.'],
            'bullets': [],
            'code': None,
        },
    ],
    'takeaways_heading': 'Key takeaways',
    'key_takeaways': ['A gradient generalises slope to many dimensions'],
    'article_html': (
        '<p>What a gradient measures, in one paragraph.</p>'
        '<h2>Slope in one dimension</h2>'
        '<p>A derivative is the slope of a curve at a point.</p>'
        '<h2>Key takeaways</h2>'
        '<ul><li><p>A gradient generalises slope to many dimensions</p></li></ul>'
    ),
    'word_count': 24,
    'estimated_reading_minutes': 1,
}


def _body(**overrides):
    payload = {'lecture_title': 'What a gradient actually measures'}
    payload.update(overrides)
    return payload


class ArticleLecturePreviewAPITests(APITestCase):
    """POST /api/v1/courses/ai/article-lecture-preview/"""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='ai_article_instructor@example.com', password='pw12345!',
            full_name='AI Instructor', user_type='instructor', is_email_verified=True,
        )
        cls.institution = User.objects.create_user(
            email='ai_article_institution@example.com', password='pw12345!',
            full_name='AI Institution', user_type='partner_institution',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='ai_article_learner@example.com', password='pw12345!',
            full_name='AI Learner', user_type='learner', is_email_verified=True,
        )
        cls.unverified = User.objects.create_user(
            email='ai_article_unverified@example.com', password='pw12345!',
            full_name='AI Unverified', user_type='instructor', is_email_verified=False,
        )

    def setUp(self):
        # Throttle counters live in the default cache and would otherwise leak
        # between tests in the same process.
        cache.clear()

    def auth(self, user):
        self.client.force_authenticate(user=user)

    # ---------------------------------------------------------------- success

    @patch('courses.all_views.ai_views.generate_article_lecture', return_value=_SERVICE_RESULT)
    def test_instructor_can_generate_an_article(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, _body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['success'])
        self.assertEqual(resp.data['data'], _SERVICE_RESULT)
        mock_generate.assert_called_once()

    @patch('courses.all_views.ai_views.generate_article_lecture', return_value=_SERVICE_RESULT)
    def test_partner_institution_can_generate_an_article(self, mock_generate):
        """IsCourseCreator, not IsInstructorUser — institutions author too."""
        self.auth(self.institution)
        resp = self.client.post(URL, _body(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('courses.all_views.ai_views.generate_article_lecture', return_value=_SERVICE_RESULT)
    def test_optional_fields_are_defaulted_not_required(self, mock_generate):
        self.auth(self.instructor)
        self.client.post(URL, _body(), format='json')

        kwargs = mock_generate.call_args.kwargs
        self.assertEqual(kwargs['course_title'], '')
        self.assertEqual(kwargs['section_title'], '')
        self.assertEqual(kwargs['description'], '')
        self.assertEqual(kwargs['key_points'], [])
        self.assertEqual(kwargs['audience'], '')
        self.assertEqual(kwargs['level'], '')
        self.assertEqual(kwargs['language'], 'English')
        self.assertIsNone(kwargs['target_duration_minutes'])
        self.assertFalse(kwargs['include_code_examples'])
        self.assertEqual(kwargs['extra_instructions'], '')

    @patch('courses.all_views.ai_views.generate_article_lecture', return_value=_SERVICE_RESULT)
    def test_every_supplied_field_is_forwarded(self, mock_generate):
        self.auth(self.instructor)
        self.client.post(URL, _body(
            course_title='Introduction to Machine Learning',
            section_title='Foundations',
            description='Build intuition before the maths.',
            key_points=['Slope in many dimensions', 'Why it points uphill'],
            audience='Undergraduate CS students',
            level='beginner',
            language='Bangla',
            target_duration_minutes=6,
            include_code_examples=True,
            extra_instructions='Use a worked example.',
        ), format='json')

        kwargs = mock_generate.call_args.kwargs
        self.assertEqual(kwargs['course_title'], 'Introduction to Machine Learning')
        self.assertEqual(kwargs['section_title'], 'Foundations')
        self.assertEqual(kwargs['key_points'], ['Slope in many dimensions', 'Why it points uphill'])
        self.assertEqual(kwargs['level'], 'beginner')
        self.assertEqual(kwargs['language'], 'Bangla')
        self.assertEqual(kwargs['target_duration_minutes'], 6)
        self.assertTrue(kwargs['include_code_examples'])
        self.assertEqual(kwargs['extra_instructions'], 'Use a worked example.')

    @patch('courses.all_views.ai_views.generate_article_lecture', return_value=_SERVICE_RESULT)
    def test_nothing_is_persisted(self, mock_generate):
        """The endpoint is a suggestion generator — it must not create or fill
        in a lecture. The instructor saves the draft themselves."""
        from courses.models import Lecture

        self.auth(self.instructor)
        before = Lecture.objects.count()
        self.client.post(URL, _body(), format='json')
        self.assertEqual(Lecture.objects.count(), before)

    # ------------------------------------------------------------- validation

    @patch('courses.all_views.ai_views.generate_article_lecture')
    def test_missing_lecture_title_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, {}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('lecture_title', resp.data['errors'])
        # A malformed request must never reach the paid service.
        mock_generate.assert_not_called()

    @patch('courses.all_views.ai_views.generate_article_lecture')
    def test_blank_lecture_title_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, _body(lecture_title=''), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        mock_generate.assert_not_called()

    @patch('courses.all_views.ai_views.generate_article_lecture')
    def test_unknown_level_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, _body(level='wizard'), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('level', resp.data['errors'])

    @patch('courses.all_views.ai_views.generate_article_lecture')
    def test_out_of_range_duration_returns_400(self, mock_generate):
        self.auth(self.instructor)
        for value in (-1, 500):
            resp = self.client.post(
                URL, _body(target_duration_minutes=value), format='json',
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn('target_duration_minutes', resp.data['errors'])

    @patch('courses.all_views.ai_views.generate_article_lecture')
    def test_too_many_key_points_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(
            URL, _body(key_points=[f'Point {i}' for i in range(13)]), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('key_points', resp.data['errors'])

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

    @patch('courses.all_views.ai_views.generate_article_lecture', return_value=_SERVICE_RESULT)
    def test_generation_is_throttled(self, mock_generate):
        self.auth(self.instructor)
        # `rate` is read at class-definition time, so override_settings can't
        # reach it — patch the parsed limit on the throttle class instead.
        with patch.object(AIArticleThrottle, 'rate', '2/min'):
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

    @patch('courses.all_views.ai_views.generate_course_outline', return_value={'modules': []})
    @patch('courses.all_views.ai_views.generate_article_lecture', return_value=_SERVICE_RESULT)
    def test_article_and_outline_throttles_are_separate_counters(
        self, mock_article, mock_outline,
    ):
        """Outlining once per course must not spend a writing session's budget."""
        self.auth(self.instructor)
        with patch.object(AIArticleThrottle, 'rate', '1/min'):
            self.assertEqual(
                self.client.post(URL, _body(), format='json').status_code,
                status.HTTP_200_OK,
            )
            self.assertEqual(
                self.client.post(URL, _body(), format='json').status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
            outline_resp = self.client.post(
                reverse('courses:ai-outline-preview'),
                {'title': 'T', 'description': 'D', 'audience': 'A'},
                format='json',
            )
        self.assertEqual(outline_resp.status_code, status.HTTP_200_OK)

    # --------------------------------------------------------- service errors

    @patch(
        'courses.all_views.ai_views.generate_article_lecture',
        side_effect=AIArticleError(
            'Article generation is temporarily unavailable. Please try again.', 503,
        ),
    )
    def test_service_down_returns_503(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, _body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(resp.data['success'])
        self.assertIn('temporarily unavailable', resp.data['message'])

    @patch(
        'courses.all_views.ai_views.generate_article_lecture',
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
class AIArticleServiceClientTests(APITestCase):
    """courses/services/ai_article_service.py — pure HTTP transport."""

    def _call(self, **overrides):
        kwargs = {'lecture_title': 'What a gradient actually measures'}
        kwargs.update(overrides)
        return generate_article_lecture(**kwargs)

    @patch('courses.services.ai_article_service.requests.post')
    def test_posts_to_the_configured_service_with_the_shared_key(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self.assertEqual(self._call(), _SERVICE_RESULT)

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], 'http://ai-services:8001/v1/article-lecture/')
        self.assertEqual(kwargs['headers']['X-Service-Key'], 'shared-secret')
        self.assertEqual(kwargs['timeout'], REQUEST_TIMEOUT)
        # Pinned deliberately: the read leg must stay ABOVE the AI service's own
        # LLM timeout (40s) or Django gives up first and every slow generation
        # looks like a 503.
        self.assertEqual(kwargs['timeout'], (5, 45))

    @patch('courses.services.ai_article_service.requests.post')
    def test_blank_level_is_sent_as_null(self, mock_post):
        """The AI service treats `level` as Optional; '' would fail its schema."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self._call(level='')

        self.assertIsNone(mock_post.call_args.kwargs['json']['level'])

    @patch('courses.services.ai_article_service.requests.post')
    def test_key_points_default_to_an_empty_list_not_null(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self._call(key_points=None)

        self.assertEqual(mock_post.call_args.kwargs['json']['key_points'], [])

    @patch(
        'courses.services.ai_article_service.requests.post',
        side_effect=requests.ConnectionError('refused'),
    )
    def test_unreachable_service_raises_503(self, mock_post):
        with self.assertRaises(AIArticleError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

    @patch(
        'courses.services.ai_article_service.requests.post',
        side_effect=requests.Timeout('too slow'),
    )
    def test_timeout_raises_503(self, mock_post):
        with self.assertRaises(AIArticleError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

    @patch('courses.services.ai_article_service.requests.post')
    def test_non_200_raises_503_without_leaking_upstream_detail(self, mock_post):
        mock_post.return_value.status_code = 401
        mock_post.return_value.text = 'Invalid service key.'

        with self.assertRaises(AIArticleError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)
        self.assertNotIn('service key', ctx.exception.message)

    @patch('courses.services.ai_article_service.requests.post')
    def test_malformed_json_raises_503(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.side_effect = ValueError('not json')

        with self.assertRaises(AIArticleError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

"""AI coding-exercise preview endpoint and its AI-service client.

Nothing here touches the network or Docker: view tests patch
`generate_coding_exercise` where the view imported it, client tests patch
`requests.post`, and the verification runs are the frontend's job.
"""

from unittest.mock import patch

import requests
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.all_views.ai_views import AICodingThrottle
from courses.models import (
    CodingExercise,
    CourseSection,
    Lecture,
    NidusCourse,
    SectionContent,
)
from courses.services.ai_coding_service import (
    REQUEST_TIMEOUT,
    AICodingError,
    generate_coding_exercise,
)

URL = reverse('courses:ai-coding-exercise-preview')

_SERVICE_RESULT = {
    'description': 'Write solve(values) returning the sum of the list.',
    'starter_code': 'def solve(values):\n    # TODO\n    pass\n',
    'solution_code': 'def solve(values):\n    return sum(values)\n',
    'evaluation_script': (
        'import unittest\n'
        'from exercise import solve\n\n'
        'class SolveTests(unittest.TestCase):\n'
        '    def test_simple(self):\n'
        '        self.assertEqual(solve([1, 2]), 3)\n'
    ),
    'test_names': ['Sums a short list', 'Handles an empty list', 'Handles negatives'],
    'language': 'python',
    'difficulty': 'core',
    'grounded': True,
}


class CodingExercisePreviewAPITests(APITestCase):
    """POST /api/v1/courses/ai/coding-exercise-preview/"""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='ai_code_instructor@example.com', password='pw12345!',
            full_name='AI Code Instructor', user_type='instructor',
            is_email_verified=True,
        )
        cls.institution = User.objects.create_user(
            email='ai_code_institution@example.com', password='pw12345!',
            full_name='AI Code Institution', user_type='partner_institution',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='ai_code_learner@example.com', password='pw12345!',
            full_name='AI Code Learner', user_type='learner', is_email_verified=True,
        )
        cls.unverified = User.objects.create_user(
            email='ai_code_unverified@example.com', password='pw12345!',
            full_name='AI Code Unverified', user_type='instructor',
            is_email_verified=False,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Introduction to Python',
            slug='ai-coding-course',
            description='A course used by the AI coding tests.',
            audiences='Undergraduate CS students',
            language='English',
            level=NidusCourse.CourseLevel.BEGINNER,
        )
        cls.course.instructors.add(cls.instructor)
        cls.section = CourseSection.objects.create(
            course=cls.course, title='Lists', position=1,
            description='Where list iteration is introduced.',
        )
        cls.exercise = CodingExercise.objects.create(
            section=cls.section,
            title='Sum a list',
            description='Practise iterating a list.',
            language=CodingExercise.Language.PYTHON,
            time_limit_ms=3000,
        )
        lecture = Lecture.objects.create(
            section=cls.section, title='Iterating a list',
            lecture_type=Lecture.LectureType.ARTICLE,
            article_content='<p>A for loop visits each element in turn.</p>',
        )
        SectionContent.objects.create(
            section=cls.section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk,
            position=1,
        )

        cls.other_course = NidusCourse.objects.create(
            created_by=cls.institution, title='Other Course',
            slug='ai-coding-other', description='Owned by the institution.',
        )
        cls.other_section = CourseSection.objects.create(
            course=cls.other_course, title='Other Section', position=1,
        )
        cls.other_exercise = CodingExercise.objects.create(
            section=cls.other_section, title='Other Exercise',
            language=CodingExercise.Language.JAVA,
        )

    def setUp(self):
        cache.clear()

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def body(self, **overrides):
        payload = {'exercise_id': self.exercise.pk}
        payload.update(overrides)
        return payload

    # ---------------------------------------------------------------- success

    @patch('courses.all_views.ai_views.generate_coding_exercise', return_value=_SERVICE_RESULT)
    def test_instructor_can_generate_an_exercise(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, self.body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['success'])
        self.assertIn('evaluation_script', resp.data['data'])
        self.assertIn('solution_code', resp.data['data'])
        mock_generate.assert_called_once()

    @patch('courses.all_views.ai_views.generate_coding_exercise', return_value=_SERVICE_RESULT)
    def test_partner_institution_can_generate_an_exercise(self, mock_generate):
        self.auth(self.institution)
        resp = self.client.post(
            URL, {'exercise_id': self.other_exercise.pk}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('courses.all_views.ai_views.generate_coding_exercise', return_value=_SERVICE_RESULT)
    def test_the_exercise_supplies_the_context(self, mock_generate):
        self.auth(self.instructor)
        self.client.post(URL, self.body(), format='json')

        kwargs = mock_generate.call_args.kwargs
        self.assertEqual(kwargs['exercise_title'], 'Sum a list')
        self.assertEqual(kwargs['course_title'], 'Introduction to Python')
        self.assertEqual(kwargs['section_title'], 'Lists')
        self.assertEqual(kwargs['audience'], 'Undergraduate CS students')
        self.assertEqual(kwargs['level'], 'beginner')
        self.assertEqual(kwargs['time_limit_ms'], 3000)
        self.assertIn('for loop visits each element', kwargs['source_material'])

    @patch('courses.all_views.ai_views.generate_coding_exercise', return_value=_SERVICE_RESULT)
    def test_language_comes_from_the_exercise_not_the_request(self, mock_generate):
        """It decides the evaluation-script contract, so a client-supplied one
        must be ignored rather than trusted."""
        self.auth(self.institution)
        resp = self.client.post(
            URL, {'exercise_id': self.other_exercise.pk, 'language': 'python'},
            format='json',
        )

        self.assertEqual(mock_generate.call_args.kwargs['language'], 'java')
        self.assertEqual(resp.data['data']['language'], 'java')

    @patch('courses.all_views.ai_views.generate_coding_exercise', return_value=_SERVICE_RESULT)
    def test_the_knobs_are_defaulted_not_required(self, mock_generate):
        self.auth(self.instructor)
        self.client.post(URL, self.body(), format='json')

        kwargs = mock_generate.call_args.kwargs
        self.assertEqual(kwargs['difficulty'], 'core')
        self.assertEqual(kwargs['topic_hint'], '')
        self.assertEqual(kwargs['avoid_titles'], [])
        self.assertEqual(kwargs['extra_instructions'], '')

    @patch('courses.all_views.ai_views.generate_coding_exercise', return_value=_SERVICE_RESULT)
    def test_every_supplied_knob_is_forwarded(self, mock_generate):
        self.auth(self.instructor)
        self.client.post(URL, self.body(
            difficulty='challenge',
            topic_hint='binary search',
            avoid_titles=['Reverse a string'],
            extra_instructions='Use recursion.',
        ), format='json')

        kwargs = mock_generate.call_args.kwargs
        self.assertEqual(kwargs['difficulty'], 'challenge')
        self.assertEqual(kwargs['topic_hint'], 'binary search')
        self.assertEqual(kwargs['avoid_titles'], ['Reverse a string'])
        self.assertEqual(kwargs['extra_instructions'], 'Use recursion.')

    @patch('courses.all_views.ai_views.generate_coding_exercise', return_value=_SERVICE_RESULT)
    def test_nothing_is_persisted(self, mock_generate):
        """The draft reaches the exercise only through the normal PATCH."""
        self.auth(self.instructor)
        self.client.post(URL, self.body(), format='json')

        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.solution_code, '')
        self.assertEqual(self.exercise.evaluation_script, '')
        self.assertEqual(self.exercise.starter_code, '')

    @patch('courses.all_views.ai_views.generate_coding_exercise', return_value=_SERVICE_RESULT)
    def test_grounded_is_decided_here_not_upstream(self, mock_generate):
        self.auth(self.institution)
        resp = self.client.post(
            URL, {'exercise_id': self.other_exercise.pk}, format='json',
        )

        # The canned reply says True; that section has no article lectures.
        self.assertFalse(resp.data['data']['grounded'])
        self.assertTrue(_SERVICE_RESULT['grounded'], 'the canned reply must not be mutated')

    # -------------------------------------------------------------- validation

    @patch('courses.all_views.ai_views.generate_coding_exercise')
    def test_missing_exercise_id_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, {}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('exercise_id', resp.data['errors'])
        mock_generate.assert_not_called()

    @patch('courses.all_views.ai_views.generate_coding_exercise')
    def test_unknown_difficulty_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, self.body(difficulty='impossible'), format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('difficulty', resp.data['errors'])

    @patch('courses.all_views.ai_views.generate_coding_exercise')
    def test_too_many_avoid_titles_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(
            URL, self.body(avoid_titles=[f'T{i}' for i in range(11)]), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------- permissions

    def test_unauthenticated_returns_401(self):
        resp = self.client.post(URL, self.body(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_learner_returns_403(self):
        self.auth(self.learner)
        resp = self.client.post(URL, self.body(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unverified_email_returns_403(self):
        self.auth(self.unverified)
        resp = self.client.post(URL, self.body(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @patch('courses.all_views.ai_views.generate_coding_exercise')
    def test_an_exercise_the_caller_does_not_own_returns_404(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(
            URL, {'exercise_id': self.other_exercise.pk}, format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        mock_generate.assert_not_called()

    @patch('courses.all_views.ai_views.generate_coding_exercise')
    def test_a_missing_exercise_returns_404(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, {'exercise_id': 999999}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # -------------------------------------------------------------- throttling

    @patch('courses.all_views.ai_views.generate_coding_exercise', return_value=_SERVICE_RESULT)
    def test_generation_is_throttled(self, mock_generate):
        self.auth(self.instructor)
        with patch.object(AICodingThrottle, 'rate', '2/min'):
            for expected in (
                status.HTTP_200_OK,
                status.HTTP_200_OK,
                status.HTTP_429_TOO_MANY_REQUESTS,
            ):
                resp = self.client.post(URL, self.body(), format='json')
                self.assertEqual(resp.status_code, expected)

    @patch('courses.all_views.ai_views.generate_course_outline', return_value={'modules': []})
    @patch('courses.all_views.ai_views.generate_coding_exercise', return_value=_SERVICE_RESULT)
    def test_coding_and_outline_throttles_are_separate_counters(
        self, mock_coding, mock_outline,
    ):
        self.auth(self.instructor)
        with patch.object(AICodingThrottle, 'rate', '1/min'):
            self.assertEqual(
                self.client.post(URL, self.body(), format='json').status_code,
                status.HTTP_200_OK,
            )
            self.assertEqual(
                self.client.post(URL, self.body(), format='json').status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
            outline_resp = self.client.post(
                reverse('courses:ai-outline-preview'),
                {'title': 'T', 'description': 'D', 'audience': 'A'},
                format='json',
            )
        self.assertEqual(outline_resp.status_code, status.HTTP_200_OK)

    # ---------------------------------------------------------- service errors

    @patch(
        'courses.all_views.ai_views.generate_coding_exercise',
        side_effect=AICodingError(
            'Exercise generation is temporarily unavailable. Please try again.', 503,
        ),
    )
    def test_service_down_returns_503(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, self.body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn('temporarily unavailable', resp.data['message'])

    @patch(
        'courses.all_views.ai_views.generate_coding_exercise',
        side_effect=RuntimeError('kaboom'),
    )
    def test_unexpected_error_returns_500_without_leaking_details(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, self.body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotIn('kaboom', str(resp.data))

    # ------------------------------------------------------------- regression

    def test_a_verification_run_writes_nothing_to_the_exercise(self):
        """The review modal runs an unsaved draft through the existing run
        endpoint. Both overrides must be used and neither stored."""
        from courses.services.code_runner import ScriptTestResult

        self.auth(self.instructor)
        captured = {}

        def _fake_run(self_runner, code, evaluation_script, time_limit_ms, language):
            captured['code'] = code
            captured['script'] = evaluation_script
            return [ScriptTestResult('evaluate.T.test_a', 'passed', '', '', 1)]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission', new=_fake_run,
        ):
            resp = self.client.post(
                reverse('courses:coding-exercise-instructor-run', args=[self.exercise.pk]),
                {
                    'code': _SERVICE_RESULT['solution_code'],
                    'evaluation_script': _SERVICE_RESULT['evaluation_script'],
                },
                format='json',
            )

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn('return sum(values)', captured['code'])
        self.assertIn('from exercise import solve', captured['script'])

        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.solution_code, '')
        self.assertEqual(self.exercise.evaluation_script, '')

    def test_the_learner_payload_still_hides_the_solution_and_script(self):
        from courses.all_serializers.learner_serializers import (
            LearnerCodingExerciseDetailSerializer,
        )

        self.exercise.solution_code = 'def solve(v): return sum(v)'
        self.exercise.evaluation_script = 'import unittest'
        self.exercise.save(update_fields=['solution_code', 'evaluation_script'])

        declared = set(LearnerCodingExerciseDetailSerializer().get_fields())
        self.assertNotIn('solution_code', declared)
        self.assertNotIn('evaluation_script', declared)


@override_settings(
    AI_SERVICES_BASE_URL='http://ai-services:8001',
    AI_SERVICES_KEY='shared-secret',
)
class AICodingServiceClientTests(APITestCase):
    """courses/services/ai_coding_service.py — pure HTTP transport."""

    def _call(self, **overrides):
        kwargs = {'exercise_title': 'Sum a list', 'language': 'python'}
        kwargs.update(overrides)
        return generate_coding_exercise(**kwargs)

    @patch('courses.services.ai_coding_service.requests.post')
    def test_posts_to_the_configured_service_with_the_shared_key(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self.assertEqual(self._call(), _SERVICE_RESULT)

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], 'http://ai-services:8001/v1/coding-exercise/')
        self.assertEqual(kwargs['headers']['X-Service-Key'], 'shared-secret')
        self.assertEqual(kwargs['timeout'], REQUEST_TIMEOUT)
        self.assertEqual(kwargs['timeout'], (5, 45))

    @patch('courses.services.ai_coding_service.requests.post')
    def test_blank_level_is_sent_as_null(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self._call(level='')

        self.assertIsNone(mock_post.call_args.kwargs['json']['level'])

    @patch('courses.services.ai_coding_service.requests.post')
    def test_avoid_titles_default_to_an_empty_list(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self._call(avoid_titles=None)

        self.assertEqual(mock_post.call_args.kwargs['json']['avoid_titles'], [])

    @patch(
        'courses.services.ai_coding_service.requests.post',
        side_effect=requests.ConnectionError('refused'),
    )
    def test_unreachable_service_raises_503(self, mock_post):
        with self.assertRaises(AICodingError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

    @patch(
        'courses.services.ai_coding_service.requests.post',
        side_effect=requests.Timeout('too slow'),
    )
    def test_timeout_raises_503(self, mock_post):
        with self.assertRaises(AICodingError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

    @patch('courses.services.ai_coding_service.requests.post')
    def test_non_200_raises_503_without_leaking_upstream_detail(self, mock_post):
        mock_post.return_value.status_code = 401
        mock_post.return_value.text = 'Invalid service key.'

        with self.assertRaises(AICodingError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)
        self.assertNotIn('service key', ctx.exception.message)

    @patch('courses.services.ai_coding_service.requests.post')
    def test_malformed_json_raises_503(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.side_effect = ValueError('not json')

        with self.assertRaises(AICodingError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

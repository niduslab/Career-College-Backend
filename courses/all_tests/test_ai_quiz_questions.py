"""AI quiz-question preview endpoint, its AI-service client, and the grounding
material Django assembles for it.

Nothing here touches the network: view tests patch `generate_quiz_questions`
where the view imported it, and client tests patch `requests.post`.
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
from courses.all_views.ai_views import AIQuizThrottle
from courses.models import (
    CourseSection,
    Lecture,
    NidusCourse,
    Quiz,
    QuizAnswer,
    QuizQuestion,
    SectionContent,
)
from courses.services.ai_quiz_service import (
    REQUEST_TIMEOUT,
    AIQuizError,
    generate_quiz_questions,
)
from courses.services.quiz_service import (
    build_quiz_source_material,
    collect_avoid_questions,
)
from courses.services.section_context_service import MAX_SOURCE_CHARS, html_to_text

URL = reverse('courses:ai-quiz-questions-preview')

_SERVICE_RESULT = {
    'questions': [
        {
            'question_text': 'What does a gradient point towards?',
            'options': [
                {'answer_text': 'Steepest increase', 'is_correct': True},
                {'answer_text': 'Steepest decrease', 'is_correct': False},
            ],
            'explanation': 'It is the direction of steepest increase.',
            'difficulty': 'understanding',
        },
    ],
    'grounded': True,
    'requested_count': 5,
}


def _article(section, title, body, position):
    """An article lecture plus its curriculum row.

    `chk_lecture_payload_by_type` requires article lectures to carry content and
    video lectures to carry none, so neither can be created carelessly.
    """
    lecture = Lecture.objects.create(
        section=section,
        title=title,
        lecture_type=Lecture.LectureType.ARTICLE,
        article_content=body,
    )
    SectionContent.objects.create(
        section=section,
        item_type=SectionContent.ItemType.LECTURE,
        content_type=ContentType.objects.get_for_model(Lecture),
        object_id=lecture.pk,
        position=position,
    )
    return lecture


class QuizQuestionsPreviewAPITests(APITestCase):
    """POST /api/v1/courses/ai/quiz-questions-preview/"""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='ai_quiz_instructor@example.com', password='pw12345!',
            full_name='AI Quiz Instructor', user_type='instructor',
            is_email_verified=True,
        )
        cls.institution = User.objects.create_user(
            email='ai_quiz_institution@example.com', password='pw12345!',
            full_name='AI Quiz Institution', user_type='partner_institution',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='ai_quiz_learner@example.com', password='pw12345!',
            full_name='AI Quiz Learner', user_type='learner', is_email_verified=True,
        )
        cls.unverified = User.objects.create_user(
            email='ai_quiz_unverified@example.com', password='pw12345!',
            full_name='AI Quiz Unverified', user_type='instructor',
            is_email_verified=False,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Introduction to Machine Learning',
            slug='ai-quiz-course',
            description='A course used by the AI quiz tests.',
            audiences='Undergraduate CS students',
            language='English',
            level=NidusCourse.CourseLevel.BEGINNER,
        )
        cls.course.instructors.add(cls.instructor)
        cls.section = CourseSection.objects.create(
            course=cls.course, title='Foundations', position=1,
            description='Where gradients are introduced.',
        )
        cls.quiz = Quiz.objects.create(
            section=cls.section,
            title='Gradients checkpoint',
            description='Check that gradients landed.',
        )
        _article(
            cls.section, 'What a gradient measures',
            '<p>A gradient points in the direction of steepest increase.</p>', 1,
        )

        # A second course, owned by the institution, so ownership is exercised
        # from both sides of `course_owner_q`.
        cls.other_course = NidusCourse.objects.create(
            created_by=cls.institution,
            title='Other Course', slug='ai-quiz-other-course',
            description='Owned by the institution.',
        )
        cls.other_section = CourseSection.objects.create(
            course=cls.other_course, title='Other Section', position=1,
        )
        cls.other_quiz = Quiz.objects.create(
            section=cls.other_section, title='Other Quiz',
        )

    def setUp(self):
        # Throttle counters live in the default cache and would otherwise leak
        # between tests in the same process.
        cache.clear()

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def body(self, **overrides):
        payload = {'quiz_id': self.quiz.pk}
        payload.update(overrides)
        return payload

    # ---------------------------------------------------------------- success

    @patch('courses.all_views.ai_views.generate_quiz_questions', return_value=_SERVICE_RESULT)
    def test_instructor_can_generate_questions(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, self.body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['success'])
        self.assertEqual(len(resp.data['data']['questions']), 1)
        self.assertEqual(resp.data['data']['requested_count'], 5)
        mock_generate.assert_called_once()

    @patch('courses.all_views.ai_views.generate_quiz_questions', return_value=_SERVICE_RESULT)
    def test_partner_institution_can_generate_questions(self, mock_generate):
        """IsCourseCreator, not IsInstructorUser — institutions author too."""
        self.auth(self.institution)
        resp = self.client.post(
            URL, {'quiz_id': self.other_quiz.pk}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @patch('courses.all_views.ai_views.generate_quiz_questions', return_value=_SERVICE_RESULT)
    def test_the_quiz_supplies_the_context_the_browser_never_sends(self, mock_generate):
        self.auth(self.instructor)
        self.client.post(URL, self.body(), format='json')

        kwargs = mock_generate.call_args.kwargs
        self.assertEqual(kwargs['quiz_title'], 'Gradients checkpoint')
        self.assertEqual(kwargs['course_title'], 'Introduction to Machine Learning')
        self.assertEqual(kwargs['section_title'], 'Foundations')
        self.assertEqual(kwargs['quiz_description'], 'Check that gradients landed.')
        self.assertEqual(kwargs['audience'], 'Undergraduate CS students')
        self.assertEqual(kwargs['level'], 'beginner')
        self.assertEqual(kwargs['language'], 'English')
        self.assertIn('steepest increase', kwargs['source_material'])

    @patch('courses.all_views.ai_views.generate_quiz_questions', return_value=_SERVICE_RESULT)
    def test_the_knobs_are_defaulted_not_required(self, mock_generate):
        self.auth(self.instructor)
        self.client.post(URL, self.body(), format='json')

        kwargs = mock_generate.call_args.kwargs
        self.assertEqual(kwargs['question_count'], 5)
        self.assertEqual(kwargs['options_per_question'], 4)
        self.assertEqual(kwargs['difficulty'], 'understanding')
        self.assertEqual(kwargs['topics'], [])
        self.assertEqual(kwargs['extra_instructions'], '')

    @patch('courses.all_views.ai_views.generate_quiz_questions', return_value=_SERVICE_RESULT)
    def test_every_supplied_knob_is_forwarded(self, mock_generate):
        self.auth(self.instructor)
        self.client.post(URL, self.body(
            question_count=8,
            options_per_question=3,
            difficulty='application',
            topics=['Gradients'],
            extra_instructions='Focus on the maths.',
        ), format='json')

        kwargs = mock_generate.call_args.kwargs
        self.assertEqual(kwargs['question_count'], 8)
        self.assertEqual(kwargs['options_per_question'], 3)
        self.assertEqual(kwargs['difficulty'], 'application')
        self.assertEqual(kwargs['topics'], ['Gradients'])
        self.assertEqual(kwargs['extra_instructions'], 'Focus on the maths.')

    @patch('courses.all_views.ai_views.generate_quiz_questions', return_value=_SERVICE_RESULT)
    def test_existing_questions_are_sent_as_the_avoid_list(self, mock_generate):
        """The instructor never has to supply this — a regenerate that repeats
        what the quiz already asks is the common failure."""
        QuizQuestion.objects.create(
            quiz=self.quiz, question_text='Already asked?', position=1,
        )
        self.auth(self.instructor)
        self.client.post(URL, self.body(avoid_questions=['On screen but unsaved?']),
                         format='json')

        avoid = mock_generate.call_args.kwargs['avoid_questions']
        self.assertIn('Already asked?', avoid)
        self.assertIn('On screen but unsaved?', avoid)

    @patch('courses.all_views.ai_views.generate_quiz_questions', return_value=_SERVICE_RESULT)
    def test_nothing_is_persisted(self, mock_generate):
        """Reading for context is not persisting a suggestion."""
        self.auth(self.instructor)
        before = QuizQuestion.objects.count(), QuizAnswer.objects.count()

        self.client.post(URL, self.body(), format='json')

        self.assertEqual((QuizQuestion.objects.count(), QuizAnswer.objects.count()), before)

    @patch('courses.all_views.ai_views.generate_quiz_questions', return_value=_SERVICE_RESULT)
    def test_grounded_is_decided_here_not_upstream(self, mock_generate):
        """The AI service only knows whether it was given material; this side
        knows whether the section actually has written lectures."""
        self.auth(self.institution)
        resp = self.client.post(URL, {'quiz_id': self.other_quiz.pk}, format='json')

        # The canned service reply says True; the section has no article text.
        self.assertFalse(resp.data['data']['grounded'])
        self.assertTrue(_SERVICE_RESULT['grounded'], 'the canned reply must not be mutated')

    # -------------------------------------------------------------- validation

    @patch('courses.all_views.ai_views.generate_quiz_questions')
    def test_missing_quiz_id_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, {}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('quiz_id', resp.data['errors'])
        # A malformed request must never reach the paid service.
        mock_generate.assert_not_called()

    @patch('courses.all_views.ai_views.generate_quiz_questions')
    def test_out_of_range_counts_return_400(self, mock_generate):
        self.auth(self.instructor)
        for field, value in (
            ('question_count', 0), ('question_count', 16),
            ('options_per_question', 1), ('options_per_question', 6),
        ):
            resp = self.client.post(URL, self.body(**{field: value}), format='json')
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, field)
            self.assertIn(field, resp.data['errors'])
        mock_generate.assert_not_called()

    @patch('courses.all_views.ai_views.generate_quiz_questions')
    def test_unknown_difficulty_returns_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, self.body(difficulty='impossible'), format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('difficulty', resp.data['errors'])

    @patch('courses.all_views.ai_views.generate_quiz_questions')
    def test_too_many_avoid_questions_return_400(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(
            URL, self.body(avoid_questions=[f'Q{i}?' for i in range(31)]), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('avoid_questions', resp.data['errors'])

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

    @patch('courses.all_views.ai_views.generate_quiz_questions')
    def test_a_quiz_the_caller_does_not_own_returns_404(self, mock_generate):
        """Unlike its two sibling AI endpoints this one takes a resource id, so
        denial is 404 — the project's identifier-type rule, not an
        inconsistency. A 403 would confirm the quiz exists."""
        self.auth(self.instructor)
        resp = self.client.post(URL, {'quiz_id': self.other_quiz.pk}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        mock_generate.assert_not_called()

    @patch('courses.all_views.ai_views.generate_quiz_questions')
    def test_a_missing_quiz_returns_404(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, {'quiz_id': 999999}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # -------------------------------------------------------------- throttling

    @patch('courses.all_views.ai_views.generate_quiz_questions', return_value=_SERVICE_RESULT)
    def test_generation_is_throttled(self, mock_generate):
        self.auth(self.instructor)
        # `rate` is read at class-definition time, so override_settings can't
        # reach it — patch the parsed limit on the throttle class instead.
        with patch.object(AIQuizThrottle, 'rate', '2/min'):
            for expected in (
                status.HTTP_200_OK,
                status.HTTP_200_OK,
                status.HTTP_429_TOO_MANY_REQUESTS,
            ):
                resp = self.client.post(URL, self.body(), format='json')
                self.assertEqual(resp.status_code, expected)

    @patch('courses.all_views.ai_views.generate_course_outline', return_value={'modules': []})
    @patch('courses.all_views.ai_views.generate_quiz_questions', return_value=_SERVICE_RESULT)
    def test_quiz_and_outline_throttles_are_separate_counters(
        self, mock_quiz, mock_outline,
    ):
        """Generating questions per quiz must not spend the outline budget."""
        self.auth(self.instructor)
        with patch.object(AIQuizThrottle, 'rate', '1/min'):
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
        'courses.all_views.ai_views.generate_quiz_questions',
        side_effect=AIQuizError(
            'Question generation is temporarily unavailable. Please try again.', 503,
        ),
    )
    def test_service_down_returns_503(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, self.body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(resp.data['success'])
        self.assertIn('temporarily unavailable', resp.data['message'])

    @patch(
        'courses.all_views.ai_views.generate_quiz_questions',
        side_effect=RuntimeError('kaboom'),
    )
    def test_unexpected_error_returns_500_without_leaking_details(self, mock_generate):
        self.auth(self.instructor)
        resp = self.client.post(URL, self.body(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotIn('kaboom', str(resp.data))


class QuizSourceMaterialTests(APITestCase):
    """courses/services/quiz_service.py — what the model is grounded in."""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='quiz_source_instructor@example.com', password='pw12345!',
            full_name='Source Instructor', user_type='instructor',
            is_email_verified=True,
        )
        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor, title='Source Course',
            slug='quiz-source-course', description='Grounding tests.',
        )
        cls.section = CourseSection.objects.create(
            course=cls.course, title='Foundations', position=1,
            description='Where gradients are introduced.',
        )
        cls.quiz = Quiz.objects.create(section=cls.section, title='Checkpoint')

    def test_article_bodies_are_included_as_plain_text(self):
        _article(
            self.section, 'What a gradient measures',
            '<p>A gradient points <strong>uphill</strong>.</p>'
            '<ul><li><p>It is a vector</p></li></ul>',
            1,
        )

        material, grounded = build_quiz_source_material(self.quiz)

        self.assertTrue(grounded)
        self.assertIn('A gradient points uphill.', material)
        self.assertIn('It is a vector', material)
        self.assertNotIn('<p>', material)
        self.assertNotIn('<strong>', material)

    def test_the_section_description_and_titles_frame_the_material(self):
        _article(self.section, 'What a gradient measures', '<p>Body.</p>', 1)

        material, _ = build_quiz_source_material(self.quiz)

        self.assertIn('Module: Foundations', material)
        self.assertIn('Where gradients are introduced.', material)
        self.assertIn('Lesson: What a gradient measures', material)

    def test_lectures_come_in_curriculum_order_not_creation_order(self):
        _article(self.section, 'Second lesson', '<p>Second body.</p>', 2)
        _article(self.section, 'First lesson', '<p>First body.</p>', 1)

        material, _ = build_quiz_source_material(self.quiz)

        self.assertLess(
            material.index('First lesson'), material.index('Second lesson'),
        )

    def test_a_video_lecture_contributes_its_title_only(self):
        """A title is weak grounding, but it is honest about what the module
        covers — there is no text to offer."""
        lecture = Lecture.objects.create(
            section=self.section, title='Watch the descent animation',
            lecture_type=Lecture.LectureType.VIDEO,
        )
        SectionContent.objects.create(
            section=self.section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk,
            position=1,
        )

        material, grounded = build_quiz_source_material(self.quiz)

        self.assertIn('Lesson: Watch the descent animation', material)
        self.assertFalse(grounded)

    def test_a_section_with_no_written_content_is_not_grounded(self):
        material, grounded = build_quiz_source_material(self.quiz)

        self.assertFalse(grounded)
        self.assertIn('Module: Foundations', material)

    def test_the_cap_truncates_rather_than_sending_everything(self):
        """The cap is prompt input and spend at once: Groq charges prompt tokens
        and the output cap against the same per-minute allowance."""
        body = '<p>' + ('gradient descent step. ' * 800) + '</p>'
        _article(self.section, 'A very long lesson', body, 1)

        material, grounded = build_quiz_source_material(self.quiz)

        self.assertTrue(grounded)
        self.assertLessEqual(len(material), MAX_SOURCE_CHARS + 40)
        self.assertIn('[Material truncated.]', material)

    def test_avoid_questions_come_from_the_quiz_in_position_order(self):
        QuizQuestion.objects.create(quiz=self.quiz, question_text='Second?', position=2)
        QuizQuestion.objects.create(quiz=self.quiz, question_text='First?', position=1)

        self.assertEqual(collect_avoid_questions(self.quiz), ['First?', 'Second?'])

    def test_avoid_questions_merge_the_callers_unsaved_drafts(self):
        QuizQuestion.objects.create(quiz=self.quiz, question_text='Saved?', position=1)

        avoid = collect_avoid_questions(self.quiz, ['On screen?'])

        self.assertEqual(avoid, ['Saved?', 'On screen?'])

    def test_avoid_questions_are_deduped_and_capped(self):
        QuizQuestion.objects.create(quiz=self.quiz, question_text='Same?', position=1)

        avoid = collect_avoid_questions(self.quiz, ['  same?  ', 'Other?'])

        self.assertEqual(avoid, ['Same?', 'Other?'])
        self.assertLessEqual(
            len(collect_avoid_questions(self.quiz, [f'Q{i}?' for i in range(60)])), 30,
        )

    def test_html_to_text_keeps_paragraphs_apart_and_decodes_entities(self):
        text = html_to_text('<p>First &amp; foremost.</p><p>Second.</p>')

        self.assertEqual(text, 'First & foremost.\n\nSecond.')


@override_settings(
    AI_SERVICES_BASE_URL='http://ai-services:8001',
    AI_SERVICES_KEY='shared-secret',
)
class AIQuizServiceClientTests(APITestCase):
    """courses/services/ai_quiz_service.py — pure HTTP transport."""

    def _call(self, **overrides):
        kwargs = {'quiz_title': 'Gradients checkpoint'}
        kwargs.update(overrides)
        return generate_quiz_questions(**kwargs)

    @patch('courses.services.ai_quiz_service.requests.post')
    def test_posts_to_the_configured_service_with_the_shared_key(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self.assertEqual(self._call(), _SERVICE_RESULT)

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], 'http://ai-services:8001/v1/quiz-questions/')
        self.assertEqual(kwargs['headers']['X-Service-Key'], 'shared-secret')
        self.assertEqual(kwargs['timeout'], REQUEST_TIMEOUT)
        # Pinned deliberately: the read leg must stay ABOVE the AI service's own
        # LLM timeout (40s) or Django gives up first and every slow generation
        # looks like a 503.
        self.assertEqual(kwargs['timeout'], (5, 45))

    @patch('courses.services.ai_quiz_service.requests.post')
    def test_blank_level_is_sent_as_null(self, mock_post):
        """The AI service treats `level` as Optional; '' would fail its schema."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self._call(level='')

        self.assertIsNone(mock_post.call_args.kwargs['json']['level'])

    @patch('courses.services.ai_quiz_service.requests.post')
    def test_lists_default_to_empty_not_null(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = _SERVICE_RESULT

        self._call(topics=None, avoid_questions=None)

        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['topics'], [])
        self.assertEqual(payload['avoid_questions'], [])

    @patch(
        'courses.services.ai_quiz_service.requests.post',
        side_effect=requests.ConnectionError('refused'),
    )
    def test_unreachable_service_raises_503(self, mock_post):
        with self.assertRaises(AIQuizError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

    @patch(
        'courses.services.ai_quiz_service.requests.post',
        side_effect=requests.Timeout('too slow'),
    )
    def test_timeout_raises_503(self, mock_post):
        with self.assertRaises(AIQuizError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

    @patch('courses.services.ai_quiz_service.requests.post')
    def test_non_200_raises_503_without_leaking_upstream_detail(self, mock_post):
        mock_post.return_value.status_code = 401
        mock_post.return_value.text = 'Invalid service key.'

        with self.assertRaises(AIQuizError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)
        self.assertNotIn('service key', ctx.exception.message)

    @patch('courses.services.ai_quiz_service.requests.post')
    def test_malformed_json_raises_503(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.side_effect = ValueError('not json')

        with self.assertRaises(AIQuizError) as ctx:
            self._call()
        self.assertEqual(ctx.exception.http_status, 503)

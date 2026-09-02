"""Transactional bulk creation of quiz questions.

`POST quizzes/<id>/questions/bulk/` — the endpoint that turns N + N*M writes into
one. Nothing here is AI-specific; the same body is what a hand-authored paste
would send.

Also guards the two invariants generated content earns no exemption from: a quiz
built this way must still satisfy course-submission validation, and its answers
must still hide `is_correct` from learners.
"""

from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import (
    CourseSection,
    NidusCourse,
    Quiz,
    QuizAnswer,
    QuizQuestion,
)


def _question(text='What does a gradient point towards?', options=3, correct=0):
    return {
        'question_text': text,
        'options': [
            {'answer_text': f'{text} option {i + 1}', 'is_correct': i == correct}
            for i in range(options)
        ],
    }


class QuizQuestionBulkCreateAPITests(APITestCase):
    """POST /api/v1/courses/quizzes/{quiz_id}/questions/bulk/"""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='bulk_instructor@example.com', password='pw12345!',
            full_name='Bulk Instructor', user_type='instructor', is_email_verified=True,
        )
        cls.outsider = User.objects.create_user(
            email='bulk_outsider@example.com', password='pw12345!',
            full_name='Bulk Outsider', user_type='instructor', is_email_verified=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Bulk Course',
            slug='bulk-course',
            description='A course used by the bulk quiz-question tests.',
        )
        cls.course.instructors.add(cls.instructor)
        cls.section = CourseSection.objects.create(
            course=cls.course, title='Foundations', position=1,
        )
        cls.quiz = Quiz.objects.create(section=cls.section, title='Gradients checkpoint')

    def setUp(self):
        self.url = reverse('courses:quiz-question-bulk-create', args=[self.quiz.pk])
        self.client.force_authenticate(user=self.instructor)

    def post(self, questions, url=None):
        return self.client.post(url or self.url, {'questions': questions}, format='json')

    # -------------------------------------------------------------- happy path

    def test_questions_and_answers_are_written_in_one_call(self):
        resp = self.post([_question('First?'), _question('Second?', options=4)])

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['success'])
        self.assertEqual(QuizQuestion.objects.filter(quiz=self.quiz).count(), 2)
        self.assertEqual(
            QuizAnswer.objects.filter(question__quiz=self.quiz).count(), 7,
        )

    def test_the_response_carries_the_answers_so_no_refetch_is_needed(self):
        resp = self.post([_question('First?', options=3)])

        created = resp.data['data'][0]
        self.assertEqual(created['question_text'], 'First?')
        self.assertEqual(len(created['answers']), 3)
        self.assertEqual(
            sum(1 for a in created['answers'] if a['is_correct']), 1,
        )

    def test_positions_append_after_the_questions_already_there(self):
        QuizQuestion.objects.create(quiz=self.quiz, question_text='Existing', position=7)

        resp = self.post([_question('First?'), _question('Second?')])

        self.assertEqual([q['position'] for q in resp.data['data']], [8, 9])

    def test_positions_start_at_one_for_an_empty_quiz(self):
        resp = self.post([_question('First?')])
        self.assertEqual(resp.data['data'][0]['position'], 1)

    def test_the_quiz_records_who_edited_it(self):
        """`QuizQuestion` and `QuizAnswer` carry no author fields by design —
        they are sub-rows of an already-authored parent."""
        self.post([_question('First?')])

        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.last_edited_by, self.instructor)

    def test_existing_questions_are_left_alone(self):
        existing = QuizQuestion.objects.create(
            quiz=self.quiz, question_text='Existing', position=1,
        )

        self.post([_question('First?')])

        existing.refresh_from_db()
        self.assertEqual(existing.question_text, 'Existing')
        self.assertEqual(existing.position, 1)

    # -------------------------------------------------------------- validation

    def test_two_correct_options_are_rejected(self):
        question = _question(options=3)
        question['options'][1]['is_correct'] = True

        resp = self.post([question])

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(QuizQuestion.objects.filter(quiz=self.quiz).count(), 0)

    def test_no_correct_option_is_rejected(self):
        question = _question(options=3)
        for option in question['options']:
            option['is_correct'] = False

        resp = self.post([question])

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(QuizQuestion.objects.filter(quiz=self.quiz).count(), 0)

    def test_duplicate_option_text_is_rejected(self):
        question = _question(options=3)
        question['options'][2]['answer_text'] = question['options'][0]['answer_text'].upper()

        resp = self.post([question])

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_too_few_or_too_many_options_are_rejected(self):
        for count in (1, 6):
            resp = self.post([_question(options=count)])
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, count)

    def test_an_over_long_option_is_rejected(self):
        """`QuizAnswer.answer_text` is CharField(max_length=500)."""
        question = _question(options=2)
        question['options'][0]['answer_text'] = 'x' * 501

        resp = self.post([question])

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_blank_question_text_is_rejected(self):
        resp = self.post([_question(text='   ')])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_empty_batch_is_rejected(self):
        resp = self.post([])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_over_the_per_call_cap_is_rejected(self):
        resp = self.post([_question(text=f'Q{i}?') for i in range(21)])

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(QuizQuestion.objects.filter(quiz=self.quiz).count(), 0)

    def test_one_bad_question_rejects_the_whole_batch(self):
        bad = _question(text='Bad?', options=3)
        bad['options'][1]['is_correct'] = True

        resp = self.post([_question('Good?'), bad, _question('Also good?')])

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(QuizQuestion.objects.filter(quiz=self.quiz).count(), 0)

    # ------------------------------------------------------------- atomicity

    def test_a_failure_mid_write_leaves_nothing_behind(self):
        """The whole batch rolls back, so a retry starts from a clean quiz
        rather than from half of one."""
        with patch(
            'courses.services.quiz_service.QuizAnswer.objects.bulk_create',
            side_effect=IntegrityError('boom'),
        ):
            resp = self.post([_question('First?'), _question('Second?')])

        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(resp.data['success'])
        self.assertEqual(QuizQuestion.objects.filter(quiz=self.quiz).count(), 0)
        self.assertEqual(QuizAnswer.objects.filter(question__quiz=self.quiz).count(), 0)

    # ------------------------------------------------------------ permissions

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        resp = self.post([_question()])
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_quiz_the_caller_does_not_own_returns_404(self):
        """Numeric id in the URL, so denial is 404 — a 403 would confirm the
        quiz exists."""
        self.client.force_authenticate(user=self.outsider)
        resp = self.post([_question()])

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(QuizQuestion.objects.filter(quiz=self.quiz).count(), 0)

    def test_a_missing_quiz_returns_404(self):
        url = reverse('courses:quiz-question-bulk-create', args=[999999])
        resp = self.post([_question()], url=url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_locked_course_returns_422(self):
        self.course.status = NidusCourse.CourseStatus.UNDER_REVIEW
        self.course.save(update_fields=['status'])
        self.addCleanup(self._restore_course_status)

        resp = self.post([_question()])

        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(QuizQuestion.objects.filter(quiz=self.quiz).count(), 0)

    def _restore_course_status(self):
        self.course.status = NidusCourse.CourseStatus.DRAFT
        self.course.save(update_fields=['status'])

    # ------------------------------------------------------------- regression

    def test_a_quiz_built_by_bulk_apply_passes_submission_validation(self):
        """Generated content earns no exemption: `_validate_course_completeness`
        still decides whether the course may leave draft."""
        self.post([_question('First?'), _question('Second?')])

        try:
            self.course._validate_course_completeness()
        except ValidationError as exc:
            self.assertNotIn('quizzes', getattr(exc, 'message_dict', {}))

    def test_a_question_with_no_correct_answer_still_blocks_submission(self):
        """Written by hand rather than through this endpoint — the serializer
        makes it unreachable here, and the check behind it must stay."""
        question = QuizQuestion.objects.create(
            quiz=self.quiz, question_text='Unanswerable?', position=1,
        )
        QuizAnswer.objects.create(question=question, answer_text='A', is_correct=False)

        with self.assertRaises(ValidationError) as ctx:
            self.course._validate_course_completeness()
        self.assertIn('quizzes', ctx.exception.message_dict)

    def test_answers_created_here_still_hide_is_correct_from_learners(self):
        from courses.all_serializers.learner_serializers import (
            _LearnerQuizAnswerOptionSerializer,
        )

        self.post([_question('First?')])
        answer = QuizAnswer.objects.filter(question__quiz=self.quiz).first()

        data = _LearnerQuizAnswerOptionSerializer(answer).data
        self.assertNotIn('is_correct', data)

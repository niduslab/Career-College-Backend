from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import (
    CourseSection,
    Enrollment,
    NidusCourse,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
    SectionContent,
)


class LearnerQuizConsumptionAPITests(APITestCase):
    """Phase-2 quiz consumption: GET detail + POST submit."""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='quiz_instructor@example.com',
            password='pw12345!',
            full_name='Quiz Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='quiz_learner@example.com',
            password='pw12345!',
            full_name='Quiz Learner',
            user_type='learner',
            is_email_verified=True,
        )
        cls.outsider = User.objects.create_user(
            email='quiz_outsider@example.com',
            password='pw12345!',
            full_name='Quiz Outsider',
            user_type='learner',
            is_email_verified=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Quizzable Course',
            slug='quizzable-course',
            description='A course used by Phase-2 quiz tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        cls.course.instructors.add(cls.instructor)

        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section One', position=1,
        )
        cls.quiz = Quiz.objects.create(
            section=cls.section,
            title='Sample Quiz',
            description='Three multiple choice questions.',
        )
        SectionContent.objects.create(
            section=cls.section,
            item_type=SectionContent.ItemType.QUIZ,
            content_type=ContentType.objects.get_for_model(Quiz),
            object_id=cls.quiz.pk,
            position=1,
        )

        # Q1 with correct answer cls.q1_correct (option B)
        cls.q1 = QuizQuestion.objects.create(quiz=cls.quiz, question_text='Q1?', position=1)
        cls.q1_wrong = QuizAnswer.objects.create(question=cls.q1, answer_text='Q1-A', is_correct=False)
        cls.q1_correct = QuizAnswer.objects.create(question=cls.q1, answer_text='Q1-B', is_correct=True)
        cls.q1_other = QuizAnswer.objects.create(question=cls.q1, answer_text='Q1-C', is_correct=False)

        # Q2 with correct answer cls.q2_correct (option A)
        cls.q2 = QuizQuestion.objects.create(quiz=cls.quiz, question_text='Q2?', position=2)
        cls.q2_correct = QuizAnswer.objects.create(question=cls.q2, answer_text='Q2-A', is_correct=True)
        cls.q2_wrong = QuizAnswer.objects.create(question=cls.q2, answer_text='Q2-B', is_correct=False)

        # Q3 with correct answer cls.q3_correct (option B)
        cls.q3 = QuizQuestion.objects.create(quiz=cls.quiz, question_text='Q3?', position=3)
        cls.q3_wrong = QuizAnswer.objects.create(question=cls.q3, answer_text='Q3-A', is_correct=False)
        cls.q3_correct = QuizAnswer.objects.create(question=cls.q3, answer_text='Q3-B', is_correct=True)

        cls.enrollment = Enrollment.objects.create(
            user=cls.learner, course=cls.course, is_active=True,
        )

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.learner)

    # -------------------------------------------------------------------------
    # GET /learn/quizzes/<id>/
    # -------------------------------------------------------------------------

    def test_quiz_detail_returns_questions_without_is_correct(self):
        self.auth()
        url = reverse('courses:learner-quiz-detail', kwargs={'quiz_id': self.quiz.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['title'], 'Sample Quiz')
        self.assertEqual(data['question_count'], 3)
        questions = data['questions']
        self.assertEqual([q['position'] for q in questions], [1, 2, 3])
        # Critical: no answer option may leak `is_correct`.
        for question in questions:
            for answer in question['answers']:
                self.assertNotIn('is_correct', answer)
                self.assertIn('id', answer)
                self.assertIn('answer_text', answer)
        self.assertIsNone(data['latest_attempt'])  # No prior attempts.

    def test_quiz_detail_returns_404_for_unenrolled_learner(self):
        self.auth(self.outsider)
        url = reverse('courses:learner-quiz-detail', kwargs={'quiz_id': self.quiz.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_quiz_detail_allows_instructor_preview(self):
        self.auth(self.instructor)
        url = reverse('courses:learner-quiz-detail', kwargs={'quiz_id': self.quiz.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Instructor preview still must not leak `is_correct` (use same
        # serializer absence-as-guarantee).
        for question in response.data['data']['questions']:
            for answer in question['answers']:
                self.assertNotIn('is_correct', answer)

    def test_quiz_detail_includes_latest_attempt_after_submission(self):
        self.auth()
        submit_url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})
        self.client.post(
            submit_url,
            {
                'answers': [
                    {'question_id': self.q1.id, 'selected_answer_id': self.q1_correct.id},
                    {'question_id': self.q2.id, 'selected_answer_id': self.q2_correct.id},
                    {'question_id': self.q3.id, 'selected_answer_id': self.q3_wrong.id},
                ],
            },
            format='json',
        )

        detail_url = reverse('courses:learner-quiz-detail', kwargs={'quiz_id': self.quiz.id})
        response = self.client.get(detail_url)

        latest = response.data['data']['latest_attempt']
        self.assertIsNotNone(latest)
        self.assertEqual(latest['score'], 2)
        self.assertEqual(latest['max_score'], 3)

    # -------------------------------------------------------------------------
    # POST /learn/quizzes/<id>/submit/
    # -------------------------------------------------------------------------

    def test_submit_scores_all_correct(self):
        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        response = self.client.post(
            url,
            {
                'answers': [
                    {'question_id': self.q1.id, 'selected_answer_id': self.q1_correct.id},
                    {'question_id': self.q2.id, 'selected_answer_id': self.q2_correct.id},
                    {'question_id': self.q3.id, 'selected_answer_id': self.q3_correct.id},
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['score'], 3)
        self.assertEqual(data['max_score'], 3)
        for question in data['questions']:
            self.assertTrue(question['is_correct'])
            # Per rule: correct_answer_* must NOT appear when is_correct=True.
            self.assertNotIn('correct_answer_id', question)
            self.assertNotIn('correct_answer_text', question)

    def test_submit_shows_correct_answer_only_for_wrong_questions(self):
        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        response = self.client.post(
            url,
            {
                'answers': [
                    {'question_id': self.q1.id, 'selected_answer_id': self.q1_wrong.id},
                    {'question_id': self.q2.id, 'selected_answer_id': self.q2_correct.id},
                    {'question_id': self.q3.id, 'selected_answer_id': self.q3_wrong.id},
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['score'], 1)
        questions_by_id = {q['question_id']: q for q in data['questions']}

        # Wrong answer → correct_answer_* present.
        q1_result = questions_by_id[self.q1.id]
        self.assertFalse(q1_result['is_correct'])
        self.assertEqual(q1_result['correct_answer_id'], self.q1_correct.id)
        self.assertEqual(q1_result['correct_answer_text'], 'Q1-B')

        # Correct answer → correct_answer_* omitted.
        q2_result = questions_by_id[self.q2.id]
        self.assertTrue(q2_result['is_correct'])
        self.assertNotIn('correct_answer_id', q2_result)
        self.assertNotIn('correct_answer_text', q2_result)

    def test_submit_handles_unanswered_questions_as_wrong(self):
        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        # Submit only Q1; leave Q2 and Q3 unanswered.
        response = self.client.post(
            url,
            {
                'answers': [
                    {'question_id': self.q1.id, 'selected_answer_id': self.q1_correct.id},
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['score'], 1)
        self.assertEqual(data['max_score'], 3)

        questions_by_id = {q['question_id']: q for q in data['questions']}
        # Unanswered Q2 → selected_answer_id=None, is_correct=False, correct shown.
        q2_result = questions_by_id[self.q2.id]
        self.assertIsNone(q2_result['selected_answer_id'])
        self.assertIsNone(q2_result['selected_answer_text'])
        self.assertFalse(q2_result['is_correct'])
        self.assertEqual(q2_result['correct_answer_id'], self.q2_correct.id)

    def test_submit_accepts_explicit_null_selection(self):
        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        response = self.client.post(
            url,
            {
                'answers': [
                    {'question_id': self.q1.id, 'selected_answer_id': None},
                    {'question_id': self.q2.id, 'selected_answer_id': self.q2_correct.id},
                    {'question_id': self.q3.id, 'selected_answer_id': None},
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['score'], 1)

    def test_submit_persists_attempt_and_answers(self):
        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        self.client.post(
            url,
            {
                'answers': [
                    {'question_id': self.q1.id, 'selected_answer_id': self.q1_correct.id},
                    {'question_id': self.q2.id, 'selected_answer_id': self.q2_wrong.id},
                    {'question_id': self.q3.id, 'selected_answer_id': self.q3_correct.id},
                ],
            },
            format='json',
        )

        attempt = QuizAttempt.objects.get(user=self.learner, quiz=self.quiz)
        self.assertEqual(attempt.score, 2)
        self.assertEqual(attempt.max_score, 3)
        self.assertEqual(QuizAttemptAnswer.objects.filter(attempt=attempt).count(), 3)

    def test_submit_creates_separate_attempt_each_time(self):
        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})
        payload = {
            'answers': [
                {'question_id': self.q1.id, 'selected_answer_id': self.q1_correct.id},
                {'question_id': self.q2.id, 'selected_answer_id': self.q2_correct.id},
                {'question_id': self.q3.id, 'selected_answer_id': self.q3_correct.id},
            ],
        }
        self.client.post(url, payload, format='json')
        self.client.post(url, payload, format='json')

        self.assertEqual(
            QuizAttempt.objects.filter(user=self.learner, quiz=self.quiz).count(),
            2,
        )

    def test_submit_rejects_unenrolled_learner_with_404(self):
        self.auth(self.outsider)
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        response = self.client.post(
            url,
            {'answers': [{'question_id': self.q1.id, 'selected_answer_id': self.q1_correct.id}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            QuizAttempt.objects.filter(user=self.outsider, quiz=self.quiz).exists()
        )

    def test_submit_rejects_instructor_preview(self):
        self.auth(self.instructor)
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        response = self.client.post(
            url,
            {'answers': [{'question_id': self.q1.id, 'selected_answer_id': self.q1_correct.id}]},
            format='json',
        )

        # IsLearnerUser blocks the instructor — protects attempt history.
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_submit_rejects_question_from_a_different_quiz(self):
        # Build a second quiz with its own question; reference it in the payload.
        other_quiz = Quiz.objects.create(
            section=self.section, title='Other Quiz', description='',
        )
        other_q = QuizQuestion.objects.create(quiz=other_quiz, question_text='X?', position=1)

        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        response = self.client.post(
            url,
            {'answers': [{'question_id': other_q.id, 'selected_answer_id': None}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('answers', response.data['errors'])

    def test_submit_rejects_answer_from_a_different_question(self):
        # Try to attach Q2's correct answer to Q1.
        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        response = self.client.post(
            url,
            {'answers': [{'question_id': self.q1.id, 'selected_answer_id': self.q2_correct.id}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('answers', response.data['errors'])

    def test_submit_rejects_duplicate_question_in_payload(self):
        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        response = self.client.post(
            url,
            {
                'answers': [
                    {'question_id': self.q1.id, 'selected_answer_id': self.q1_correct.id},
                    {'question_id': self.q1.id, 'selected_answer_id': self.q1_wrong.id},
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # -------------------------------------------------------------------------
    # Progress recalc after submission (transaction.on_commit hook)
    # -------------------------------------------------------------------------

    def test_submit_schedules_progress_recalc_on_commit(self):
        # `submit_quiz_attempt` defers `recalculate_progress` via
        # `transaction.on_commit` so a recalc failure can't roll back a
        # valid attempt. `captureOnCommitCallbacks` records and runs those
        # callbacks under the test's outer transaction.
        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(
                url,
                {
                    'answers': [
                        {'question_id': self.q1.id, 'selected_answer_id': self.q1_correct.id},
                        {'question_id': self.q2.id, 'selected_answer_id': self.q2_correct.id},
                        {'question_id': self.q3.id, 'selected_answer_id': self.q3_correct.id},
                    ],
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # At least the recalc is deferred to on_commit. Exact count isn't pinned:
        # reaching 100% cascades further on_commit callbacks (certificate issuance
        # + course-completed notification), which is correct behaviour.
        self.assertGreaterEqual(len(callbacks), 1)

        # With one quiz and zero lectures on the course, completing the quiz
        # should bring progress_percent to 100.
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress_percent, 100)
        self.assertIsNotNone(self.enrollment.completed_at)

    def test_submit_does_not_create_quiz_submitted_notification(self):
        from notifications.models import Notification
        self.auth()
        url = reverse('courses:learner-quiz-submit', kwargs={'quiz_id': self.quiz.id})

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                url,
                {
                    'answers': [
                        {'question_id': self.q1.id, 'selected_answer_id': self.q1_correct.id},
                        {'question_id': self.q2.id, 'selected_answer_id': self.q2_correct.id},
                        {'question_id': self.q3.id, 'selected_answer_id': self.q3_correct.id},
                    ],
                },
                format='json',
            )

        self.assertFalse(
            Notification.objects.filter(event_type='quiz.submitted').exists(),
            'quiz.submitted notifications must not be created on submission',
        )

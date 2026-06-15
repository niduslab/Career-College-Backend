from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, User
from career_college_backend.celery import app as celery_app
from courses.models import (
    Assignment,
    AssignmentQuestion,
    AssignmentSubmission,
    AssignmentSubmissionAnswer,
    CourseSection,
    Enrollment,
    NidusCourse,
    SectionContent,
)
from courses.serializers import AssignmentQuestionSerializer
from courses.services.assignment_grading import RubricGrader


class RubricGraderUnitTests(SimpleTestCase):
    def setUp(self):
        self.grader = RubricGrader()

    def test_empty_rubric_returns_zero_score(self):
        score, results, feedback = self.grader.grade('any answer text', [], 10)
        self.assertEqual(score, 0)
        self.assertEqual(results, [])
        self.assertEqual(feedback, '')

    def test_keyword_match_is_case_insensitive_by_default(self):
        rubric = [{'type': 'keyword', 'value': 'Gradient Descent', 'points': 5}]
        score, results, _ = self.grader.grade('we used gradient descent', rubric, 5)
        self.assertEqual(score, 5)
        self.assertTrue(results[0]['matched'])

    def test_keyword_can_be_case_sensitive(self):
        rubric = [{
            'type': 'keyword', 'value': 'API', 'case_sensitive': True, 'points': 4,
        }]
        score, _, _ = self.grader.grade('we use the api', rubric, 4)
        self.assertEqual(score, 0)

    def test_regex_match(self):
        rubric = [{
            'type': 'regex', 'value': r'\blearning[_ ]rate\b', 'points': 3,
        }]
        score, _, _ = self.grader.grade('Set the learning rate to 0.01', rubric, 3)
        self.assertEqual(score, 3)

    def test_min_length_strips_whitespace(self):
        rubric = [{'type': 'min_length', 'value': 5, 'points': 2}]
        score, _, _ = self.grader.grade('   abcd   ', rubric, 2)
        self.assertEqual(score, 0)
        score, _, _ = self.grader.grade('   abcde  ', rubric, 2)
        self.assertEqual(score, 2)

    def test_max_length_match(self):
        rubric = [{'type': 'max_length', 'value': 10, 'points': 1}]
        score, _, _ = self.grader.grade('short', rubric, 1)
        self.assertEqual(score, 1)
        score, _, _ = self.grader.grade('this is definitely too long', rubric, 1)
        self.assertEqual(score, 0)

    def test_any_of_passes_when_one_keyword_present(self):
        rubric = [{'type': 'any_of', 'value': ['python', 'java', 'rust'], 'points': 3}]
        score, _, _ = self.grader.grade('I prefer Java for this.', rubric, 3)
        self.assertEqual(score, 3)
        score, _, _ = self.grader.grade('nothing here', rubric, 3)
        self.assertEqual(score, 0)

    def test_all_of_requires_every_keyword(self):
        rubric = [{'type': 'all_of', 'value': ['django', 'orm'], 'points': 4}]
        score, _, _ = self.grader.grade('Django and its ORM are great', rubric, 4)
        self.assertEqual(score, 4)
        score, _, _ = self.grader.grade('Django only', rubric, 4)
        self.assertEqual(score, 0)

    def test_score_clamps_to_max_score_when_rubric_oversums(self):
        rubric = [
            {'type': 'keyword', 'value': 'x', 'points': 15},
            {'type': 'keyword', 'value': 'y', 'points': 5},
        ]
        score, _, _ = self.grader.grade('x y', rubric, 10)
        self.assertEqual(score, 10)

    def test_unknown_criterion_type_records_miss_without_crash(self):
        rubric = [{'type': 'parse_with_llm', 'value': 'whatever', 'points': 5}]
        score, results, _ = self.grader.grade('any', rubric, 5)
        self.assertEqual(score, 0)
        self.assertFalse(results[0]['matched'])
        self.assertIn('Unknown criterion type', results[0]['feedback'])

    def test_feedback_strings_joined_in_order(self):
        rubric = [
            {'type': 'keyword', 'value': 'a', 'points': 1,
             'feedback_on_match': 'got a', 'feedback_on_miss': ''},
            {'type': 'keyword', 'value': 'b', 'points': 1,
             'feedback_on_match': '', 'feedback_on_miss': 'missed b'},
        ]
        _, _, feedback = self.grader.grade('a', rubric, 2)
        self.assertEqual(feedback, 'got a\nmissed b')



class AssignmentQuestionRubricAuthoringTests(APITestCase):

    def _validate(self, data, instance=None):
        serializer = AssignmentQuestionSerializer(instance=instance, data=data, partial=instance is not None)
        return serializer.is_valid(), serializer.errors

    def test_valid_rubric_is_accepted(self):
        data = {
            'question_text': 'Explain backprop.',
            'points': 5,
            'rubric': [
                {'type': 'keyword', 'value': 'chain rule', 'points': 3},
                {'type': 'min_length', 'value': 50, 'points': 2},
            ],
        }
        valid, errors = self._validate(data)
        self.assertTrue(valid, errors)

    def test_sum_of_points_mismatch_is_rejected(self):
        data = {
            'question_text': 'Q?',
            'points': 10,
            'rubric': [{'type': 'keyword', 'value': 'x', 'points': 3}],
        }
        valid, errors = self._validate(data)
        self.assertFalse(valid)

    def test_unknown_criterion_type_is_rejected(self):
        data = {
            'question_text': 'Q?',
            'points': 5,
            'rubric': [{'type': 'mystery', 'value': 'x', 'points': 5}],
        }
        valid, errors = self._validate(data)
        self.assertFalse(valid)

    def test_bad_regex_is_rejected(self):
        data = {
            'question_text': 'Q?',
            'points': 5,
            'rubric': [{'type': 'regex', 'value': '[unbalanced', 'points': 5}],
        }
        valid, errors = self._validate(data)
        self.assertFalse(valid)

    def test_empty_rubric_is_allowed_during_draft_authoring(self):
        data = {'question_text': 'Q?', 'points': 5, 'rubric': []}
        valid, errors = self._validate(data)
        self.assertTrue(valid, errors)

    def test_rubric_stripped_from_non_instructor_response(self):
        question = _make_assignment_question_with_rubric()
        learner = User.objects.create_user(
            email='rubric_learner@example.com', password='pw12345!',
            full_name='L', user_type='learner', is_email_verified=True,
        )
        request = _fake_request(learner)
        data = AssignmentQuestionSerializer(question, context={'request': request}).data
        self.assertNotIn('rubric', data)
        self.assertNotIn('model_answer', data)

    def test_rubric_visible_in_instructor_response(self):
        question = _make_assignment_question_with_rubric()
        instructor = User.objects.create_user(
            email='rubric_instructor@example.com', password='pw12345!',
            full_name='I', user_type='instructor', is_email_verified=True,
        )
        request = _fake_request(instructor)
        data = AssignmentQuestionSerializer(question, context={'request': request}).data
        self.assertIn('rubric', data)
        self.assertEqual(len(data['rubric']), 1)



class AssignmentTotalScoreTests(APITestCase):

    def test_passing_score_above_total_score_is_rejected(self):
        from courses.serializers import AssignmentCreateUpdateSerializer
        s = AssignmentCreateUpdateSerializer(data={
            'title': 'Sample Assignment',
            'total_score': 10,
            'passing_score': 11,
        })
        self.assertFalse(s.is_valid())
        self.assertIn('passing_score', s.errors)

    def test_passing_score_equal_to_total_score_is_allowed(self):
        from courses.serializers import AssignmentCreateUpdateSerializer
        s = AssignmentCreateUpdateSerializer(data={
            'title': 'Sample Assignment',
            'total_score': 10,
            'passing_score': 10,
        })
        self.assertTrue(s.is_valid(), s.errors)

    def test_partial_update_uses_instance_for_missing_side(self):
        from courses.serializers import AssignmentCreateUpdateSerializer
        class Stub:
            total_score = 100
            passing_score = 10
        s = AssignmentCreateUpdateSerializer(
            instance=Stub(),
            data={'passing_score': 80},
            partial=True,
        )
        self.assertTrue(s.is_valid(), s.errors)

    def test_submission_max_score_snapshots_assignment_total_score(self):
        instructor = User.objects.create_user(
            email='ts_instr@example.com', password='pw12345!',
            full_name='I', user_type='instructor', is_email_verified=True,
        )
        learner = User.objects.create_user(
            email='ts_learner@example.com', password='pw12345!',
            full_name='L', user_type='learner', is_email_verified=True,
        )
        course = NidusCourse.objects.create(
            created_by=instructor, title='C', slug='c-snapshot',
            description='', status=NidusCourse.CourseStatus.PUBLISHED,
        )
        course.instructors.add(instructor)
        section = CourseSection.objects.create(course=course, title='S', position=1)
        assignment = Assignment.objects.create(
            section=section, title='A', total_score=50, passing_score=0,
        )
        question = AssignmentQuestion.objects.create(
            assignment=assignment, question_text='Q?', points=5, position=1,
            rubric=[{'type': 'keyword', 'value': 'x', 'points': 5}],
        )
        Enrollment.objects.create(user=learner, course=course, is_active=True)

        from courses.services import submit_assignment
        submission = submit_assignment(
            user=learner, assignment=assignment,
            answers_payload=[{'question_id': question.id, 'answer_text': ''}],
        )
        self.assertEqual(submission.max_score, 50)


def _make_assignment_question_with_rubric():
    instructor = User.objects.create_user(
        email=f'q_owner_{id(object())}@example.com',
        password='pw12345!', full_name='Owner', user_type='instructor',
        is_email_verified=True,
    )
    course = NidusCourse.objects.create(
        created_by=instructor, title=f'Course {id(object())}',
        description='', slug=f'course-{id(object())}',
    )
    course.instructors.add(instructor)
    section = CourseSection.objects.create(course=course, title='S', position=1)
    assignment = Assignment.objects.create(
        section=section, title='A', total_score=5, passing_score=0,
    )
    return AssignmentQuestion.objects.create(
        assignment=assignment, question_text='Q?', points=5, position=1,
        rubric=[{'type': 'keyword', 'value': 'foo', 'points': 5}],
    )


def _fake_request(user):
    class _Req:
        pass
    req = _Req()
    req.user = user
    return req



class LearnerAssignmentConsumptionAPITests(APITestCase):
    """End-to-end assignment flow. Celery runs inline (mutated app config — override_settings doesn't propagate)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._prev_eager = celery_app.conf.task_always_eager
        cls._prev_propagates = celery_app.conf.task_eager_propagates
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

    @classmethod
    def tearDownClass(cls):
        celery_app.conf.task_always_eager = cls._prev_eager
        celery_app.conf.task_eager_propagates = cls._prev_propagates
        super().tearDownClass()

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='ax_instructor@example.com', password='pw12345!',
            full_name='Ax Instructor', user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.instructor).update(is_verified=True)
        cls.learner = User.objects.create_user(
            email='ax_learner@example.com', password='pw12345!',
            full_name='Ax Learner', user_type='learner', is_email_verified=True,
        )
        cls.outsider = User.objects.create_user(
            email='ax_outsider@example.com', password='pw12345!',
            full_name='Ax Outsider', user_type='learner', is_email_verified=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Assignable Course',
            slug='assignable-course',
            description='',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        cls.course.instructors.add(cls.instructor)

        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section One', position=1,
        )

        cls.assignment = Assignment.objects.create(
            section=cls.section, title='Mini Essay',
            total_score=10, passing_score=5,
            instructions='Answer both questions.',
        )
        SectionContent.objects.create(
            section=cls.section,
            item_type=SectionContent.ItemType.ASSIGNMENT,
            content_type=ContentType.objects.get_for_model(Assignment),
            object_id=cls.assignment.pk,
            position=1,
        )

        cls.q1 = AssignmentQuestion.objects.create(
            assignment=cls.assignment, question_text='What is gradient descent?',
            model_answer='An optimization algorithm.', points=6, position=1,
            rubric=[
                {'type': 'keyword', 'value': 'gradient descent', 'points': 4,
                 'feedback_on_match': 'Nailed the term.',
                 'feedback_on_miss': 'Missing the core term.'},
                {'type': 'min_length', 'value': 20, 'points': 2,
                 'feedback_on_match': 'Detailed enough.',
                 'feedback_on_miss': 'Needs more detail.'},
            ],
        )
        cls.q2 = AssignmentQuestion.objects.create(
            assignment=cls.assignment, question_text='What is a learning rate?',
            model_answer='A hyperparameter that scales the update step.',
            points=4, position=2,
            rubric=[
                {'type': 'keyword', 'value': 'learning rate', 'points': 4,
                 'feedback_on_match': 'Correct.',
                 'feedback_on_miss': 'Did not mention learning rate.'},
            ],
        )

        cls.enrollment = Enrollment.objects.create(
            user=cls.learner, course=cls.course, is_active=True,
        )

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.learner)


    def test_detail_strips_model_answer_and_rubric(self):
        self.auth()
        url = reverse('courses:learner-assignment-detail', kwargs={'assignment_id': self.assignment.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['title'], 'Mini Essay')
        self.assertEqual(data['question_count'], 2)
        self.assertEqual(data['max_score'], 10)
        self.assertEqual(data['passing_score'], 5)
        for q in data['questions']:
            self.assertNotIn('model_answer', q)
            self.assertNotIn('rubric', q)
            self.assertIn('question_text', q)
            self.assertIn('points', q)
        self.assertIsNone(data['latest_submission'])

    def test_detail_returns_404_for_unenrolled_learner(self):
        self.auth(self.outsider)
        url = reverse('courses:learner-assignment-detail', kwargs={'assignment_id': self.assignment.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_allows_instructor_preview(self):
        self.auth(self.instructor)
        url = reverse('courses:learner-assignment-detail', kwargs={'assignment_id': self.assignment.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for question in response.data['data']['questions']:
            self.assertNotIn('model_answer', question)
            self.assertNotIn('rubric', question)

    def test_detail_includes_latest_submission_after_submitting(self):
        self.auth()
        self._submit_passing_answers()
        url = reverse('courses:learner-assignment-detail', kwargs={'assignment_id': self.assignment.id})
        response = self.client.get(url)
        latest = response.data['data']['latest_submission']
        self.assertIsNotNone(latest)
        self.assertEqual(latest['status'], AssignmentSubmission.Status.PASSED)
        self.assertEqual(latest['max_score'], 10)


    def test_submit_happy_path_returns_202_and_grades_inline(self):
        self.auth()
        response = self._submit_passing_answers()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        data = response.data['data']
        self.assertEqual(data['status'], AssignmentSubmission.Status.SUBMITTED)
        submission_id = data['submission_id']

        submission = AssignmentSubmission.objects.get(pk=submission_id)
        self.assertEqual(submission.status, AssignmentSubmission.Status.PASSED)
        self.assertEqual(submission.total_score, 10)
        self.assertEqual(submission.max_score, 10)
        for answer in submission.answers.all():
            self.assertTrue(answer.rubric_snapshot, 'rubric_snapshot must be populated')

    def test_submit_below_passing_score_marks_failed(self):
        self.auth()
        url = reverse('courses:learner-assignment-submit', kwargs={'assignment_id': self.assignment.id})

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                {
                    'answers': [
                        {'question_id': self.q1.id, 'answer_text': 'no'},
                        {'question_id': self.q2.id, 'answer_text': 'not relevant'},
                    ],
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        submission = AssignmentSubmission.objects.get(pk=response.data['data']['submission_id'])
        self.assertEqual(submission.status, AssignmentSubmission.Status.FAILED)
        self.assertEqual(submission.total_score, 0)

    def test_submit_rejects_inflight_submission_with_422(self):
        AssignmentSubmission.objects.create(
            user=self.learner, assignment=self.assignment,
            status=AssignmentSubmission.Status.GRADING, max_score=10,
        )
        self.auth()
        response = self._submit_passing_answers()

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_submit_rejects_question_from_a_different_assignment(self):
        other_assignment = Assignment.objects.create(
            section=self.section, title='Other', passing_score=0,
        )
        other_q = AssignmentQuestion.objects.create(
            assignment=other_assignment, question_text='X?', points=1, position=1,
            rubric=[],
        )
        self.auth()
        url = reverse('courses:learner-assignment-submit', kwargs={'assignment_id': self.assignment.id})

        response = self.client.post(
            url,
            {
                'answers': [
                    {'question_id': other_q.id, 'answer_text': 'foo'},
                    {'question_id': self.q1.id, 'answer_text': 'gradient descent ' * 10},
                    {'question_id': self.q2.id, 'answer_text': 'learning rate'},
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_submit_rejects_duplicate_question_in_payload(self):
        self.auth()
        url = reverse('courses:learner-assignment-submit', kwargs={'assignment_id': self.assignment.id})

        response = self.client.post(
            url,
            {
                'answers': [
                    {'question_id': self.q1.id, 'answer_text': 'a'},
                    {'question_id': self.q1.id, 'answer_text': 'b'},
                    {'question_id': self.q2.id, 'answer_text': 'c'},
                ],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_submit_requires_all_questions_answered(self):
        self.auth()
        url = reverse('courses:learner-assignment-submit', kwargs={'assignment_id': self.assignment.id})

        response = self.client.post(
            url,
            {'answers': [{'question_id': self.q1.id, 'answer_text': 'only q1'}]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_submit_rejects_unenrolled_learner_with_404(self):
        self.auth(self.outsider)
        url = reverse('courses:learner-assignment-submit', kwargs={'assignment_id': self.assignment.id})

        response = self.client.post(
            url,
            {
                'answers': [
                    {'question_id': self.q1.id, 'answer_text': 'x'},
                    {'question_id': self.q2.id, 'answer_text': 'y'},
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            AssignmentSubmission.objects.filter(user=self.outsider).exists()
        )

    def test_submit_rejects_instructor_preview(self):
        self.auth(self.instructor)
        url = reverse('courses:learner-assignment-submit', kwargs={'assignment_id': self.assignment.id})

        response = self.client.post(
            url,
            {
                'answers': [
                    {'question_id': self.q1.id, 'answer_text': 'x'},
                    {'question_id': self.q2.id, 'answer_text': 'y'},
                ],
            },
            format='json',
        )
        # IsLearnerUser blocks the instructor — preview must not pollute history.
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


    def test_submission_detail_visible_to_owner(self):
        self.auth()
        submit_response = self._submit_passing_answers()
        submission_id = submit_response.data['data']['submission_id']

        detail_url = reverse(
            'courses:learner-assignment-submission-detail',
            kwargs={'submission_id': submission_id},
        )
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['status'], AssignmentSubmission.Status.PASSED)
        self.assertEqual(len(data['answers']), 2)
        for answer in data['answers']:
            self.assertIn('criterion_results', answer)
            self.assertIn('feedback', answer)

    def test_submission_detail_returns_404_for_other_learner(self):
        self.auth()
        self._submit_passing_answers()
        submission = AssignmentSubmission.objects.filter(user=self.learner).first()

        self.client.force_authenticate(user=self.outsider)
        url = reverse(
            'courses:learner-assignment-submission-detail',
            kwargs={'submission_id': submission.id},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_submission_detail_reveals_model_answer_only_after_terminal_grading(self):
        submission = AssignmentSubmission.objects.create(
            user=self.learner, assignment=self.assignment,
            status=AssignmentSubmission.Status.GRADING, max_score=10,
        )
        AssignmentSubmissionAnswer.objects.create(
            submission=submission, question=self.q1,
            answer_text='x', max_score=6, rubric_snapshot=self.q1.rubric,
        )
        AssignmentSubmissionAnswer.objects.create(
            submission=submission, question=self.q2,
            answer_text='y', max_score=4, rubric_snapshot=self.q2.rubric,
        )

        self.auth()
        url = reverse(
            'courses:learner-assignment-submission-detail',
            kwargs={'submission_id': submission.id},
        )
        response = self.client.get(url)
        for answer in response.data['data']['answers']:
            self.assertNotIn('model_answer', answer)

        submission.status = AssignmentSubmission.Status.PASSED
        submission.save(update_fields=['status'])

        response = self.client.get(url)
        for answer in response.data['data']['answers']:
            self.assertIn('model_answer', answer)

    def test_submission_detail_hides_model_answer_in_grading_failed(self):
        submission = AssignmentSubmission.objects.create(
            user=self.learner, assignment=self.assignment,
            status=AssignmentSubmission.Status.GRADING_FAILED,
            grading_error='Worker exploded', max_score=10,
        )
        AssignmentSubmissionAnswer.objects.create(
            submission=submission, question=self.q1, answer_text='x',
            max_score=6, rubric_snapshot=self.q1.rubric,
        )
        AssignmentSubmissionAnswer.objects.create(
            submission=submission, question=self.q2, answer_text='y',
            max_score=4, rubric_snapshot=self.q2.rubric,
        )

        self.auth()
        url = reverse(
            'courses:learner-assignment-submission-detail',
            kwargs={'submission_id': submission.id},
        )
        response = self.client.get(url)
        for answer in response.data['data']['answers']:
            self.assertNotIn('model_answer', answer)
        self.assertEqual(response.data['data']['grading_error'], 'Worker exploded')


    def test_retry_from_grading_failed_re_enqueues_and_reaches_terminal(self):
        submission = AssignmentSubmission.objects.create(
            user=self.learner, assignment=self.assignment,
            status=AssignmentSubmission.Status.GRADING_FAILED,
            grading_error='transient infra blip', max_score=10,
        )
        AssignmentSubmissionAnswer.objects.create(
            submission=submission, question=self.q1,
            answer_text='gradient descent is an optimization algorithm.',
            max_score=6, rubric_snapshot=self.q1.rubric,
        )
        AssignmentSubmissionAnswer.objects.create(
            submission=submission, question=self.q2,
            answer_text='learning rate is the step size.',
            max_score=4, rubric_snapshot=self.q2.rubric,
        )

        self.auth()
        url = reverse(
            'courses:learner-assignment-submission-retry',
            kwargs={'submission_id': submission.id},
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        submission.refresh_from_db()
        self.assertEqual(submission.status, AssignmentSubmission.Status.PASSED)
        self.assertEqual(submission.grading_error, '')

    def test_retry_rejected_when_status_is_not_grading_failed(self):
        submission = AssignmentSubmission.objects.create(
            user=self.learner, assignment=self.assignment,
            status=AssignmentSubmission.Status.PASSED, max_score=10, total_score=10,
        )
        self.auth()
        url = reverse(
            'courses:learner-assignment-submission-retry',
            kwargs={'submission_id': submission.id},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_retry_on_other_learners_submission_returns_404(self):
        submission = AssignmentSubmission.objects.create(
            user=self.learner, assignment=self.assignment,
            status=AssignmentSubmission.Status.GRADING_FAILED, max_score=10,
        )
        self.client.force_authenticate(user=self.outsider)
        url = reverse(
            'courses:learner-assignment-submission-retry',
            kwargs={'submission_id': submission.id},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


    def test_grade_task_short_circuits_when_status_already_terminal(self):
        from courses.tasks import grade_assignment_submission_task

        self.auth()
        submit_response = self._submit_passing_answers()
        submission_id = submit_response.data['data']['submission_id']

        with patch('courses.tasks.recalculate_progress') as recalc_mock:
            result = grade_assignment_submission_task.run(submission_id)

        self.assertTrue(result.get('skipped'))
        recalc_mock.assert_not_called()


    def test_passing_submission_triggers_progress_recalc(self):
        self.auth()
        with self.captureOnCommitCallbacks(execute=True):
            self._submit_passing_answers()

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress_percent, 100)
        self.assertIsNotNone(self.enrollment.completed_at)

    def test_failing_submission_does_not_advance_progress(self):
        self.auth()
        url = reverse('courses:learner-assignment-submit', kwargs={'assignment_id': self.assignment.id})

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                url,
                {
                    'answers': [
                        {'question_id': self.q1.id, 'answer_text': 'no'},
                        {'question_id': self.q2.id, 'answer_text': 'no'},
                    ],
                },
                format='json',
            )

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress_percent, 0)
        self.assertIsNone(self.enrollment.completed_at)

    def test_grading_does_not_create_assignment_graded_notification(self):
        from notifications.models import Notification
        self.auth()

        with self.captureOnCommitCallbacks(execute=True):
            self._submit_passing_answers()

        self.assertFalse(
            Notification.objects.filter(event_type='assignment.graded').exists(),
            'assignment.graded notifications must not be created after grading',
        )

    def _submit_passing_answers(self):
        url = reverse('courses:learner-assignment-submit', kwargs={'assignment_id': self.assignment.id})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                {
                    'answers': [
                        {
                            'question_id': self.q1.id,
                            'answer_text': 'gradient descent minimizes the loss iteratively.',
                        },
                        {
                            'question_id': self.q2.id,
                            'answer_text': 'the learning rate scales each update.',
                        },
                    ],
                },
                format='json',
            )
        return response

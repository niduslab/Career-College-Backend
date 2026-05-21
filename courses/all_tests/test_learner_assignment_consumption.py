"""
Tests for the Phase-2 learner assignment consumption + auto-grading flow.

Covers:
  - RubricGrader unit tests (pure Python, no DB).
  - Authoring-side rubric validation on AssignmentQuestionSerializer.
  - GET  /learn/assignments/<id>/                                   (detail)
  - POST /learn/assignments/<id>/submit/                            (submit + auto-grade)
  - GET  /learn/assignments/submissions/<id>/                       (own-submission detail)
  - POST /learn/assignments/submissions/<id>/retry/                 (grading_failed recovery)
  - recalculate_progress integration when a submission transitions to passed.

Celery tasks run inline via CELERY_TASK_ALWAYS_EAGER so we can assert the
final terminal state inside the same request/response cycle.
"""

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


# =============================================================================
# RubricGrader unit tests (no DB, no client)
# =============================================================================

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
        # 4 chars after strip → miss.
        score, _, _ = self.grader.grade('   abcd   ', rubric, 2)
        self.assertEqual(score, 0)
        # 5 chars after strip → match.
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
        # Misconfigured rubric whose criteria sum to 20 but question.points = 10.
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


# =============================================================================
# Authoring-side rubric validation on AssignmentQuestionSerializer
# =============================================================================

class AssignmentQuestionRubricAuthoringTests(APITestCase):
    """Validate that the authoring serializer enforces the rubric's shape
    + sum-of-points invariant before persistence."""

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
        # Empty rubric => no auto-grading possible, but authoring should
        # not block — instructors author iteratively.
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


# =============================================================================
# Assignment.total_score (instructor-declared total)
# =============================================================================

class AssignmentTotalScoreTests(APITestCase):
    """Cross-field validation on the authoring serializer + snapshot
    behaviour on the submission flow."""

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
        # Stub matching the model's attribute access; serializer only reads
        # total_score / passing_score for cross-field validation.
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
        """Even if sum(question.points) differs, the submission's max_score
        snapshot is the assignment's declared total."""
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
        # total_score = 50 but question.points sum to 5. Snapshot must be 50.
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


# =============================================================================
# Learner consumption + auto-grading API tests
# =============================================================================

class LearnerAssignmentConsumptionAPITests(APITestCase):
    """End-to-end Phase-2 assignment flow with Celery running inline.

    Celery is forced into eager mode by mutating the app config directly —
    `@override_settings(CELERY_TASK_ALWAYS_EAGER=True)` does not propagate
    because the Celery app's config is loaded once at module import.

    Submits / retries are wrapped in `captureOnCommitCallbacks(execute=True)`
    inside helpers (`_submit_with_grading`, `_retry_with_grading`) because
    `transaction.on_commit` callbacks never fire automatically under
    `TestCase` — the test's wrapping transaction is rolled back, not
    committed.
    """

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
            description='Course used by Phase-2 assignment tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        cls.course.instructors.add(cls.instructor)

        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section One', position=1,
        )

        # Assignment with total_score=10 (declared), passing_score=5;
        # two questions allocating 6 + 4 = 10 points (matches the declared total).
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

    # -------------------------------------------------------------------------
    # GET /learn/assignments/<id>/
    # -------------------------------------------------------------------------

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
        for question in data['questions']:
            self.assertNotIn('model_answer', question)
            self.assertNotIn('rubric', question)
            self.assertIn('question_text', question)
            self.assertIn('points', question)
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
        # Instructor preview uses the same serializer; absence guarantee
        # still holds — `model_answer` / `rubric` not leaked here either.
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

    # -------------------------------------------------------------------------
    # POST /learn/assignments/<id>/submit/
    # -------------------------------------------------------------------------

    def test_submit_happy_path_returns_202_and_grades_inline(self):
        self.auth()
        response = self._submit_passing_answers()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        data = response.data['data']
        self.assertEqual(data['status'], AssignmentSubmission.Status.SUBMITTED)
        submission_id = data['submission_id']

        # Celery ran inline (eager mode) → submission should be terminal now.
        submission = AssignmentSubmission.objects.get(pk=submission_id)
        self.assertEqual(submission.status, AssignmentSubmission.Status.PASSED)
        self.assertEqual(submission.total_score, 10)
        self.assertEqual(submission.max_score, 10)
        # rubric_snapshot copied onto each answer row at submit time.
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
                        # Q1 will miss the keyword and the length criterion.
                        {'question_id': self.q1.id, 'answer_text': 'no'},
                        # Q2 will miss the keyword.
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
        # Manually park a submission in `grading` so the partial unique index
        # (or the .exists() guard) blocks a concurrent attempt.
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

    # -------------------------------------------------------------------------
    # Submission detail (own only, model_answer reveal rule)
    # -------------------------------------------------------------------------

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
        # Each answer has criterion_results & feedback.
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
        # Create a submission stuck in `grading` (Celery skipped via mock).
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

        # Now flip to passed and the reveal kicks in.
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

    # -------------------------------------------------------------------------
    # POST /learn/assignments/submissions/<id>/retry/
    # -------------------------------------------------------------------------

    def test_retry_from_grading_failed_re_enqueues_and_reaches_terminal(self):
        # Park a submission in `grading_failed` with real answer rows so the
        # eager re-dispatch can actually grade something.
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
        # grading_error must be cleared on retry.
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

    # -------------------------------------------------------------------------
    # Idempotency under double-dispatch of the Celery task
    # -------------------------------------------------------------------------

    def test_grade_task_short_circuits_when_status_already_terminal(self):
        # Run grading once, then dispatch the task again directly. The second
        # run must not re-grade or re-trigger recalc.
        from courses.tasks import grade_assignment_submission_task

        self.auth()
        submit_response = self._submit_passing_answers()
        submission_id = submit_response.data['data']['submission_id']

        with patch('courses.tasks.recalculate_progress') as recalc_mock:
            result = grade_assignment_submission_task.run(submission_id)

        # Short-circuit branch returns the skipped marker.
        self.assertTrue(result.get('skipped'))
        recalc_mock.assert_not_called()

    # -------------------------------------------------------------------------
    # recalculate_progress integration
    # -------------------------------------------------------------------------

    def test_passing_submission_triggers_progress_recalc(self):
        # The assignment is the only content item on the course, so a
        # passing grade should bring progress to 100%.
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

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _submit_passing_answers(self):
        # Wrap in captureOnCommitCallbacks so the on_commit-deferred Celery
        # dispatch actually runs inside the test's transaction. With eager
        # mode set on the app config, the task body executes synchronously
        # when `.delay()` is called, so by the time this helper returns the
        # submission has reached a terminal state.
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

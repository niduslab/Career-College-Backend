"""
Tests for the Phase-2 learner coding-exercise consumption flow.

Covers:
  - CodeRunner pure-Python helpers: _normalize() (whitespace-collapses only
    JSON-ish output) and _parse_batch_output() (sentinel decoder).
  - GET  /learn/coding-exercises/<id>/                                 (detail)
  - POST /learn/coding-exercises/<id>/run/                             (transient)
  - GET  /learn/coding-exercises/tasks/<task_id>/                      (Run poll)
  - POST /learn/coding-exercises/<id>/submit/                          (persisted)
  - GET  /learn/coding-exercises/submissions/<id>/                     (Submit poll)
  - POST /learn/coding-exercises/submissions/<id>/retry/               (error recovery)
  - reap_stuck_coding_submissions_task                                 (zombie reaper)
  - recalculate_progress integration when a CodingSubmission passes.

CLAUDE.md is explicit that tests must never hit real Docker. Every test
patches `courses.services.code_runner.CodeRunner.run_submission` to return
deterministic SingleTestResult lists.

Celery is forced into eager mode by mutating the app config directly so
.delay() executes synchronously inside the request/response cycle (same
pattern as test_learner_assignment_consumption.py).
"""

from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, User
from career_college_backend.celery import app as celery_app
from courses.models import (
    CodingExercise,
    CodingExerciseLanguageConfig,
    CodingSubmission,
    CodingSubmissionTestResult,
    CodingTestCase,
    CourseSection,
    Enrollment,
    NidusCourse,
    SectionContent,
)
from courses.services.code_runner import (
    SingleTestResult,
    _normalize,
    _parse_batch_output,
)


# =============================================================================
# CodeRunner pure-Python helpers (no Docker, no DB)
# =============================================================================

class CodeRunnerNormalizeTests(SimpleTestCase):
    """_normalize collapses whitespace ONLY for JSON-array / JSON-object
    output. Strings and numbers keep their internal whitespace.
    """

    def test_strings_keep_internal_whitespace(self):
        # 'hello world' != 'helloworld' is the canonical example from the
        # standalone-platform doc — strings are compared with spaces intact.
        self.assertNotEqual(_normalize('hello world'), _normalize('helloworld'))
        self.assertEqual(_normalize('  hello  '), 'hello')

    def test_array_collapses_internal_whitespace(self):
        self.assertEqual(_normalize('[1, 2, 3]'), _normalize('[1,2,3]'))
        self.assertEqual(_normalize('[\n  1,\n  2\n]'), '[1,2]')

    def test_object_collapses_internal_whitespace(self):
        self.assertEqual(
            _normalize('{"a": 1, "b": 2}'),
            _normalize('{"a":1,"b":2}'),
        )

    def test_numeric_strings_kept_literal(self):
        # 1 != 1.0 — number strings are compared character-by-character.
        self.assertNotEqual(_normalize('1'), _normalize('1.0'))


class CodeRunnerSentinelParserTests(SimpleTestCase):
    """The sentinel parser is the single point where harness output becomes
    structured per-test results, so it earns a unit test independent of
    Docker."""

    def _make_block(self, idx, status_, runtime, exit_code, stdout_b, stderr_b):
        return (
            b'<<<TEST_RESULT idx=%d status=%s runtime_ms=%d exit=%d '
            b'stdout_len=%d stderr_len=%d>>>\n'
            % (idx, status_.encode(), runtime, exit_code, len(stdout_b), len(stderr_b))
            + stdout_b + b'\n'
            + stderr_b + b'\n'
            + b'<<<END idx=%d>>>\n' % idx
        )

    def test_parses_two_result_blocks_in_order(self):
        stream = (
            self._make_block(0, 'passed', 12, 0, b'hello', b'')
            + self._make_block(1, 'error', 4, 1, b'partial', b'boom')
        )
        results = _parse_batch_output(stream, 2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].actual_output, 'hello')
        self.assertEqual(results[0].harness_status, 'passed')
        self.assertEqual(results[0].runtime_ms, 12)
        self.assertEqual(results[1].actual_output, 'partial')
        self.assertEqual(results[1].stderr, 'boom')
        self.assertEqual(results[1].harness_status, 'error')

    def test_missing_test_stays_none_so_caller_can_mark_error(self):
        # Only the first test produced output (container crashed before #2).
        stream = self._make_block(0, 'passed', 1, 0, b'ok', b'')
        results = _parse_batch_output(stream, 3)
        self.assertEqual(len(results), 3)
        self.assertIsNotNone(results[0])
        self.assertIsNone(results[1])
        self.assertIsNone(results[2])

    def test_binary_output_with_embedded_sentinel_text_survives_length_prefix(self):
        # If the learner accidentally prints something that looks like a
        # sentinel, the length-prefix protects us — the parser doesn't go
        # looking for the next <<<TEST_RESULT inside the body.
        evil = b'<<<TEST_RESULT idx=0 status=passed runtime_ms=0 exit=0 stdout_len=0 stderr_len=0>>>'
        stream = self._make_block(0, 'passed', 1, 0, evil, b'')
        results = _parse_batch_output(stream, 1)
        self.assertEqual(results[0].actual_output, evil.decode())


# =============================================================================
# Learner consumption + execution API tests
# =============================================================================

class LearnerCodingConsumptionAPITests(APITestCase):
    """End-to-end Phase-2 coding-exercise flow with Celery eager + the
    CodeRunner patched."""

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
            email='cx_instructor@example.com', password='pw12345!',
            full_name='Cx Instructor', user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.instructor).update(is_verified=True)
        cls.learner = User.objects.create_user(
            email='cx_learner@example.com', password='pw12345!',
            full_name='Cx Learner', user_type='learner', is_email_verified=True,
        )
        cls.outsider = User.objects.create_user(
            email='cx_outsider@example.com', password='pw12345!',
            full_name='Cx Outsider', user_type='learner', is_email_verified=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Codable Course',
            slug='codable-course',
            description='Course used by Phase-2 coding tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        cls.course.instructors.add(cls.instructor)
        cls.section = CourseSection.objects.create(
            course=cls.course, title='Code Section', position=1,
        )

        cls.exercise = CodingExercise.objects.create(
            section=cls.section,
            title='Sum Two',
            description='Sum the two integers in input.',
            problem_statement='Given two ints on one line, print their sum.',
            difficulty=CodingExercise.Difficulty.EASY,
            default_language='python',
            supported_languages=['python', 'javascript'],
            time_limit_ms=2000,
        )
        SectionContent.objects.create(
            section=cls.section,
            item_type=SectionContent.ItemType.CODING,
            content_type=ContentType.objects.get_for_model(CodingExercise),
            object_id=cls.exercise.pk,
            position=1,
        )

        CodingExerciseLanguageConfig.objects.create(
            exercise=cls.exercise, language='python',
            starter_code='def solve(s):\n    pass\n',
            solution_code='def solve(s):\n    a,b=map(int,s.split())\n    return a+b\n',
        )

        # Two visible cases + one hidden case.
        cls.tc_v1 = CodingTestCase.objects.create(
            exercise=cls.exercise, position=1,
            input_data='1 2', expected_output='3',
            is_hidden=False, explanation='easy',
        )
        cls.tc_v2 = CodingTestCase.objects.create(
            exercise=cls.exercise, position=2,
            input_data='4 5', expected_output='9',
            is_hidden=False,
        )
        cls.tc_hidden = CodingTestCase.objects.create(
            exercise=cls.exercise, position=3,
            input_data='100 200', expected_output='300',
            is_hidden=True,
        )

        cls.enrollment = Enrollment.objects.create(
            user=cls.learner, course=cls.course, is_active=True,
        )

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.learner)

    # -------------------------------------------------------------------------
    # GET /learn/coding-exercises/<id>/
    # -------------------------------------------------------------------------

    def test_detail_omits_solution_code_and_hidden_test_cases(self):
        self.auth()
        url = reverse(
            'courses:learner-coding-exercise-detail',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['title'], 'Sum Two')

        # Language configs never expose solution_code.
        for cfg in data['language_configs']:
            self.assertNotIn('solution_code', cfg)
            self.assertIn('starter_code', cfg)

        # Only the two visible test cases are present.
        positions = [tc['position'] for tc in data['test_cases']]
        self.assertEqual(sorted(positions), [1, 2])
        for tc in data['test_cases']:
            self.assertNotIn('is_hidden', tc)  # field isn't even declared

    def test_detail_returns_404_for_unenrolled_learner(self):
        self.auth(self.outsider)
        url = reverse(
            'courses:learner-coding-exercise-detail',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_allows_instructor_preview(self):
        self.auth(self.instructor)
        url = reverse(
            'courses:learner-coding-exercise-detail',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Instructor preview uses the same learner serializer, so the
        # absence guarantee for solution_code still holds.
        for cfg in response.data['data']['language_configs']:
            self.assertNotIn('solution_code', cfg)

    # -------------------------------------------------------------------------
    # POST /learn/coding-exercises/<id>/run/
    # -------------------------------------------------------------------------

    def test_run_dispatches_visible_tests_only_and_returns_task_id(self):
        self.auth()
        url = reverse(
            'courses:learner-coding-run',
            kwargs={'exercise_id': self.exercise.id},
        )

        captured_test_cases = []

        def _fake_run_submission(self_runner, code, test_cases, time_limit_ms, language):
            captured_test_cases.append(list(test_cases))
            return [
                SingleTestResult(
                    status='passed', actual_output='3', stdout='3', stderr='',
                    runtime_ms=2, exit_code=0,
                ),
                SingleTestResult(
                    status='passed', actual_output='9', stdout='9', stderr='',
                    runtime_ms=2, exit_code=0,
                ),
            ]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ):
            response = self.client.post(
                url,
                {'language': 'python', 'code': 'def solve(s): return sum(map(int,s.split()))'},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn('task_id', response.data['data'])
        # The runner was called with only the visible test cases (hidden filtered upstream).
        positions = sorted(tc.position for tc in captured_test_cases[0])
        self.assertEqual(positions, [1, 2])

    def test_run_rejects_instructor_preview_with_403(self):
        self.auth(self.instructor)
        url = reverse(
            'courses:learner-coding-run',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.post(
            url, {'language': 'python', 'code': 'def solve(s): pass'}, format='json',
        )
        # IsLearnerUser blocks the instructor — preview must not pollute history.
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_run_rejects_unsupported_language(self):
        self.auth()
        url = reverse(
            'courses:learner-coding-run',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.post(
            url, {'language': 'cpp', 'code': 'void solve(...){}'}, format='json',
        )
        # cpp is not in supported_languages for this exercise.
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # -------------------------------------------------------------------------
    # POST /learn/coding-exercises/<id>/submit/
    # -------------------------------------------------------------------------

    def test_submit_persists_all_tests_with_hidden_redacted(self):
        self.auth()
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )

        def _fake_run_submission(self_runner, code, test_cases, time_limit_ms, language):
            # Runner sees ALL three test cases (visible + hidden) — Submit
            # mode does not filter.
            self_test = self
            self_test.assertEqual(len(test_cases), 3)
            return [
                SingleTestResult('passed', '3', '3', '', 5, 0),
                SingleTestResult('passed', '9', '9', '', 5, 0),
                SingleTestResult('passed', '300', '300', '', 5, 0),
            ]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                {'language': 'python', 'code': 'def solve(s): return sum(map(int,s.split()))'},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        submission = CodingSubmission.objects.get(pk=response.data['data']['id'])
        # Celery ran inline -> submission reached PASSED.
        self.assertEqual(submission.status, CodingSubmission.Status.PASSED)
        self.assertEqual(submission.passed_tests, 3)
        self.assertEqual(submission.total_tests, 3)
        self.assertEqual(submission.score, 100)
        self.assertEqual(submission.test_results.count(), 3)
        # The hidden row's flag was copied at write time.
        hidden_row = submission.test_results.get(position=3)
        self.assertTrue(hidden_row.is_hidden)

        # GET the submission and assert hidden rows are stripped entirely
        # from test_results. Aggregate counts still reflect all 3 tests so
        # the learner can infer hidden-test outcome from the mismatch.
        detail_url = reverse(
            'courses:learner-coding-submission-detail',
            kwargs={'submission_id': submission.id},
        )
        detail = self.client.get(detail_url).data['data']
        self.assertEqual(detail['total_tests'], 3)
        self.assertEqual(detail['passed_tests'], 3)
        # Only visible rows surface in test_results.
        positions = sorted(r['position'] for r in detail['test_results'])
        self.assertEqual(positions, [1, 2])
        for row in detail['test_results']:
            self.assertFalse(row['is_hidden'])

    def test_submit_status_precedence_error_over_failed(self):
        """One ERROR + one FAILED + one PASSED -> submission status = ERROR."""
        self.auth()
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )

        def _fake_run_submission(self_runner, code, test_cases, time_limit_ms, language):
            return [
                SingleTestResult('passed', '3', '3', '', 1, 0),
                SingleTestResult('failed', '8', '8', '', 1, 0),
                SingleTestResult('error', '', '', 'crash', 1, 1),
            ]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url, {'language': 'python', 'code': 'def solve(s): pass'}, format='json',
            )
        submission = CodingSubmission.objects.get(pk=response.data['data']['id'])
        self.assertEqual(submission.status, CodingSubmission.Status.ERROR)
        self.assertIn('crash', submission.error_message)

    def test_submit_blocks_inflight_with_422(self):
        # Park a queued submission for the same (user, exercise).
        CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='x',
            status=CodingSubmission.Status.QUEUED, total_tests=3,
        )
        self.auth()
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.post(
            url, {'language': 'python', 'code': 'def solve(s): pass'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_submit_rejects_instructor_with_403(self):
        self.auth(self.instructor)
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.post(
            url, {'language': 'python', 'code': 'def solve(s): pass'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_submit_rejects_unenrolled_learner_with_404(self):
        self.auth(self.outsider)
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.post(
            url, {'language': 'python', 'code': 'def solve(s): pass'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # -------------------------------------------------------------------------
    # GET submission detail (own only)
    # -------------------------------------------------------------------------

    def test_submission_detail_for_other_learner_returns_404(self):
        sub = CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='x',
            status=CodingSubmission.Status.PASSED, total_tests=3, passed_tests=3,
        )
        self.client.force_authenticate(user=self.outsider)
        url = reverse(
            'courses:learner-coding-submission-detail',
            kwargs={'submission_id': sub.id},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # -------------------------------------------------------------------------
    # POST retry/
    # -------------------------------------------------------------------------

    def test_retry_only_works_for_error_status(self):
        # PASSED submissions are not retryable — learner should submit fresh code.
        sub = CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='x',
            status=CodingSubmission.Status.PASSED, total_tests=3, passed_tests=3,
        )
        self.auth()
        url = reverse(
            'courses:learner-coding-submission-retry',
            kwargs={'submission_id': sub.id},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_retry_re_enqueues_errored_submission(self):
        sub = CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='def solve(s): return sum(map(int,s.split()))',
            status=CodingSubmission.Status.ERROR, total_tests=3,
            error_message='transient docker hiccup',
        )

        def _fake_run_submission(self_runner, code, test_cases, time_limit_ms, language):
            return [
                SingleTestResult('passed', '3', '3', '', 1, 0),
                SingleTestResult('passed', '9', '9', '', 1, 0),
                SingleTestResult('passed', '300', '300', '', 1, 0),
            ]

        self.auth()
        url = reverse(
            'courses:learner-coding-submission-retry',
            kwargs={'submission_id': sub.id},
        )
        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        sub.refresh_from_db()
        self.assertEqual(sub.status, CodingSubmission.Status.PASSED)
        self.assertEqual(sub.error_message, '')

    def test_retry_on_other_learners_submission_returns_404(self):
        sub = CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='x',
            status=CodingSubmission.Status.ERROR, total_tests=3,
        )
        self.client.force_authenticate(user=self.outsider)
        url = reverse(
            'courses:learner-coding-submission-retry',
            kwargs={'submission_id': sub.id},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # -------------------------------------------------------------------------
    # Zombie reaper
    # -------------------------------------------------------------------------

    def test_reaper_flips_stale_in_flight_submissions_to_error(self):
        from datetime import timedelta
        from courses.tasks import reap_stuck_coding_submissions_task

        stuck = CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='x',
            status=CodingSubmission.Status.QUEUED, total_tests=3,
        )
        # Backdate submitted_at past the 5-minute stale threshold; auto_now_add
        # set it to now() so we update directly.
        CodingSubmission.objects.filter(pk=stuck.pk).update(
            submitted_at=timezone.now() - timedelta(minutes=10),
        )

        result = reap_stuck_coding_submissions_task.run()
        self.assertEqual(result['reaped'], 1)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, CodingSubmission.Status.ERROR)
        self.assertIn('Reaped', stuck.error_message)

    def test_reaper_leaves_fresh_in_flight_alone(self):
        from courses.tasks import reap_stuck_coding_submissions_task

        fresh = CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='x',
            status=CodingSubmission.Status.QUEUED, total_tests=3,
        )
        result = reap_stuck_coding_submissions_task.run()
        self.assertEqual(result['reaped'], 0)
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, CodingSubmission.Status.QUEUED)

    # -------------------------------------------------------------------------
    # Progress integration
    # -------------------------------------------------------------------------

    def test_passing_submission_advances_progress(self):
        # The coding exercise is the only content item -> 100% on PASS.
        self.auth()
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )

        def _fake_run_submission(self_runner, code, test_cases, time_limit_ms, language):
            return [
                SingleTestResult('passed', '3', '3', '', 1, 0),
                SingleTestResult('passed', '9', '9', '', 1, 0),
                SingleTestResult('passed', '300', '300', '', 1, 0),
            ]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ), self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                url,
                {'language': 'python', 'code': 'def solve(s): return sum(map(int,s.split()))'},
                format='json',
            )

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress_percent, 100)
        self.assertIsNotNone(self.enrollment.completed_at)

    def test_failing_submission_does_not_advance_progress(self):
        self.auth()
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )

        def _fake_run_submission(self_runner, code, test_cases, time_limit_ms, language):
            return [
                SingleTestResult('failed', '0', '0', '', 1, 0),
                SingleTestResult('failed', '0', '0', '', 1, 0),
                SingleTestResult('failed', '0', '0', '', 1, 0),
            ]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ), self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                url,
                {'language': 'python', 'code': 'def solve(s): return 0'},
                format='json',
            )

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress_percent, 0)

    # -------------------------------------------------------------------------
    # Idempotency under acks_late redelivery
    # -------------------------------------------------------------------------

    def test_submit_task_short_circuits_when_status_already_terminal(self):
        from courses.tasks import evaluate_coding_submission_task

        sub = CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='x',
            status=CodingSubmission.Status.PASSED, total_tests=3, passed_tests=3,
        )
        with patch('courses.tasks.recalculate_progress') as recalc_mock:
            result = evaluate_coding_submission_task.run(sub.id)
        self.assertTrue(result.get('skipped'))
        recalc_mock.assert_not_called()

"""
Tests for the learner coding-exercise consumption flow (script evaluation).

Covers:
  - CodeRunner pure-Python helper: _parse_script_output() (sentinel decoder).
  - GET  /learn/coding-exercises/<id>/                                 (detail)
  - POST /learn/coding-exercises/<id>/run/                             (transient)
  - POST /learn/coding-exercises/<id>/submit/                          (persisted)
  - GET  /learn/coding-exercises/submissions/<id>/                     (Submit poll)
  - POST /learn/coding-exercises/submissions/<id>/retry/               (error recovery)
  - reap_stuck_coding_submissions_task                                 (zombie reaper)
  - recalculate_progress integration when a CodingSubmission passes.
  - Leak guard: evaluation_script / solution_code never reach learners.

CLAUDE.md is explicit that tests must never hit real Docker. Every test
patches `courses.services.code_runner.CodeRunner.run_submission` to return
deterministic ScriptTestResult lists.

Celery is forced into eager mode by mutating the app config directly so
.delay() executes synchronously inside the request/response cycle (same
pattern as test_learner_assignment_consumption.py).
"""

import json

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
    CodingSubmission,
    CodingSubmissionTestResult,
    CourseSection,
    Enrollment,
    NidusCourse,
    SectionContent,
)
from courses.services.code_runner import (
    ScriptTestResult,
    _parse_script_output,
)

_EVAL_SCRIPT = (
    'import unittest\n'
    'from exercise import add\n'
    'class AddTests(unittest.TestCase):\n'
    '    def test_small(self):\n'
    '        self.assertEqual(add(1, 2), 3)\n'
    '    def test_medium(self):\n'
    '        self.assertEqual(add(4, 5), 9)\n'
    '    def test_large(self):\n'
    '        self.assertEqual(add(100, 200), 300)\n'
)


# =============================================================================
# CodeRunner pure-Python helper (no Docker, no DB)
# =============================================================================

class CodeRunnerScriptParserTests(SimpleTestCase):
    """The sentinel parser is the single point where harness output becomes
    structured per-test results, so it earns a unit test independent of
    Docker."""

    def _make_block(self, idx, status_, runtime, name_b, stdout_b, stderr_b):
        return (
            b'<<<SCRIPT_RESULT idx=%d status=%s runtime_ms=%d '
            b'name_len=%d stdout_len=%d stderr_len=%d>>>\n'
            % (idx, status_.encode(), runtime, len(name_b), len(stdout_b), len(stderr_b))
            + name_b + b'\n'
            + stdout_b + b'\n'
            + stderr_b + b'\n'
            + b'<<<SCRIPT_END idx=%d>>>\n' % idx
        )

    def test_parses_two_result_blocks_in_order(self):
        stream = (
            self._make_block(0, 'passed', 12, b'evaluate.AddTests.test_small', b'hello', b'')
            + self._make_block(1, 'error', 4, b'evaluate.AddTests.test_medium', b'partial', b'boom')
        )
        results = _parse_script_output(stream)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].test_name, 'evaluate.AddTests.test_small')
        self.assertEqual(results[0].status, 'passed')
        self.assertEqual(results[0].stdout, 'hello')
        self.assertEqual(results[0].runtime_ms, 12)
        self.assertEqual(results[1].test_name, 'evaluate.AddTests.test_medium')
        self.assertEqual(results[1].status, 'error')
        self.assertEqual(results[1].stderr, 'boom')

    def test_empty_stream_yields_no_results(self):
        # The caller (CodeRunner) synthesizes a single error result when the
        # parser returns nothing — the parser itself stays honest.
        self.assertEqual(_parse_script_output(b''), [])
        self.assertEqual(_parse_script_output(b'garbage with no sentinel\n'), [])

    def test_names_with_dots_and_spaces_survive_length_prefix(self):
        name = b'evaluate.My Test Class.test with spaces'
        stream = self._make_block(0, 'passed', 1, name, b'', b'')
        results = _parse_script_output(stream)
        self.assertEqual(results[0].test_name, name.decode())

    def test_multiline_traceback_survives_length_prefix(self):
        tb = b'Traceback (most recent call last):\n  File "<x>", line 1\nAssertionError: 3 != 4\n'
        stream = self._make_block(0, 'failed', 2, b'evaluate.T.test_x', b'', tb)
        results = _parse_script_output(stream)
        self.assertEqual(results[0].status, 'failed')
        self.assertEqual(results[0].stderr, tb.decode())

    def test_body_with_embedded_sentinel_text_survives_length_prefix(self):
        # If the learner prints something that looks like a sentinel, the
        # length-prefix protects us — the parser doesn't go looking for the
        # next <<<SCRIPT_RESULT inside the body.
        evil = (
            b'<<<SCRIPT_RESULT idx=0 status=passed runtime_ms=0 '
            b'name_len=0 stdout_len=0 stderr_len=0>>>'
        )
        stream = (
            self._make_block(0, 'passed', 1, b'evaluate.T.test_a', evil, b'')
            + self._make_block(1, 'failed', 1, b'evaluate.T.test_b', b'', b'nope')
        )
        results = _parse_script_output(stream)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].stdout, evil.decode())
        self.assertEqual(results[1].status, 'failed')

    def test_truncated_tail_yields_partial_results(self):
        # Crash mid-suite: only the first block completed.
        stream = self._make_block(0, 'passed', 1, b'evaluate.T.test_a', b'ok', b'')
        stream += b'<<<SCRIPT_RESULT idx=1 status='  # torn header
        results = _parse_script_output(stream)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].test_name, 'evaluate.T.test_a')


# =============================================================================
# Learner consumption + execution API tests
# =============================================================================

class LearnerCodingConsumptionAPITests(APITestCase):
    """End-to-end coding-exercise flow with Celery eager + the CodeRunner
    patched."""

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
            description='Course used by coding consumption tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        cls.course.instructors.add(cls.instructor)
        cls.section = CourseSection.objects.create(
            course=cls.course, title='Code Section', position=1,
        )

        cls.exercise = CodingExercise.objects.create(
            section=cls.section,
            title='Sum Two',
            description='Write add(a, b) returning the sum of two ints.',
            language=CodingExercise.Language.PYTHON,
            starter_code='def add(a, b):\n    pass\n',
            solution_code='def add(a, b):\n    return a + b\n',
            evaluation_script=_EVAL_SCRIPT,
            time_limit_ms=2000,
        )
        SectionContent.objects.create(
            section=cls.section,
            item_type=SectionContent.ItemType.CODING,
            content_type=ContentType.objects.get_for_model(CodingExercise),
            object_id=cls.exercise.pk,
            position=1,
        )

        cls.enrollment = Enrollment.objects.create(
            user=cls.learner, course=cls.course, is_active=True,
        )

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.learner)

    @staticmethod
    def _passing_results():
        return [
            ScriptTestResult('evaluate.AddTests.test_small', 'passed', '', '', 1),
            ScriptTestResult('evaluate.AddTests.test_medium', 'passed', '', '', 1),
            ScriptTestResult('evaluate.AddTests.test_large', 'passed', '', '', 1),
        ]

    # -------------------------------------------------------------------------
    # GET /learn/coding-exercises/<id>/
    # -------------------------------------------------------------------------

    def test_detail_never_leaks_solution_or_evaluation_script(self):
        self.auth()
        url = reverse(
            'courses:learner-coding-exercise-detail',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['title'], 'Sum Two')
        self.assertEqual(data['language'], 'python')
        self.assertIn('starter_code', data)

        # Belt-and-braces: the raw payload must not contain the key names or
        # the script body anywhere.
        raw = json.dumps(response.data)
        self.assertNotIn('evaluation_script', raw)
        self.assertNotIn('solution_code', raw)
        self.assertNotIn('assertEqual(add(1, 2), 3)', raw)

    def test_detail_has_no_test_cases_key(self):
        self.auth()
        url = reverse(
            'courses:learner-coding-exercise-detail',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.get(url)
        self.assertNotIn('test_cases', response.data['data'])
        self.assertNotIn('language_configs', response.data['data'])

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
        # absence guarantee still holds.
        self.assertNotIn('solution_code', response.data['data'])
        self.assertNotIn('evaluation_script', response.data['data'])

    # -------------------------------------------------------------------------
    # POST /learn/coding-exercises/<id>/run/
    # -------------------------------------------------------------------------

    def test_run_passes_evaluation_script_to_runner_and_returns_task_id(self):
        self.auth()
        url = reverse(
            'courses:learner-coding-run',
            kwargs={'exercise_id': self.exercise.id},
        )

        captured = {}

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            captured['script'] = evaluation_script
            captured['language'] = language
            return [
                ScriptTestResult('evaluate.AddTests.test_small', 'passed', '', '', 2),
                ScriptTestResult('evaluate.AddTests.test_medium', 'failed', '', 'AssertionError: 8 != 9', 2),
            ]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ):
            response = self.client.post(
                url,
                {'language': 'python', 'code': 'def add(a, b): return a + b'},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn('task_id', response.data['data'])
        self.assertEqual(captured['script'], _EVAL_SCRIPT)
        self.assertEqual(captured['language'], 'python')

    def _make_scriptless_exercise(self):
        exercise = CodingExercise.objects.create(
            section=self.section,
            title='No Script Yet',
            description='Exercise whose evaluation script has not been written.',
            language=CodingExercise.Language.PYTHON,
            starter_code='def f():\n    pass\n',
            evaluation_script='',
        )
        SectionContent.objects.create(
            section=self.section,
            item_type=SectionContent.ItemType.CODING,
            content_type=ContentType.objects.get_for_model(CodingExercise),
            object_id=exercise.pk,
            position=SectionContent.objects.filter(section=self.section).count() + 1,
        )
        return exercise

    def test_run_returns_422_when_exercise_has_no_evaluation_script(self):
        exercise = self._make_scriptless_exercise()
        self.auth()
        url = reverse(
            'courses:learner-coding-run',
            kwargs={'exercise_id': exercise.id},
        )
        response = self.client.post(
            url, {'language': 'python', 'code': 'def f(): pass'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_run_rejects_instructor_preview_with_403(self):
        self.auth(self.instructor)
        url = reverse(
            'courses:learner-coding-run',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.post(
            url, {'language': 'python', 'code': 'def add(a, b): pass'}, format='json',
        )
        # IsLearnerUser blocks the instructor — preview must not pollute history.
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_run_rejects_mismatched_language(self):
        self.auth()
        url = reverse(
            'courses:learner-coding-run',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.post(
            url, {'language': 'cpp', 'code': 'int add(int a, int b){return a+b;}'}, format='json',
        )
        # Exercises are single-language; this one is python.
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # -------------------------------------------------------------------------
    # POST /learn/coding-exercises/<id>/submit/
    # -------------------------------------------------------------------------

    def test_submit_persists_named_results_and_backfills_total(self):
        self.auth()
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            return self._passing_results()

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                {'language': 'python', 'code': 'def add(a, b): return a + b'},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        # The immediate 202 body reflects the queued row: count unknown yet.
        self.assertEqual(response.data['data']['total_tests'], 0)

        submission = CodingSubmission.objects.get(pk=response.data['data']['id'])
        # Celery ran inline -> submission reached PASSED with total back-filled.
        self.assertEqual(submission.status, CodingSubmission.Status.PASSED)
        self.assertEqual(submission.passed_tests, 3)
        self.assertEqual(submission.total_tests, 3)
        self.assertEqual(submission.score, 100)
        rows = list(submission.test_results.order_by('position'))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].test_name, 'evaluate.AddTests.test_small')
        self.assertEqual([r.position for r in rows], [1, 2, 3])

        # GET the submission: every row is returned with its test name.
        detail_url = reverse(
            'courses:learner-coding-submission-detail',
            kwargs={'submission_id': submission.id},
        )
        detail = self.client.get(detail_url).data['data']
        self.assertEqual(detail['total_tests'], 3)
        self.assertEqual(detail['passed_tests'], 3)
        names = [r['test_name'] for r in detail['test_results']]
        self.assertEqual(names, [
            'evaluate.AddTests.test_small',
            'evaluate.AddTests.test_medium',
            'evaluate.AddTests.test_large',
        ])

    def test_submit_status_precedence_error_over_failed(self):
        """One ERROR + one FAILED + one PASSED -> submission status = ERROR."""
        self.auth()
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            return [
                ScriptTestResult('evaluate.T.test_a', 'passed', '', '', 1),
                ScriptTestResult('evaluate.T.test_b', 'failed', '', 'AssertionError', 1),
                ScriptTestResult('evaluate.T.test_c', 'error', '', 'crash', 1),
            ]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url, {'language': 'python', 'code': 'def add(a, b): pass'}, format='json',
            )
        submission = CodingSubmission.objects.get(pk=response.data['data']['id'])
        self.assertEqual(submission.status, CodingSubmission.Status.ERROR)
        self.assertIn('crash', submission.error_message)

    def test_submit_load_crash_yields_single_error_row(self):
        """A learner syntax error crashes the suite at import: the runner
        returns one synthetic 'load' error result."""
        self.auth()
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            return [ScriptTestResult(
                'evaluate (load)', 'error', '',
                'Traceback (most recent call last):\nSyntaxError: invalid syntax', 0,
            )]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url, {'language': 'python', 'code': 'def add(a b): return'}, format='json',
            )
        submission = CodingSubmission.objects.get(pk=response.data['data']['id'])
        self.assertEqual(submission.status, CodingSubmission.Status.ERROR)
        self.assertEqual(submission.total_tests, 1)
        self.assertIn('SyntaxError', submission.error_message)
        row = submission.test_results.get()
        self.assertEqual(row.test_name, 'evaluate (load)')

    def test_submit_returns_422_when_exercise_has_no_evaluation_script(self):
        exercise = self._make_scriptless_exercise()
        self.auth()
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': exercise.id},
        )
        response = self.client.post(
            url, {'language': 'python', 'code': 'def f(): pass'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        # Nothing persisted.
        self.assertFalse(
            CodingSubmission.objects.filter(user=self.learner, exercise=exercise).exists()
        )

    def test_submit_blocks_inflight_with_422(self):
        # Park a queued submission for the same (user, exercise).
        CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='x',
            status=CodingSubmission.Status.QUEUED,
        )
        self.auth()
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.post(
            url, {'language': 'python', 'code': 'def add(a, b): pass'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_submit_rejects_instructor_with_403(self):
        self.auth(self.instructor)
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.post(
            url, {'language': 'python', 'code': 'def add(a, b): pass'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_submit_rejects_unenrolled_learner_with_404(self):
        self.auth(self.outsider)
        url = reverse(
            'courses:learner-coding-submit',
            kwargs={'exercise_id': self.exercise.id},
        )
        response = self.client.post(
            url, {'language': 'python', 'code': 'def add(a, b): pass'}, format='json',
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

    def test_submission_detail_never_leaks_evaluation_script(self):
        sub = CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='x',
            status=CodingSubmission.Status.PASSED, total_tests=1, passed_tests=1,
        )
        CodingSubmissionTestResult.objects.create(
            submission=sub, test_name='evaluate.AddTests.test_small',
            status='passed', position=1,
        )
        self.auth()
        url = reverse(
            'courses:learner-coding-submission-detail',
            kwargs={'submission_id': sub.id},
        )
        raw = json.dumps(self.client.get(url).data)
        self.assertNotIn('evaluation_script', raw)
        self.assertNotIn('solution_code', raw)

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
            language='python', code='def add(a, b): return a + b',
            status=CodingSubmission.Status.ERROR,
            error_message='transient docker hiccup',
        )

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            return self._passing_results()

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
        self.assertEqual(sub.total_tests, 3)
        self.assertEqual(sub.error_message, '')

    def test_retry_on_other_learners_submission_returns_404(self):
        sub = CodingSubmission.objects.create(
            user=self.learner, exercise=self.exercise,
            language='python', code='x',
            status=CodingSubmission.Status.ERROR,
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
            status=CodingSubmission.Status.QUEUED,
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
            status=CodingSubmission.Status.QUEUED,
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

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            return self._passing_results()

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ), self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                url,
                {'language': 'python', 'code': 'def add(a, b): return a + b'},
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

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            return [
                ScriptTestResult('evaluate.T.test_a', 'failed', '', 'AssertionError', 1),
                ScriptTestResult('evaluate.T.test_b', 'failed', '', 'AssertionError', 1),
            ]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ), self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                url,
                {'language': 'python', 'code': 'def add(a, b): return 0'},
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

    # -------------------------------------------------------------------------
    # Instructor authoring run (run code / run tests)
    # -------------------------------------------------------------------------

    def _instructor_run_url(self, exercise=None):
        return reverse(
            'courses:coding-exercise-instructor-run',
            kwargs={'exercise_id': (exercise or self.exercise).id},
        )

    def test_instructor_run_tests_defaults_to_stored_solution_and_script(self):
        self.auth(self.instructor)
        captured = {}

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            captured['code'] = code
            captured['script'] = evaluation_script
            return self._passing_results()

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ):
            response = self.client.post(self._instructor_run_url(), {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn('task_id', response.data['data'])
        self.assertEqual(captured['code'], self.exercise.solution_code)
        self.assertEqual(captured['script'], _EVAL_SCRIPT)

    def test_instructor_run_accepts_unsaved_code_and_script_overrides(self):
        self.auth(self.instructor)
        captured = {}

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            captured['code'] = code
            captured['script'] = evaluation_script
            return self._passing_results()

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ):
            response = self.client.post(
                self._instructor_run_url(),
                {'code': 'def add(a, b): return a + b  # draft',
                 'evaluation_script': 'import unittest\n# draft tests'},
                format='json',
            )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn('# draft', captured['code'])
        self.assertIn('# draft tests', captured['script'])

    def test_instructor_run_code_mode_uses_smoke_script(self):
        from courses.services.code_runner import SMOKE_EVALUATION_SCRIPTS

        self.auth(self.instructor)
        captured = {}

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            captured['script'] = evaluation_script
            return [ScriptTestResult('evaluate.RunCode.test_run_code', 'passed', 'hi\n', '', 1)]

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ):
            response = self.client.post(
                self._instructor_run_url(), {'mode': 'code'}, format='json',
            )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(captured['script'], SMOKE_EVALUATION_SCRIPTS['python'])

    def test_instructor_run_tests_mode_422_when_no_script_anywhere(self):
        exercise = self._make_scriptless_exercise()
        exercise.solution_code = 'def f(): pass'
        exercise.save(update_fields=['solution_code'])
        self.auth(self.instructor)
        response = self.client.post(self._instructor_run_url(exercise), {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_instructor_run_400_when_no_code_anywhere(self):
        exercise = self._make_scriptless_exercise()  # blank solution_code too
        self.auth(self.instructor)
        response = self.client.post(
            self._instructor_run_url(exercise), {'mode': 'code'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_instructor_run_is_learner_forbidden_and_outsider_hidden(self):
        self.auth(self.learner)
        response = self.client.post(self._instructor_run_url(), {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        other_instructor = User.objects.create_user(
            email='cx_other_instructor@example.com', password='pw12345!',
            full_name='Other Instructor', user_type='instructor', is_email_verified=True,
        )
        self.auth(other_instructor)
        response = self.client.post(self._instructor_run_url(), {}, format='json')
        # Not on this course's roster -> 404, never 403 (numeric-ID rule).
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_instructor_can_poll_task_status_endpoint(self):
        """The task-status endpoint is IsEmailVerified-gated (not learner-
        only) so instructors poll the same URL. Eager Celery doesn't store
        results, so the AsyncResult is mocked with a real task payload."""
        from courses.tasks import evaluate_coding_run_task

        self.auth(self.instructor)

        def _fake_run_submission(self_runner, code, evaluation_script, time_limit_ms, language):
            return self._passing_results()

        with patch(
            'courses.services.code_runner.CodeRunner.run_submission',
            new=_fake_run_submission,
        ):
            payload = evaluate_coding_run_task.run(
                self.exercise.id, 'python',
                self.exercise.solution_code, 2000, _EVAL_SCRIPT,
            )

        class _FakeAsyncResult:
            state = 'SUCCESS'

            def __init__(self, *args, **kwargs):
                pass

            def get(self, timeout=None):
                return payload

        with patch('celery.result.AsyncResult', _FakeAsyncResult):
            poll = self.client.get(reverse(
                'courses:learner-coding-task-status', kwargs={'task_id': 'x' * 36},
            ))
        self.assertEqual(poll.status_code, status.HTTP_200_OK)
        self.assertEqual(poll.data['data']['state'], 'SUCCESS')
        self.assertEqual(poll.data['data']['result']['status'], 'passed')
        names = [r['test_name'] for r in poll.data['data']['result']['test_results']]
        self.assertIn('evaluate.AddTests.test_small', names)

    # -------------------------------------------------------------------------
    # Course completeness: evaluation scripts are load-bearing
    # -------------------------------------------------------------------------

    def test_course_cannot_leave_draft_when_exercise_misses_script(self):
        from django.core.exceptions import ValidationError

        scriptless = self._make_scriptless_exercise()
        course = NidusCourse.objects.get(pk=self.course.pk)
        course.status = NidusCourse.CourseStatus.DRAFT
        course.save(update_fields=['status'])

        with self.assertRaises(ValidationError) as ctx:
            course.transition_to(NidusCourse.CourseStatus.UNDER_REVIEW)
        self.assertIn('coding_exercises', ctx.exception.message_dict)
        self.assertIn('No Script Yet', str(ctx.exception.message_dict['coding_exercises']))

        # Filling in the missing script clears the block.
        scriptless.evaluation_script = _EVAL_SCRIPT
        scriptless.save(update_fields=['evaluation_script'])
        course.transition_to(NidusCourse.CourseStatus.UNDER_REVIEW)
        self.assertEqual(course.status, NidusCourse.CourseStatus.UNDER_REVIEW)

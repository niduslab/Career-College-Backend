"""Tests for the four learner dashboard aggregate endpoints.

Fixture note: `courses/signals.py` fires `recalculate_progress` on
WatchProgress post_save, which at 100% schedules certificate issuance via
`transaction.on_commit`. Those callbacks do not run under APITestCase, so
these tests create Certificate rows directly where they need one.
"""

from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import (
    Certificate,
    CourseSchedule,
    CourseSection,
    Enrollment,
    LearnerActivityDay,
    Lecture,
    NidusCourse,
    Quiz,
    QuizAttempt,
    SectionContent,
    VideoAsset,
    WatchProgress,
)
from courses.services.dashboard_service import STREAK_WINDOW_DAYS


class _DashboardFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='dash_instructor@example.com',
            password='pw12345!',
            full_name='Dash Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='dash_learner@example.com',
            password='pw12345!',
            full_name='Dash Learner',
            user_type='learner',
            is_email_verified=True,
        )
        cls.other_learner = User.objects.create_user(
            email='dash_other@example.com',
            password='pw12345!',
            full_name='Dash Other',
            user_type='learner',
            is_email_verified=True,
        )
        cls.unverified_learner = User.objects.create_user(
            email='dash_unverified@example.com',
            password='pw12345!',
            full_name='Dash Unverified',
            user_type='learner',
            is_email_verified=False,
        )

        cls.course = cls._make_course('Dash Course', 'dash-course')
        cls.section = CourseSection.objects.create(
            course=cls.course, title='Module 1', position=1,
        )
        cls.lecture_one = cls._make_lecture('Lecture One', position=1)
        cls.lecture_two = cls._make_lecture('Lecture Two', position=2)

    @classmethod
    def _make_course(cls, title, slug):
        course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title=title,
            slug=slug,
            description='A course used by dashboard tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        course.instructors.add(cls.instructor)
        return course

    @classmethod
    def _make_lecture(cls, title, position, section=None):
        section = section or cls.section
        lecture = Lecture.objects.create(
            section=section, title=title, lecture_type=Lecture.LectureType.VIDEO,
        )
        SectionContent.objects.create(
            section=section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk,
            position=position,
        )
        # A video lecture with no VideoAsset is "awaiting content" (step 1 of
        # two-step authoring) and is hidden from learners, so these fixtures
        # carry a ready asset — they stand in for finished lessons.
        VideoAsset.objects.create(
            lecture=lecture, video_file=f'courses/dash/{lecture.pk}.mp4',
            duration_seconds=1800, is_active=True, status=VideoAsset.Status.READY,
        )
        return lecture

    @classmethod
    def _make_certificate(cls, user, course, issued_at=None):
        issued_at = issued_at or timezone.now()
        enrollment, _ = Enrollment.objects.get_or_create(
            user=user, course=course, schedule=None,
            defaults={'progress_percent': 100, 'completed_at': issued_at},
        )
        return Certificate.objects.create(
            enrollment=enrollment,
            learner_name=user.full_name,
            course_title=course.title,
            issued_at=issued_at,
        )


class LearnerDashboardSummaryTests(_DashboardFixtureMixin, APITestCase):
    @property
    def url(self):
        return reverse('courses:learner-dashboard-summary')

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_instructor_is_forbidden(self):
        self.client.force_authenticate(user=self.instructor)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_unverified_learner_is_forbidden(self):
        self.client.force_authenticate(user=self.unverified_learner)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_learner_with_no_data_gets_zeroes(self):
        self.client.force_authenticate(user=self.learner)
        data = self.client.get(self.url).data['data']

        self.assertEqual(data['courses_enrolled'], 0)
        self.assertEqual(data['courses_completed'], 0)
        self.assertEqual(data['certificates_earned'], 0)
        self.assertEqual(data['total_learning_seconds'], 0)
        self.assertEqual(data['average_progress_percent'], 0.0)
        self.assertEqual(data['day_streak'], 0)

    def test_total_xp_is_absent(self):
        """XP has no ledger to compute from — the key must not appear at all."""
        self.client.force_authenticate(user=self.learner)
        self.assertNotIn('total_xp', self.client.get(self.url).data['data'])

    def test_counts_are_exact(self):
        now = timezone.now()
        second = self._make_course('Dash Course Two', 'dash-course-two')
        third = self._make_course('Dash Course Three', 'dash-course-three')

        Enrollment.objects.create(user=self.learner, course=self.course, progress_percent=40)
        Enrollment.objects.create(user=self.learner, course=second, progress_percent=60)
        self._make_certificate(self.learner, third, now)
        Enrollment.objects.create(user=self.other_learner, course=self.course)

        self.client.force_authenticate(user=self.learner)
        data = self.client.get(self.url).data['data']

        self.assertEqual(data['courses_enrolled'], 3)
        self.assertEqual(data['courses_in_progress'], 2)
        self.assertEqual(data['courses_completed'], 1)
        self.assertEqual(data['certificates_earned'], 1)
        self.assertAlmostEqual(data['average_progress_percent'], 66.7, places=1)

    def test_learning_seconds_sum_watch_progress(self):
        WatchProgress.objects.create(user=self.learner, lecture=self.lecture_one, watched_seconds=1800)
        WatchProgress.objects.create(
            user=self.learner, lecture=self.lecture_two, watched_seconds=1800, is_completed=True,
        )
        WatchProgress.objects.create(user=self.other_learner, lecture=self.lecture_one, watched_seconds=9999)

        self.client.force_authenticate(user=self.learner)
        data = self.client.get(self.url).data['data']

        self.assertEqual(data['total_learning_seconds'], 3600)
        self.assertEqual(data['total_learning_hours'], 1.0)
        self.assertEqual(data['lectures_completed'], 1)

    def test_average_progress_excludes_inactive_enrollments(self):
        second = self._make_course('Dash Inactive', 'dash-inactive')
        Enrollment.objects.create(user=self.learner, course=self.course, progress_percent=80)
        Enrollment.objects.create(
            user=self.learner, course=second, progress_percent=0, is_active=False,
        )

        self.client.force_authenticate(user=self.learner)
        data = self.client.get(self.url).data['data']

        self.assertEqual(data['courses_enrolled'], 1)
        self.assertEqual(data['average_progress_percent'], 80.0)

    def _active_on(self, day_offset):
        """Record an activity day `day_offset` days ago."""
        return LearnerActivityDay.objects.create(
            user=self.learner,
            activity_date=timezone.localdate() - timedelta(days=day_offset),
        )

    def test_streak_counts_consecutive_days(self):
        for offset in (0, 1, 2):
            self._active_on(offset)

        self.client.force_authenticate(user=self.learner)
        data = self.client.get(self.url).data['data']

        self.assertEqual(data['day_streak'], 3)
        # Exact now that it reads an append-only record, not four proxies.
        self.assertFalse(data['day_streak_is_approximate'])
        self.assertTrue(data['day_streak_timezone'])

    def test_streak_is_zero_after_a_gap(self):
        self._active_on(3)

        self.client.force_authenticate(user=self.learner)
        self.assertEqual(self.client.get(self.url).data['data']['day_streak'], 0)

    def test_streak_survives_the_grace_day(self):
        self._active_on(1)

        self.client.force_authenticate(user=self.learner)
        self.assertGreaterEqual(self.client.get(self.url).data['data']['day_streak'], 1)

    def test_streak_ignores_days_outside_the_window(self):
        for offset in (0, 1):
            self._active_on(offset)
        self._active_on(STREAK_WINDOW_DAYS + 5)

        self.client.force_authenticate(user=self.learner)
        self.assertEqual(self.client.get(self.url).data['data']['day_streak'], 2)

    def test_query_count_is_fixed(self):
        """Four aggregates: enrollments, watch progress, certificates, streak.
        Reading the activity ledger replaced a four-query union, so this must
        not creep back up."""
        Enrollment.objects.create(user=self.learner, course=self.course)
        self._active_on(0)

        self.client.force_authenticate(user=self.learner)
        with self.assertNumQueries(4):
            self.client.get(self.url)

    def test_streak_is_scoped_to_the_caller(self):
        LearnerActivityDay.objects.create(
            user=self.other_learner, activity_date=timezone.localdate(),
        )

        self.client.force_authenticate(user=self.learner)
        self.assertEqual(self.client.get(self.url).data['data']['day_streak'], 0)


class LearnerActivityFeedTests(_DashboardFixtureMixin, APITestCase):
    @property
    def url(self):
        return reverse('courses:learner-activity')

    def setUp(self):
        self.client.force_authenticate(user=self.learner)

    def test_permission_triple(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.force_authenticate(user=self.instructor)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.unverified_learner)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_envelope_and_pagination_shape(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertIn('results', response.data['data'])
        self.assertIn('count', response.data['data'])

    def test_ordering_is_descending_across_mixed_sources(self):
        Enrollment.objects.create(user=self.learner, course=self.course)
        WatchProgress.objects.create(
            user=self.learner, lecture=self.lecture_one, watched_seconds=10, is_completed=True,
        )
        self._make_certificate(self.learner, self._make_course('Act Cert', 'act-cert'))

        results = self.client.get(self.url).data['data']['results']
        timestamps = [row['occurred_at'] for row in results]

        self.assertGreaterEqual(len(results), 3)
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_composite_id_and_course_ref(self):
        WatchProgress.objects.create(
            user=self.learner, lecture=self.lecture_one, watched_seconds=10, is_completed=True,
        )
        row = self.client.get(self.url).data['data']['results'][0]

        self.assertTrue(row['id'].startswith('watch:'))
        self.assertEqual(row['type'], 'lecture_completed')
        self.assertEqual(row['course']['slug'], self.course.slug)

    def test_incomplete_watch_progress_is_excluded(self):
        WatchProgress.objects.create(
            user=self.learner, lecture=self.lecture_one, watched_seconds=10, is_completed=False,
        )
        results = self.client.get(self.url).data['data']['results']
        self.assertFalse(any(row['type'] == 'lecture_completed' for row in results))

    def test_another_learners_rows_never_leak(self):
        Enrollment.objects.create(user=self.other_learner, course=self.course)
        self.assertEqual(self.client.get(self.url).data['data']['count'], 0)

    def test_type_filter_narrows_the_source_set(self):
        Enrollment.objects.create(user=self.learner, course=self.course)
        WatchProgress.objects.create(
            user=self.learner, lecture=self.lecture_one, watched_seconds=10, is_completed=True,
        )

        results = self.client.get(self.url, {'type': 'course_enrolled'}).data['data']['results']

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['type'], 'course_enrolled')

    def test_unknown_type_returns_400(self):
        response = self.client.get(self.url, {'type': 'bogus'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('type', response.data['errors'])

    def test_window_is_capped(self):
        quiz = Quiz.objects.create(section=self.section, title='Capped Quiz')
        QuizAttempt.objects.bulk_create(
            [QuizAttempt(user=self.learner, quiz=quiz) for _ in range(250)]
        )
        self.assertEqual(self.client.get(self.url).data['data']['count'], 200)

    def test_query_count_is_fixed(self):
        Enrollment.objects.create(user=self.learner, course=self.course)
        WatchProgress.objects.create(
            user=self.learner, lecture=self.lecture_one, watched_seconds=10, is_completed=True,
        )
        with self.assertNumQueries(6):
            self.client.get(self.url)


class LearnerUpcomingTests(_DashboardFixtureMixin, APITestCase):
    @property
    def url(self):
        return reverse('courses:learner-upcoming')

    def setUp(self):
        self.client.force_authenticate(user=self.learner)
        self.now = timezone.now()

    def _make_schedule(self, course, start_in_days, end_in_days=None):
        return CourseSchedule.objects.create(
            course=course,
            cohort_label='Fall Batch',
            enrollment_opens_at=self.now - timedelta(days=10),
            enrollment_closes_at=self.now - timedelta(days=1),
            start_date=self.now + timedelta(days=start_in_days),
            end_date=self.now + timedelta(days=end_in_days) if end_in_days else None,
            status=CourseSchedule.Status.SCHEDULED,
        )

    def test_cohort_start_inside_the_horizon_appears(self):
        schedule = self._make_schedule(self.course, start_in_days=5)
        Enrollment.objects.create(user=self.learner, course=self.course, schedule=schedule)

        items = self.client.get(self.url).data['data']['items']

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['type'], 'course_starts')
        self.assertEqual(items[0]['subtitle'], 'Fall Batch')

    def test_horizon_is_respected(self):
        schedule = self._make_schedule(self.course, start_in_days=90)
        Enrollment.objects.create(user=self.learner, course=self.course, schedule=schedule)

        self.assertEqual(self.client.get(self.url, {'days': 30}).data['data']['count'], 0)
        self.assertEqual(self.client.get(self.url, {'days': 120}).data['data']['count'], 1)

    def test_days_is_clamped_not_rejected(self):
        response = self.client.get(self.url, {'days': 9999})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['horizon_days'], 365)

    def test_invalid_days_returns_400(self):
        response = self.client.get(self.url, {'days': 'soon'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('days', response.data['errors'])

    def test_future_section_unlock_appears_for_enrolled_courses_only(self):
        CourseSection.objects.create(
            course=self.course,
            title='Module 2',
            position=2,
            unlocks_at=self.now + timedelta(days=3),
        )
        other = self._make_course('Not Enrolled', 'not-enrolled')
        CourseSection.objects.create(
            course=other, title='Hidden', position=1, unlocks_at=self.now + timedelta(days=3),
        )
        Enrollment.objects.create(user=self.learner, course=self.course)

        items = self.client.get(self.url).data['data']['items']

        self.assertEqual([item['type'] for item in items], ['section_unlocks'])
        self.assertEqual(items[0]['title'], 'Module 2')

    def test_dual_enrollment_does_not_duplicate_section_rows(self):
        """A learner may hold both a self-paced and a cohort enrollment for one
        course; without .distinct() the join duplicates every section."""
        CourseSection.objects.create(
            course=self.course,
            title='Module 2',
            position=2,
            unlocks_at=self.now + timedelta(days=3),
        )
        schedule = self._make_schedule(self.course, start_in_days=60)
        Enrollment.objects.create(user=self.learner, course=self.course)
        Enrollment.objects.create(user=self.learner, course=self.course, schedule=schedule)

        items = self.client.get(self.url).data['data']['items']
        unlocks = [item for item in items if item['type'] == 'section_unlocks']

        self.assertEqual(len(unlocks), 1)

    def test_items_are_ascending(self):
        CourseSection.objects.create(
            course=self.course, title='Later', position=2,
            unlocks_at=self.now + timedelta(days=10),
        )
        CourseSection.objects.create(
            course=self.course, title='Sooner', position=3,
            unlocks_at=self.now + timedelta(days=2),
        )
        Enrollment.objects.create(user=self.learner, course=self.course)

        items = self.client.get(self.url).data['data']['items']
        self.assertEqual([item['title'] for item in items], ['Sooner', 'Later'])


class LearnerContinueTests(_DashboardFixtureMixin, APITestCase):
    @property
    def url(self):
        return reverse('courses:learner-continue')

    def setUp(self):
        self.client.force_authenticate(user=self.learner)

    def test_no_enrollments_returns_200_with_null_data(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['data'])

    def test_picks_the_most_recently_accessed_not_the_newest(self):
        now = timezone.now()
        other_course = self._make_course('Recently Accessed', 'recently-accessed')
        Enrollment.objects.create(
            user=self.learner, course=other_course, last_accessed_at=now,
        )
        Enrollment.objects.create(
            user=self.learner, course=self.course, last_accessed_at=now - timedelta(days=2),
        )

        data = self.client.get(self.url).data['data']
        self.assertEqual(data['course']['slug'], other_course.slug)

    def test_next_lecture_is_the_first_incomplete_one(self):
        Enrollment.objects.create(user=self.learner, course=self.course)

        data = self.client.get(self.url).data['data']
        self.assertEqual(data['next_lecture']['lecture_id'], self.lecture_one.pk)
        self.assertEqual(data['next_lecture']['section']['id'], self.section.pk)

    def test_completing_a_lecture_advances_the_target(self):
        Enrollment.objects.create(user=self.learner, course=self.course)
        WatchProgress.objects.create(
            user=self.learner, lecture=self.lecture_one, watched_seconds=10, is_completed=True,
        )

        data = self.client.get(self.url).data['data']
        self.assertEqual(data['next_lecture']['lecture_id'], self.lecture_two.pk)

    def test_all_complete_yields_no_next_lecture(self):
        Enrollment.objects.create(
            user=self.learner, course=self.course,
            progress_percent=100, completed_at=timezone.now(),
        )
        for lecture in (self.lecture_one, self.lecture_two):
            WatchProgress.objects.create(
                user=self.learner, lecture=lecture, watched_seconds=10, is_completed=True,
            )

        data = self.client.get(self.url).data['data']
        self.assertIsNone(data['next_lecture'])
        self.assertTrue(data['is_course_complete'])

    def test_locked_section_reports_locked_until(self):
        locked_at = timezone.now() + timedelta(days=7)
        CourseSection.objects.filter(pk=self.section.pk).update(unlocks_at=locked_at)
        Enrollment.objects.create(user=self.learner, course=self.course)

        data = self.client.get(self.url).data['data']

        self.assertIsNone(data['next_lecture'])
        self.assertIsNotNone(data['locked_until'])

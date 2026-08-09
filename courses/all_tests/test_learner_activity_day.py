"""Tests for the activity-day ledger that backs the streak.

The point of this table is that it records days the old four-table union
could not. The gap tests below are the reason it exists:

  * re-reading an article that is already complete — its "mark as complete"
    button is gone, so nothing wrote before;
  * running a coding exercise without submitting — a Run persists nothing;
  * re-opening any lecture, which used to *overwrite* the historical date
    `WatchProgress.last_watched_at` carried rather than adding a new one.
"""

from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import (
    CodingExercise,
    CourseSection,
    Enrollment,
    LearnerActivityDay,
    Lecture,
    NidusCourse,
    Quiz,
    SectionContent,
)
from courses.services.activity_service import record_learner_activity


class _ActivityFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='act_instructor@example.com',
            password='pw12345!',
            full_name='Act Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='act_learner@example.com',
            password='pw12345!',
            full_name='Act Learner',
            user_type='learner',
            is_email_verified=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Activity Course',
            slug='activity-course',
            description='A course used by activity-ledger tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        cls.course.instructors.add(cls.instructor)

        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section 1', position=1,
        )
        cls.article = cls._add_content(
            Lecture.objects.create(
                section=cls.section,
                title='An article lecture',
                lecture_type=Lecture.LectureType.ARTICLE,
                article_content='Some prose.',
            ),
            SectionContent.ItemType.LECTURE,
            position=1,
        )
        cls.quiz = cls._add_content(
            Quiz.objects.create(section=cls.section, title='A quiz'),
            SectionContent.ItemType.QUIZ,
            position=2,
        )
        cls.exercise = cls._add_content(
            CodingExercise.objects.create(
                section=cls.section,
                title='An exercise',
                description='Add two numbers.',
                language='python',
                starter_code='def add(a, b): ...',
                evaluation_script='assert True',
            ),
            SectionContent.ItemType.CODING,
            position=3,
        )

        cls.enrollment = Enrollment.objects.create(
            user=cls.learner, course=cls.course,
        )

    @classmethod
    def _add_content(cls, obj, item_type, position):
        SectionContent.objects.create(
            section=cls.section,
            item_type=item_type,
            content_type=ContentType.objects.get_for_model(type(obj)),
            object_id=obj.pk,
            position=position,
        )
        return obj

    @staticmethod
    def _days():
        return set(
            LearnerActivityDay.objects.values_list('activity_date', flat=True)
        )


class RecordLearnerActivityTests(_ActivityFixtureMixin, APITestCase):
    def test_records_today(self):
        record_learner_activity(self.learner)

        self.assertEqual(self._days(), {timezone.localdate()})

    def test_is_idempotent_within_a_day(self):
        for _ in range(5):
            record_learner_activity(self.learner)

        self.assertEqual(LearnerActivityDay.objects.count(), 1)

    def test_skips_non_learners(self):
        record_learner_activity(self.instructor)

        self.assertFalse(LearnerActivityDay.objects.exists())

    def test_tolerates_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        record_learner_activity(AnonymousUser())
        record_learner_activity(None)

        self.assertFalse(LearnerActivityDay.objects.exists())

    def test_duplicate_row_violates_the_db_constraint(self):
        today = timezone.localdate()
        LearnerActivityDay.objects.create(user=self.learner, activity_date=today)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LearnerActivityDay.objects.create(
                    user=self.learner, activity_date=today,
                )

    def test_honours_an_explicit_moment(self):
        record_learner_activity(
            self.learner, when=timezone.now() - timedelta(days=2),
        )

        self.assertEqual(
            self._days(), {timezone.localdate() - timedelta(days=2)},
        )


class ActivityRecordedOnConsumptionTests(_ActivityFixtureMixin, APITestCase):
    """Every learner-side read and write must register the day.

    These are the gap regressions — each one is a path that recorded nothing
    under the old four-table union.
    """

    def setUp(self):
        self.client.force_authenticate(user=self.learner)

    def test_opening_a_lecture_records_the_day(self):
        response = self.client.get(
            reverse('courses:learner-lecture-detail',
                    kwargs={'lecture_id': self.article.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._days(), {timezone.localdate()})

    def test_rereading_a_completed_article_still_records(self):
        """The regression this table was built for. Once an article is
        complete the UI hides its only write action, so under the old scheme
        a whole day of re-reading recorded nothing."""
        self.client.post(
            reverse('courses:learner-lecture-progress',
                    kwargs={'lecture_id': self.article.pk}),
            {'watched_seconds': 0, 'is_completed': True},
            format='json',
        )
        LearnerActivityDay.objects.all().delete()

        self.client.get(
            reverse('courses:learner-lecture-detail',
                    kwargs={'lecture_id': self.article.pk})
        )

        self.assertEqual(self._days(), {timezone.localdate()})

    def test_marking_an_article_complete_records_the_day(self):
        response = self.client.post(
            reverse('courses:learner-lecture-progress',
                    kwargs={'lecture_id': self.article.pk}),
            {'watched_seconds': 0, 'is_completed': True},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._days(), {timezone.localdate()})

    def test_opening_a_quiz_records_the_day(self):
        self.client.get(
            reverse('courses:learner-quiz-detail', kwargs={'quiz_id': self.quiz.pk})
        )

        self.assertEqual(self._days(), {timezone.localdate()})

    def test_opening_a_coding_exercise_records_the_day(self):
        self.client.get(
            reverse('courses:learner-coding-exercise-detail',
                    kwargs={'exercise_id': self.exercise.pk})
        )

        self.assertEqual(self._days(), {timezone.localdate()})

    def test_instructor_preview_records_nothing(self):
        """Previewing own content must not build a streak."""
        self.client.force_authenticate(user=self.instructor)

        self.client.get(
            reverse('courses:learner-lecture-detail',
                    kwargs={'lecture_id': self.article.pk})
        )

        self.assertFalse(LearnerActivityDay.objects.exists())

    def test_dashboard_and_catalog_record_nothing(self):
        """Browsing is not studying — only course content counts."""
        self.client.get(reverse('courses:learner-dashboard-summary'))
        self.client.get(reverse('courses:my-courses-list'))
        self.client.get(reverse('courses:catalog-list'))

        self.assertFalse(LearnerActivityDay.objects.exists())

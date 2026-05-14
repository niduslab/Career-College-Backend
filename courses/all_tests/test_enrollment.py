from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import (
    CourseSection,
    Enrollment,
    Lecture,
    NidusCourse,
    SectionContent,
    WatchProgress,
)


class EnrollmentAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='enroll_instructor@example.com',
            password='pw12345!',
            full_name='Enrollment Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='enroll_learner@example.com',
            password='pw12345!',
            full_name='Enrollment Learner',
            user_type='learner',
            is_email_verified=True,
        )
        cls.other_learner = User.objects.create_user(
            email='other_learner@example.com',
            password='pw12345!',
            full_name='Other Learner',
            user_type='learner',
            is_email_verified=True,
        )
        cls.unverified_learner = User.objects.create_user(
            email='unverified_learner@example.com',
            password='pw12345!',
            full_name='Unverified Learner',
            user_type='learner',
            is_email_verified=False,
        )

        cls.published_course = cls._make_course(
            title='Published Enrollment Course',
            slug='published-enrollment-course',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        cls.draft_course = cls._make_course(
            title='Draft Enrollment Course',
            slug='draft-enrollment-course',
            status=NidusCourse.CourseStatus.DRAFT,
        )
        cls.paid_course = cls._make_course(
            title='Paid MVP Course',
            slug='paid-mvp-course',
            status=NidusCourse.CourseStatus.PUBLISHED,
            price='49.00',
        )

    @classmethod
    def _make_course(cls, title, slug, status, price='0.00'):
        course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title=title,
            slug=slug,
            description='A course used by enrollment tests.',
            status=status,
            price=price,
        )
        course.instructors.add(cls.instructor)
        return course

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.learner)

    def test_catalog_lists_only_published_courses(self):
        response = self.client.get(reverse('courses:catalog-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = {row['title'] for row in response.data['data']['results']}
        self.assertIn(self.published_course.title, titles)
        self.assertIn(self.paid_course.title, titles)
        self.assertNotIn(self.draft_course.title, titles)

    def test_learner_can_enroll_in_published_course(self):
        self.auth()

        response = self.client.post(
            reverse('courses:course-enroll', kwargs={'slug': self.published_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])
        self.assertTrue(
            Enrollment.objects.filter(
                user=self.learner,
                course=self.published_course,
                is_active=True,
            ).exists()
        )

    def test_paid_course_enrolls_as_free_until_payment_integration_exists(self):
        self.auth()

        response = self.client.post(
            reverse('courses:course-enroll', kwargs={'slug': self.paid_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['enrollment_type'], Enrollment.EnrollmentType.FREE)

    def test_duplicate_enroll_returns_422(self):
        self.auth()
        url = reverse('courses:course-enroll', kwargs={'slug': self.published_course.slug})
        self.client.post(url)

        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertFalse(response.data['success'])

    def test_unenroll_soft_deactivates_and_reenroll_reactivates_same_row(self):
        self.auth()
        enroll_url = reverse('courses:course-enroll', kwargs={'slug': self.published_course.slug})
        unenroll_url = reverse('courses:course-unenroll', kwargs={'slug': self.published_course.slug})
        self.client.post(enroll_url)
        enrollment = Enrollment.objects.get(user=self.learner, course=self.published_course)

        response = self.client.post(unenroll_url)
        enrollment.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(enrollment.is_active)

        response = self.client.post(enroll_url)
        enrollment.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(enrollment.is_active)
        self.assertEqual(
            Enrollment.objects.filter(user=self.learner, course=self.published_course).count(),
            1,
        )

    def test_non_learner_cannot_enroll(self):
        self.auth(self.instructor)

        response = self.client.post(
            reverse('courses:course-enroll', kwargs={'slug': self.published_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unverified_learner_cannot_enroll(self):
        self.auth(self.unverified_learner)

        response = self.client.post(
            reverse('courses:course-enroll', kwargs={'slug': self.published_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_my_courses_lists_only_current_users_active_enrollments(self):
        Enrollment.objects.create(user=self.learner, course=self.published_course)
        Enrollment.objects.create(user=self.learner, course=self.paid_course, is_active=False)
        Enrollment.objects.create(user=self.other_learner, course=self.paid_course)
        self.auth()

        response = self.client.get(reverse('courses:my-courses-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = {row['course']['title'] for row in response.data['data']['results']}
        self.assertEqual(titles, {self.published_course.title})

    def test_my_course_detail_updates_last_accessed_in_response_and_database(self):
        Enrollment.objects.create(user=self.learner, course=self.published_course)
        self.auth()

        response = self.client.get(
            reverse('courses:my-courses-detail', kwargs={'slug': self.published_course.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['data']['enrollment']['last_accessed_at'])
        enrollment = Enrollment.objects.get(user=self.learner, course=self.published_course)
        self.assertIsNotNone(enrollment.last_accessed_at)

    def test_watch_progress_recalculates_active_enrollment_progress(self):
        enrollment = Enrollment.objects.create(user=self.learner, course=self.published_course)
        section = CourseSection.objects.create(
            course=self.published_course,
            title='Progress Section',
            position=1,
        )
        lecture = Lecture.objects.create(
            section=section,
            title='Progress Lecture',
            content_type=Lecture.ContentType.ARTICLE,
            article_content='Complete me.',
        )
        SectionContent.objects.create(
            section=section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk,
            position=1,
        )

        WatchProgress.objects.create(
            user=self.learner,
            lecture=lecture,
            watched_seconds=10,
            is_completed=True,
        )

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.progress_percent, 100)
        self.assertIsNotNone(enrollment.completed_at)

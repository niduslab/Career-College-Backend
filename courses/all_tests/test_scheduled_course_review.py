"""
GET /{pk}/review/ — admin review-context endpoint (CourseAdminReviewView.get).

Exposes delivery_mode, attached schedules, and outline stats so a platform
admin can judge a cohort course's readiness before approving.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, User
from courses.models import CourseSchedule, CourseSection, NidusCourse


class ScheduledCourseAdminReviewGetTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='review_instructor@example.com', password='pw12345!',
            full_name='Review Instructor', user_type='instructor',
            is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.instructor).update(is_verified=True)
        cls.admin = User.objects.create_user(
            email='review_admin@example.com', password='pw12345!',
            full_name='Review Admin', user_type='admin',
            is_email_verified=True, is_staff=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Cohort Review Course',
            description='A cohort course pending review.',
            delivery_mode=NidusCourse.DeliveryMode.SCHEDULED,
        )
        cls.course.instructors.add(cls.instructor)
        cls.section_with_content = CourseSection.objects.create(
            course=cls.course, title='Week 1', position=1,
        )
        cls.section_outline_only = CourseSection.objects.create(
            course=cls.course, title='Week 2 (coming soon)', position=2,
        )

        now = timezone.now()
        start = now + timedelta(days=30)
        cls.schedule = CourseSchedule.objects.create(
            course=cls.course,
            cohort_label='Spring 2027 Batch',
            enrollment_opens_at=now,
            enrollment_closes_at=start - timedelta(days=1),
            start_date=start,
            end_date=start + timedelta(days=60),
        )

    def _review_url(self):
        return reverse('courses:course-review', kwargs={'pk': self.course.pk})

    def test_admin_get_returns_delivery_mode_schedules_and_outline_stats(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self._review_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        data = response.data['data']

        self.assertEqual(data['delivery_mode'], 'scheduled')

        self.assertEqual(len(data['schedules']), 1)
        self.assertEqual(data['schedules'][0]['id'], self.schedule.pk)
        self.assertEqual(data['schedules'][0]['cohort_label'], 'Spring 2027 Batch')

        stats = data['outline_stats']
        self.assertEqual(stats['total_sections'], 2)
        self.assertEqual(stats['sections_with_content'], 0)
        self.assertIn('Week 1', stats['empty_section_titles'])
        self.assertIn('Week 2 (coming soon)', stats['empty_section_titles'])

    def test_non_admin_instructor_forbidden(self):
        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_returns_401(self):
        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unknown_course_returns_404(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('courses:course-review', kwargs={'pk': self.course.pk + 99999})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

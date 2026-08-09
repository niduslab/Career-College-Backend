from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import Certificate, Enrollment, NidusCourse


class MyCertificateListTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='cert_instructor@example.com',
            password='pw12345!',
            full_name='Cert Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='cert_learner@example.com',
            password='pw12345!',
            full_name='Cert Learner',
            user_type='learner',
            is_email_verified=True,
        )
        cls.other_learner = User.objects.create_user(
            email='cert_other@example.com',
            password='pw12345!',
            full_name='Cert Other',
            user_type='learner',
            is_email_verified=True,
        )
        cls.unverified_learner = User.objects.create_user(
            email='cert_unverified@example.com',
            password='pw12345!',
            full_name='Cert Unverified',
            user_type='learner',
            is_email_verified=False,
        )

        cls.course_a = cls._make_course('Cert Course A', 'cert-course-a')
        cls.course_b = cls._make_course('Cert Course B', 'cert-course-b')

    @classmethod
    def _make_course(cls, title, slug):
        course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title=title,
            slug=slug,
            description='A course used by certificate tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
        )
        course.instructors.add(cls.instructor)
        return course

    @staticmethod
    def _issue(user, course, issued_at):
        """Create the Certificate row directly.

        The signal path (WatchProgress → recalculate_progress → on_commit)
        does not fire under APITestCase, and these tests are about the list
        endpoint, not issuance.
        """
        enrollment = Enrollment.objects.create(
            user=user, course=course, completed_at=issued_at, progress_percent=100,
        )
        return Certificate.objects.create(
            enrollment=enrollment,
            learner_name=user.full_name,
            course_title=course.title,
            issued_at=issued_at,
        )

    @property
    def url(self):
        return reverse('courses:my-certificates-list')

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_instructor_is_forbidden(self):
        self.client.force_authenticate(user=self.instructor)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_unverified_learner_is_forbidden(self):
        self.client.force_authenticate(user=self.unverified_learner)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_empty_list_for_a_learner_with_none(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['count'], 0)

    def test_only_own_certificates_are_listed(self):
        now = timezone.now()
        self._issue(self.learner, self.course_a, now)
        self._issue(self.other_learner, self.course_b, now)

        self.client.force_authenticate(user=self.learner)
        response = self.client.get(self.url)

        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['course']['slug'], self.course_a.slug)

    def test_ordering_is_newest_first(self):
        now = timezone.now()
        older = self._issue(self.learner, self.course_a, now - timezone.timedelta(days=5))
        newer = self._issue(self.learner, self.course_b, now)

        self.client.force_authenticate(user=self.learner)
        response = self.client.get(self.url)

        self.assertEqual(
            [row['certificate_uid'] for row in response.data['data']['results']],
            [str(newer.certificate_uid), str(older.certificate_uid)],
        )

    def test_download_and_verify_urls_resolve_and_respond(self):
        certificate = self._issue(self.learner, self.course_a, timezone.now())

        self.client.force_authenticate(user=self.learner)
        row = self.client.get(self.url).data['data']['results'][0]

        self.assertEqual(
            row['download_url'],
            reverse(
                'courses:certificate-download',
                kwargs={'certificate_uid': str(certificate.certificate_uid)},
            ),
        )
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(row['verify_url']).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(row['download_url']).status_code, status.HTTP_200_OK)

    def test_snapshot_title_and_live_title_can_differ(self):
        self._issue(self.learner, self.course_a, timezone.now())
        self.course_a.title = 'Renamed After Issue'
        self.course_a.save(update_fields=['title'])

        self.client.force_authenticate(user=self.learner)
        row = self.client.get(self.url).data['data']['results'][0]

        self.assertEqual(row['course_title'], 'Cert Course A')
        self.assertEqual(row['course']['title'], 'Renamed After Issue')

    def test_list_has_no_n_plus_one(self):
        now = timezone.now()
        self._issue(self.learner, self.course_a, now)
        self._issue(self.learner, self.course_b, now)

        self.client.force_authenticate(user=self.learner)
        with self.assertNumQueries(2):
            self.client.get(self.url)

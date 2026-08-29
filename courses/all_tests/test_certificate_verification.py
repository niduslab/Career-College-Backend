from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from admin_console.all_models.user_admin_models import AdminActionLog
from authentication.models import User
from courses.models import Certificate, Enrollment, NidusCourse
from courses.services.certificate_service import issue_certificate


class CertificateVerificationTests(APITestCase):
    """Public verification by ID or UUID, plus admin revoke / restore."""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='ver_instructor@example.com', password='pw12345!',
            full_name='Ada Lovelace', user_type='instructor', is_email_verified=True,
        )
        cls.learner = User.objects.create_user(
            email='ver_learner@example.com', password='pw12345!',
            full_name='Grace Hopper', user_type='learner', is_email_verified=True,
        )
        cls.admin = User.objects.create_user(
            email='ver_admin@example.com', password='pw12345!',
            full_name='Platform Admin', user_type='admin', is_email_verified=True,
            is_staff=True,
        )

        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor, title='Next.js Development',
            slug='nextjs-verification', description='Verification tests.',
            status=NidusCourse.CourseStatus.PUBLISHED, learning_hours=120,
        )
        cls.course.instructors.add(cls.instructor)

    def setUp(self):
        enrollment = Enrollment.objects.create(
            user=self.learner, course=self.course,
            completed_at=timezone.now(), progress_percent=100,
        )
        self.certificate = issue_certificate(enrollment)

    def _public_verify_url(self, identifier):
        return reverse('courses:certificate-public-verify',
                       kwargs={'identifier': str(identifier)})

    def _revoke_url(self, certificate=None):
        return reverse('courses:certificate-revoke', kwargs={
            'certificate_uid': str((certificate or self.certificate).certificate_uid)})

    def _restore_url(self, certificate=None):
        return reverse('courses:certificate-restore', kwargs={
            'certificate_uid': str((certificate or self.certificate).certificate_uid)})

# Public verification

    def test_verify_by_certificate_id_is_public(self):
        response = self.client.get(
            self._public_verify_url(self.certificate.certificate_id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['certificate_id'],
                         self.certificate.certificate_id)
        self.assertEqual(data['status'], 'valid')
        self.assertEqual(data['student']['name'], 'Grace Hopper')
        self.assertEqual(data['course']['name'], 'Next.js Development')
        self.assertEqual(data['course']['learning_hours'], 120)
        self.assertEqual(data['instructor']['name'], 'Ada Lovelace')
        self.assertIn('verification_url', data)

    def test_verify_by_uuid_resolves_the_same_row(self):
        by_id = self.client.get(self._public_verify_url(
            self.certificate.certificate_id))
        by_uid = self.client.get(self._public_verify_url(
            self.certificate.certificate_uid))

        self.assertEqual(by_uid.status_code, status.HTTP_200_OK)
        self.assertEqual(by_uid.data['data']['certificate_uid'],
                         by_id.data['data']['certificate_uid'])

    def test_unknown_identifier_is_404(self):
        for identifier in ('CC-2026-NOPE-999999', '00000000-0000-0000-0000-000000000000'):
            with self.subTest(identifier=identifier):
                response = self.client.get(self._public_verify_url(identifier))
                self.assertEqual(response.status_code,
                                 status.HTTP_404_NOT_FOUND)
                self.assertEqual(
                    response.data['message'], 'Certificate not found.')

    def test_verification_never_exposes_learner_email(self):
        response = self.client.get(
            self._public_verify_url(self.certificate.certificate_id))
        self.assertNotIn(self.learner.email, str(response.data))

    def test_legacy_uuid_verify_route_still_works(self):
        response = self.client.get(reverse(
            'courses:certificate-verify',
            kwargs={'certificate_uid': str(self.certificate.certificate_uid)},
        ))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], 'valid')

# Revocation

    def test_revoke_requires_admin(self):
        self.assertEqual(
            self.client.post(self._revoke_url()).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.client.force_authenticate(user=self.learner)
        self.assertEqual(
            self.client.post(self._revoke_url()).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_revoke_and_verification_reports_it(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            self._revoke_url(), {'reason': 'Issued in error.'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.certificate.refresh_from_db()
        self.assertEqual(self.certificate.status, Certificate.Status.REVOKED)
        self.assertIsNotNone(self.certificate.revoked_at)
        self.assertEqual(self.certificate.revoked_reason, 'Issued in error.')

        self.client.force_authenticate(user=None)
        public = self.client.get(self._public_verify_url(
            self.certificate.certificate_id))
        self.assertEqual(public.status_code, status.HTTP_200_OK)
        self.assertEqual(public.data['data']['status'], 'revoked')
        self.assertIn('revoked', public.data['message'].lower())

    def test_revocation_preserves_the_issued_snapshot(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self._revoke_url(), {'reason': 'Issued in error.'})

        self.certificate.refresh_from_db()
        self.assertEqual(self.certificate.certificate_id,
                         Certificate.objects.get(pk=self.certificate.pk).certificate_id)
        self.assertEqual(self.certificate.learner_name, 'Grace Hopper')
        self.assertEqual(self.certificate.instructor_name, 'Ada Lovelace')

    def test_double_revoke_is_422(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self._revoke_url())
        response = self.client.post(self._revoke_url())

        self.assertEqual(response.status_code,
                         status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_restore_lifts_the_revocation(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self._revoke_url())
        response = self.client.post(self._restore_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.certificate.refresh_from_db()
        self.assertEqual(self.certificate.status, Certificate.Status.VALID)
        self.assertIsNone(self.certificate.revoked_at)
        self.assertEqual(self.certificate.revoked_reason, '')

    def test_restoring_a_valid_certificate_is_422(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self._restore_url())

        self.assertEqual(response.status_code,
                         status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_revoke_writes_an_audit_row(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self._revoke_url(), {'reason': 'Fraud.'})

        log = AdminActionLog.objects.get(action='certificate_revoke')
        self.assertEqual(log.actor_id, self.admin.id)
        self.assertEqual(log.target_user_id, self.learner.id)
        self.assertEqual(log.reason, 'Fraud.')
        self.assertEqual(log.metadata['certificate_id'],
                         self.certificate.certificate_id)

    def test_revoking_an_unknown_certificate_is_404(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(reverse(
            'courses:certificate-revoke',
            kwargs={'certificate_uid': '00000000-0000-0000-0000-000000000000'},
        ))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

# PDF

    def test_pdf_download_renders_with_snapshot_data(self):
        response = self.client.get(reverse(
            'courses:certificate-download',
            kwargs={'certificate_uid': str(self.certificate.certificate_uid)},
        ))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn(self.certificate.certificate_id,
                      response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF-'))

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from admin_console.all_models.platform_settings_models import PlatformSettings
from authentication.models import PartnerInstitutionProfile, User
from courses.models import Certificate, Enrollment, NidusCourse
from courses.services.certificate_service import (
    CertificateError,
    _slug_abbrev,
    build_verification_url,
    issue_certificate,
)

# 1×1 transparent PNG — smallest valid image the ImageField will accept.
_PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d494844520000000100000001080600000'
    '01f15c4890000000a49444154789c6300010000050001'
    '0d0a2db40000000049454e44ae426082'
)


def _png(name='sig.png'):
    return SimpleUploadedFile(name, _PNG_BYTES, content_type='image/png')


class CertificateIssuanceTests(TestCase):
    """Issuance: eligibility, snapshotting, ID allocation, signatory fallback."""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='iss_instructor@example.com', password='pw12345!',
            full_name='Ada Lovelace', user_type='instructor', is_email_verified=True,
        )
        profile = cls.instructor.instructor_profile
        profile.current_title = 'Lead Instructor'
        profile.signature = _png('ada.png')
        profile.save()

        cls.learner = User.objects.create_user(
            email='iss_learner@example.com', password='pw12345!',
            full_name='Grace Hopper', user_type='learner', is_email_verified=True,
        )

    def setUp(self):
        settings_obj = PlatformSettings.load()
        settings_obj.organization_name = 'Career College'
        settings_obj.authorized_signatory_name = 'John Doe'
        settings_obj.authorized_signatory_designation = 'Academic Director'
        settings_obj.authorized_signature = _png('john.png')
        settings_obj.save()

    def _course(self, title='Next.js Development', slug='nextjs-development', **kwargs):
        course = NidusCourse.objects.create(
            created_by=self.instructor, title=title, slug=slug,
            description='Course used by issuance tests.',
            status=NidusCourse.CourseStatus.PUBLISHED,
            learning_hours=kwargs.pop('learning_hours', 120),
            **kwargs,
        )
        course.instructors.add(self.instructor)
        return course

    def _completed_enrollment(self, course, user=None):
        return Enrollment.objects.create(
            user=user or self.learner, course=course,
            completed_at=timezone.now(), progress_percent=100,
        )

    # ── Eligibility ──────────────────────────────────────────────────────────

    def test_incomplete_enrollment_cannot_be_issued(self):
        enrollment = Enrollment.objects.create(
            user=self.learner, course=self._course(), progress_percent=40,
        )
        with self.assertRaises(CertificateError) as ctx:
            issue_certificate(enrollment)
        self.assertEqual(ctx.exception.http_status, 422)
        self.assertFalse(Certificate.objects.exists())

    # ── Certificate ID ───────────────────────────────────────────────────────

    def test_certificate_id_format(self):
        certificate = issue_certificate(self._completed_enrollment(self._course()))
        year = certificate.issued_at.year
        self.assertEqual(certificate.certificate_id, f'CC-{year}-NEXTJS-000001')

    def test_ids_are_sequential_within_a_course(self):
        course = self._course()
        first = issue_certificate(self._completed_enrollment(course))
        second_learner = User.objects.create_user(
            email='iss_learner2@example.com', password='pw12345!',
            full_name='Alan Turing', user_type='learner', is_email_verified=True,
        )
        second = issue_certificate(self._completed_enrollment(course, second_learner))

        self.assertNotEqual(first.certificate_id, second.certificate_id)
        self.assertTrue(second.certificate_id.endswith('000002'))

    def test_issuance_is_idempotent(self):
        enrollment = self._completed_enrollment(self._course())
        first = issue_certificate(enrollment)
        second = issue_certificate(enrollment)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.certificate_id, second.certificate_id)
        self.assertEqual(Certificate.objects.count(), 1)

    def test_slug_abbrev_falls_back_for_unusable_slug(self):
        self.assertEqual(_slug_abbrev(type('C', (), {'slug': '---'})()), 'GEN')

    # ── Snapshot ─────────────────────────────────────────────────────────────

    def test_snapshot_captures_course_and_instructor(self):
        certificate = issue_certificate(self._completed_enrollment(self._course()))

        self.assertEqual(certificate.learner_name, 'Grace Hopper')
        self.assertEqual(certificate.course_title, 'Next.js Development')
        self.assertEqual(certificate.learning_hours, 120)
        self.assertEqual(certificate.instructor_name, 'Ada Lovelace')
        self.assertEqual(certificate.instructor_designation, 'Lead Instructor')
        self.assertTrue(certificate.instructor_signature)
        self.assertIsNotNone(certificate.completion_date)
        self.assertEqual(certificate.status, Certificate.Status.VALID)

    def test_signature_files_are_copied_not_referenced(self):
        certificate = issue_certificate(self._completed_enrollment(self._course()))
        profile = self.instructor.instructor_profile

        self.assertNotEqual(certificate.instructor_signature.name, profile.signature.name)
        self.assertIn('certificates/signatures', certificate.instructor_signature.name)

    def test_later_signature_change_does_not_alter_issued_certificate(self):
        """The headline requirement: an issued certificate is frozen."""
        certificate = issue_certificate(self._completed_enrollment(self._course()))
        original_sig = certificate.instructor_signature.name
        original_designation = certificate.instructor_designation
        original_signatory = certificate.authorized_signatory_name

        profile = self.instructor.instructor_profile
        profile.signature = _png('ada-new.png')
        profile.current_title = 'Retired'
        profile.save()

        platform = PlatformSettings.load()
        platform.authorized_signatory_name = 'Someone Else'
        platform.save()

        certificate.refresh_from_db()
        self.assertEqual(certificate.instructor_signature.name, original_sig)
        self.assertEqual(certificate.instructor_designation, original_designation)
        self.assertEqual(certificate.authorized_signatory_name, original_signatory)
        # The copied file is still readable at its original key.
        certificate.instructor_signature.open('rb')
        try:
            self.assertTrue(certificate.instructor_signature.read())
        finally:
            certificate.instructor_signature.close()

    # ── Signatory fallback chain ─────────────────────────────────────────────

    def test_individual_course_falls_back_to_platform_signatory(self):
        certificate = issue_certificate(self._completed_enrollment(self._course()))

        self.assertEqual(certificate.authorized_signatory_name, 'John Doe')
        self.assertEqual(certificate.authorized_signatory_designation, 'Academic Director')
        self.assertEqual(certificate.issuer_name, 'Career College')
        self.assertTrue(certificate.authorized_signature)

    def test_institution_course_uses_its_own_signatory(self):
        institution_user = User.objects.create_user(
            email='iss_inst@example.com', password='pw12345!',
            full_name='Tech Institute', user_type='partner_institution',
            is_email_verified=True,
        )
        institution = PartnerInstitutionProfile.objects.get(user=institution_user)
        institution.institution_name = 'Tech Institute'
        institution.authorized_signatory_name = 'Marie Curie'
        institution.authorized_signatory_designation = 'Dean'
        institution.authorized_signature = _png('marie.png')
        institution.save()

        course = self._course(slug='inst-course', partner_institution=institution)
        certificate = issue_certificate(self._completed_enrollment(course))

        self.assertEqual(certificate.authorized_signatory_name, 'Marie Curie')
        self.assertEqual(certificate.authorized_signatory_designation, 'Dean')
        self.assertEqual(certificate.issuer_name, 'Tech Institute')

    def test_issuance_succeeds_with_no_signatory_configured(self):
        """A missing signature must never cost the learner their certificate."""
        platform = PlatformSettings.load()
        platform.authorized_signatory_name = ''
        platform.authorized_signatory_designation = ''
        platform.authorized_signature = None
        platform.save()

        profile = self.instructor.instructor_profile
        profile.signature = None
        profile.save()

        certificate = issue_certificate(self._completed_enrollment(self._course()))

        self.assertEqual(certificate.authorized_signatory_name, '')
        self.assertFalse(certificate.instructor_signature)
        self.assertTrue(certificate.certificate_id)

    # ── Verification URL ─────────────────────────────────────────────────────

    def test_verification_url_uses_certificate_id(self):
        certificate = issue_certificate(self._completed_enrollment(self._course()))
        url = build_verification_url(certificate)

        self.assertIn('/verify/', url)
        self.assertTrue(url.endswith(certificate.certificate_id))

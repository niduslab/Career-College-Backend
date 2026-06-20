"""
End-to-end tests for the partner-institution Phase-1 feature set:

  1. Institution identity verification (draft → submit → admin approve/reject).
  2. Expert auto-provisioning + management.
  3. Partner course creation (clean() unblock).
  4. Direct instructor (expert) assignment to a course.
"""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, PartnerInstitutionProfile, User
from courses.models import NidusCourse
from id_verification.models import InstitutionVerification


def _pdf_upload(name='accreditation.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 fake pdf bytes', content_type='application/pdf')


class PartnerInstitutionTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institution_user = User.objects.create_user(
            email='inst@example.com', password='pw12345!',
            full_name='Acme Institute', user_type='partner_institution',
            is_email_verified=True,
        )
        cls.institution = cls.institution_user.partner_institution_profile

        cls.other_institution_user = User.objects.create_user(
            email='other@example.com', password='pw12345!',
            full_name='Other Institute', user_type='partner_institution',
            is_email_verified=True,
        )
        cls.other_institution = cls.other_institution_user.partner_institution_profile

        cls.admin = User.objects.create_user(
            email='admin@example.com', password='pw12345!',
            full_name='Admin User', user_type='admin', is_email_verified=True,
            is_staff=True,
        )

    def _verify_institution(self, institution=None):
        institution = institution or self.institution
        institution.is_verified = True
        institution.is_active = True
        institution.save(update_fields=['is_verified', 'is_active'])


class InstitutionVerificationFlowTests(PartnerInstitutionTestBase):
    def test_create_submit_approve_marks_verified(self):
        self.client.force_authenticate(self.institution_user)

        # Create draft with documents.
        resp = self.client.post(
            reverse('id_verification:institution-verification-create'),
            {
                'registration_number': 'REG-123',
                'issuing_authority': 'Ministry of Education',
                'accreditation_document': _pdf_upload(),
            },
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        verification_id = resp.data['data']['id']

        # Submit.
        with patch('id_verification.all_views.institution_views.transaction.on_commit',
                   side_effect=lambda fn: fn()), \
             patch('notifications.services.dispatcher.dispatch'):
            resp = self.client.post(reverse(
                'id_verification:institution-verification-submit',
                kwargs={'pk': verification_id},
            ))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['data']['status'], 'submitted')

        # Admin picks up + approves.
        self.client.force_authenticate(self.admin)
        review_url = reverse(
            'id_verification:admin-institution-verification-review',
            kwargs={'pk': verification_id},
        )
        self.client.post(review_url, {'action': 'pick_up'}, format='json')
        with patch('id_verification.all_views.admin_views.transaction.on_commit',
                   side_effect=lambda fn: fn()), \
             patch('notifications.services.dispatcher.dispatch'):
            resp = self.client.post(review_url, {'action': 'approve'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        self.institution.refresh_from_db()
        self.assertTrue(self.institution.is_verified)

    def test_submit_incomplete_fails(self):
        self.client.force_authenticate(self.institution_user)
        v = InstitutionVerification.objects.create(institution=self.institution)
        resp = self.client.post(reverse(
            'id_verification:institution-verification-submit', kwargs={'pk': v.pk},
        ))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_requires_reason(self):
        v = InstitutionVerification.objects.create(
            institution=self.institution,
            registration_number='R', issuing_authority='A',
            accreditation_document=_pdf_upload(), status='under_review',
        )
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            reverse('id_verification:admin-institution-verification-review', kwargs={'pk': v.pk}),
            {'action': 'reject'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_draft_verification(self):
        self.client.force_authenticate(self.institution_user)
        v = InstitutionVerification.objects.create(
            institution=self.institution, registration_number='OLD',
        )
        resp = self.client.patch(
            reverse('id_verification:institution-verification-update', kwargs={'pk': v.pk}),
            {'registration_number': 'NEW-REG'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        v.refresh_from_db()
        self.assertEqual(v.registration_number, 'NEW-REG')

    def test_expire_action_invalid_for_institution(self):
        v = InstitutionVerification.objects.create(
            institution=self.institution, registration_number='R',
            issuing_authority='A', accreditation_document=_pdf_upload(),
            status='under_review',
        )
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            reverse('id_verification:admin-institution-verification-review', kwargs={'pk': v.pk}),
            {'action': 'expire'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_learner_cannot_access_institution_verification(self):
        learner = User.objects.create_user(
            email='l@example.com', password='pw12345!', full_name='L',
            user_type='learner', is_email_verified=True,
        )
        self.client.force_authenticate(learner)
        resp = self.client.post(reverse('id_verification:institution-verification-create'), {})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class ExpertManagementTests(PartnerInstitutionTestBase):
    def setUp(self):
        self._verify_institution()
        self.client.force_authenticate(self.institution_user)

    def test_provision_expert_creates_affiliated_instructor(self):
        with patch('authentication.utils.send_otp_email') as mock_mail, \
             patch('authentication.services.expert_service.transaction.on_commit',
                   side_effect=lambda fn: fn()), \
             patch('notifications.services.dispatcher.dispatch'):
            resp = self.client.post(
                reverse('authentication:institution-expert-list-create'),
                {'full_name': 'Jane Expert', 'email': 'jane@example.com',
                 'specialization': ['NLP']},
                format='json',
            )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        mock_mail.assert_called_once()

        expert = User.objects.get(email='jane@example.com')
        self.assertEqual(expert.user_type, 'instructor')
        profile = expert.instructor_profile
        self.assertEqual(profile.affiliated_institution_id, self.institution.id)
        self.assertEqual(profile.onboarding_source, 'institution')
        self.assertEqual(profile.affiliation_status, 'active')
        self.assertTrue(profile.is_verified)

    def test_duplicate_email_rejected(self):
        User.objects.create_user(
            email='dup@example.com', password='pw', full_name='Dup',
            user_type='learner', is_email_verified=True,
        )
        resp = self.client.post(
            reverse('authentication:institution-expert-list-create'),
            {'full_name': 'Dup Two', 'email': 'dup@example.com'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_unverified_institution_blocked(self):
        self.institution.is_verified = False
        self.institution.save(update_fields=['is_verified'])
        resp = self.client.get(reverse('authentication:institution-expert-list-create'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_see_other_institutions_expert(self):
        foreign = User.objects.create_user(
            email='foreign@example.com', password='pw', full_name='Foreign Expert',
            user_type='instructor', is_email_verified=True,
        )
        fp = foreign.instructor_profile
        fp.affiliated_institution = self.other_institution
        fp.affiliation_status = 'active'
        fp.save()
        resp = self.client.get(reverse(
            'authentication:institution-expert-detail', kwargs={'expert_id': fp.pk},
        ))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_deactivate_expert_blocks_authoring(self):
        with patch('authentication.utils.send_otp_email'), \
             patch('authentication.services.expert_service.transaction.on_commit',
                   side_effect=lambda fn: fn()), \
             patch('notifications.services.dispatcher.dispatch'):
            self.client.post(
                reverse('authentication:institution-expert-list-create'),
                {'full_name': 'Temp Expert', 'email': 'temp@example.com'}, format='json',
            )
        profile = User.objects.get(email='temp@example.com').instructor_profile
        resp = self.client.patch(
            reverse('authentication:institution-expert-detail', kwargs={'expert_id': profile.pk}),
            {'is_active': False}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        profile.refresh_from_db()
        self.assertEqual(profile.affiliation_status, 'removed')
        self.assertFalse(profile.is_verified)


class PartnerCourseCreationTests(PartnerInstitutionTestBase):
    def setUp(self):
        self._verify_institution()
        self.client.force_authenticate(self.institution_user)

    def test_partner_can_create_course(self):
        resp = self.client.post(
            reverse('courses:course-create'),
            {'title': 'Institution Course', 'description': 'A self-paced course.'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        course = NidusCourse.objects.get(created_by=self.institution_user)
        self.assertEqual(course.partner_institution_id, self.institution.id)

    def test_clean_allows_partner_institution(self):
        course = NidusCourse(
            created_by=self.institution_user,
            title='Direct Clean Test',
            description='desc',
        )
        course.clean()  # should not raise (partner_institution now permitted)

    def test_unverified_partner_cannot_create_course(self):
        self.institution.is_verified = False
        self.institution.save(update_fields=['is_verified'])
        resp = self.client.post(
            reverse('courses:course-create'),
            {'title': 'Blocked Course', 'description': 'Should be blocked.'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class InstitutionCourseAssignmentTests(PartnerInstitutionTestBase):
    def setUp(self):
        self._verify_institution()
        self.course = NidusCourse.objects.create(
            created_by=self.institution_user,
            title='Roster Course',
            description='desc',
            partner_institution=self.institution,
        )
        # Onboard an active expert.
        self.expert = User.objects.create_user(
            email='expert@example.com', password='pw', full_name='Expert One',
            user_type='instructor', is_email_verified=True,
        )
        ep = self.expert.instructor_profile
        ep.affiliated_institution = self.institution
        ep.affiliation_status = 'active'
        ep.is_verified = True
        ep.save()
        self.client.force_authenticate(self.institution_user)

    def _add_url(self):
        return reverse('courses:institution-course-instructor-add', kwargs={'pk': self.course.pk})

    def _remove_url(self, uid):
        return reverse('courses:institution-course-instructor-remove',
                       kwargs={'pk': self.course.pk, 'expert_user_id': uid})

    def test_assign_and_remove_expert(self):
        resp = self.client.post(self._add_url(), {'expert_user_id': self.expert.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(self.course.instructors.filter(pk=self.expert.pk).exists())

        resp = self.client.delete(self._remove_url(self.expert.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(self.course.instructors.filter(pk=self.expert.pk).exists())

    def test_non_numeric_expert_id_returns_400(self):
        resp = self.client.post(self._add_url(), {'expert_user_id': 'abc'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_expert_id_returns_400(self):
        resp = self.client.post(self._add_url(), {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_assign_non_affiliated_user(self):
        outsider = User.objects.create_user(
            email='outsider@example.com', password='pw', full_name='Outsider',
            user_type='instructor', is_email_verified=True,
        )
        resp = self.client.post(self._add_url(), {'expert_user_id': outsider.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_foreign_course_returns_404(self):
        foreign_course = NidusCourse.objects.create(
            created_by=self.other_institution_user,
            title='Foreign Course', description='desc',
            partner_institution=self.other_institution,
        )
        url = reverse('courses:institution-course-instructor-add', kwargs={'pk': foreign_course.pk})
        resp = self.client.post(url, {'expert_user_id': self.expert.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_assigned_expert_can_edit_course(self):
        self.course.instructors.add(self.expert)
        self.client.force_authenticate(self.expert)
        resp = self.client.patch(
            reverse('courses:course-detail', kwargs={'pk': self.course.pk}),
            {'description': 'Edited by expert.'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.course.refresh_from_db()
        self.assertEqual(self.course.description, 'Edited by expert.')

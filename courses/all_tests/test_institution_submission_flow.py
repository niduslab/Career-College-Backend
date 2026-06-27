"""
Two-stage submission for institution-owned courses.

  expert /finish/  → institution_review
  institution /institution-review/ submit    → under_review (admin)
  institution /institution-review/ send_back → rejected (back to expert)

See docs/future_implementations/INSTITUTION_COURSE_SUBMISSION_FLOW.md.
"""
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, PartnerInstitutionProfile, User
from courses.models import CourseSection, Lecture, NidusCourse, SectionContent


class InstitutionSubmissionFlowTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        # Verified partner institution (profile auto-created by signal).
        cls.institution_user = User.objects.create_user(
            email='inst@example.com', password='pw12345!',
            full_name='Acme Institute', user_type='partner_institution',
            is_email_verified=True,
        )
        PartnerInstitutionProfile.objects.filter(user=cls.institution_user).update(
            institution_name='Acme Institute', is_verified=True, is_active=True,
        )
        cls.institution = cls.institution_user.partner_institution_profile

        # A second institution (for cross-institution 404 checks).
        cls.other_inst_user = User.objects.create_user(
            email='other-inst@example.com', password='pw12345!',
            full_name='Other Institute', user_type='partner_institution',
            is_email_verified=True,
        )
        PartnerInstitutionProfile.objects.filter(user=cls.other_inst_user).update(
            institution_name='Other Institute', is_verified=True, is_active=True,
        )

        # Institution-onboarded expert (verified instructor).
        cls.expert = User.objects.create_user(
            email='expert@example.com', password='pw12345!',
            full_name='Dr Expert', user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.expert).update(
            is_verified=True, affiliated_institution=cls.institution,
            affiliation_status='active', onboarding_source='institution',
        )

        # Individual (non-institution) instructor + course, for the unchanged path.
        cls.solo = User.objects.create_user(
            email='solo@example.com', password='pw12345!',
            full_name='Solo Instr', user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.solo).update(is_verified=True)

        cls.admin = User.objects.create_user(
            email='admin@example.com', password='pw12345!', full_name='Admin',
            user_type='admin', is_email_verified=True, is_staff=True,
        )

    def setUp(self):
        # Fresh complete institution course per test, with the expert rostered.
        self.course = self._make_complete_course(
            created_by=self.institution_user, institution=self.institution,
            title='Institution Course',
        )
        self.course.instructors.add(self.expert)

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _make_complete_course(created_by, institution, title):
        course = NidusCourse.objects.create(
            created_by=created_by, partner_institution=institution,
            title=title, description='A well-described course.',
        )
        section = CourseSection.objects.create(course=course, title='S1', position=1)
        lecture = Lecture.objects.create(
            section=section, title='L1',
            lecture_type=Lecture.LectureType.ARTICLE,
            article_content='Enough content to pass validation.',
        )
        SectionContent.objects.create(
            section=section, item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lecture.pk, position=1,
        )
        return course

    def _finish_url(self, pk=None):
        return reverse('courses:course-finish', kwargs={'pk': pk or self.course.pk})

    def _inst_review_url(self, pk=None):
        return reverse('courses:course-institution-review', kwargs={'pk': pk or self.course.pk})

    def _submit_url(self, pk=None):
        return reverse('courses:course-submit', kwargs={'pk': pk or self.course.pk})

    def _section_create_url(self):
        return reverse('courses:section-create', kwargs={'course_id': self.course.pk})

    # ---- expert /finish/ ---------------------------------------------------

    def test_expert_finish_moves_to_institution_review(self):
        self.client.force_authenticate(self.expert)
        r = self.client.post(self._finish_url())
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, 'institution_review')

    def test_finish_incomplete_course_returns_400(self):
        bare = NidusCourse.objects.create(
            created_by=self.institution_user, partner_institution=self.institution,
            title='Bare',  # no description, no sections
        )
        bare.instructors.add(self.expert)
        self.client.force_authenticate(self.expert)
        r = self.client.post(self._finish_url(pk=bare.pk))
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_institution_user_cannot_finish_404(self):
        # Institution is created_by, not in instructors → scoped out.
        self.client.force_authenticate(self.institution_user)
        r = self.client.post(self._finish_url())
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_finish_on_individual_course_returns_422(self):
        solo_course = NidusCourse.objects.create(
            created_by=self.solo, title='Solo', description='desc',
        )
        solo_course.instructors.add(self.solo)
        CourseSection.objects.create(course=solo_course, title='S', position=1)
        # give it content so completeness isn't the failure
        sec = solo_course.sections.first()
        lec = Lecture.objects.create(
            section=sec, title='L', lecture_type=Lecture.LectureType.ARTICLE,
            article_content='content',
        )
        SectionContent.objects.create(
            section=sec, item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=lec.pk, position=1,
        )
        self.client.force_authenticate(self.solo)
        r = self.client.post(self._finish_url(pk=solo_course.pk))
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_expert_submit_on_institution_course_returns_422(self):
        self.client.force_authenticate(self.expert)
        r = self.client.post(self._submit_url())
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, 'draft')

    def test_content_frozen_during_institution_review_422(self):
        self.course.transition_to('institution_review')
        self.client.force_authenticate(self.expert)
        r = self.client.post(self._section_create_url(), {'title': 'X', 'position': 2})
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # ---- institution /institution-review/ ----------------------------------

    def test_institution_submit_moves_to_under_review(self):
        self.course.transition_to('institution_review')
        self.client.force_authenticate(self.institution_user)
        r = self.client.post(self._inst_review_url(), {'action': 'submit'})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, 'under_review')

    def test_institution_send_back_moves_to_rejected(self):
        self.course.transition_to('institution_review')
        self.client.force_authenticate(self.institution_user)
        r = self.client.post(
            self._inst_review_url(), {'action': 'send_back', 'rejection_reason': 'Fix module 2.'}
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, 'rejected')
        self.assertEqual(self.course.rejection_reason, 'Fix module 2.')

    def test_send_back_without_reason_returns_400(self):
        self.course.transition_to('institution_review')
        self.client.force_authenticate(self.institution_user)
        r = self.client.post(self._inst_review_url(), {'action': 'send_back'})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_institution_review_when_not_in_institution_review_422(self):
        # course is still draft
        self.client.force_authenticate(self.institution_user)
        r = self.client.post(self._inst_review_url(), {'action': 'submit'})
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_expert_cannot_call_institution_review_403(self):
        self.course.transition_to('institution_review')
        self.client.force_authenticate(self.expert)
        r = self.client.post(self._inst_review_url(), {'action': 'submit'})
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_institution_cannot_act_404(self):
        self.course.transition_to('institution_review')
        self.client.force_authenticate(self.other_inst_user)
        r = self.client.post(self._inst_review_url(), {'action': 'submit'})
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_invalid_action_returns_400(self):
        self.course.transition_to('institution_review')
        self.client.force_authenticate(self.institution_user)
        r = self.client.post(self._inst_review_url(), {'action': 'publish'})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    # ---- individual path unchanged + full loop -----------------------------

    def test_individual_course_goes_straight_to_under_review(self):
        solo_course = self._make_complete_course(
            created_by=self.solo, institution=None, title='Solo Direct',
        )
        solo_course.instructors.add(self.solo)
        self.client.force_authenticate(self.solo)
        r = self.client.post(self._submit_url(pk=solo_course.pk))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        solo_course.refresh_from_db()
        self.assertEqual(solo_course.status, 'under_review')

    def test_full_loop_send_back_rework_finish_again(self):
        # finish → send_back → rework → finish → institution_review
        self.course.transition_to('institution_review')
        self.client.force_authenticate(self.institution_user)
        self.client.post(self._inst_review_url(), {'action': 'send_back', 'rejection_reason': 'x'})
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, 'rejected')

        rework_url = reverse('courses:course-rework', kwargs={'pk': self.course.pk})
        self.client.force_authenticate(self.expert)
        self.client.post(rework_url)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, 'draft')

        self.client.post(self._finish_url())
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, 'institution_review')

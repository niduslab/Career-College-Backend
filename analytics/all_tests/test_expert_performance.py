"""Per-expert performance metrics for a partner institution's roster."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, PartnerInstitutionProfile, User
from courses.models import (
    Certificate,
    CourseSection,
    Enrollment,
    Lecture,
    NidusCourse,
)
from webinars.models import Webinar, WebinarRegistration


def _make_institution(email, name):
    user = User.objects.create_user(
        email=email, password='pw12345!', full_name=name,
        user_type='partner_institution', is_email_verified=True,
    )
    PartnerInstitutionProfile.objects.filter(user=user).update(
        institution_name=name, is_verified=True, is_active=True,
    )
    return user, user.partner_institution_profile


def _make_expert(email, institution, name='Expert'):
    user = User.objects.create_user(
        email=email, password='pw12345!', full_name=name,
        user_type='instructor', is_email_verified=True,
    )
    InstructorProfile.objects.filter(user=user).update(
        is_verified=True, affiliated_institution=institution,
        affiliation_status='active', onboarding_source='institution',
    )
    return user


def _make_learner(email):
    return User.objects.create_user(
        email=email, password='pw12345!', full_name=email.split('@')[0],
        user_type='learner', is_email_verified=True,
    )


class ExpertPerformanceTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst_user, cls.institution = _make_institution('inst@ep.com', 'Acme Institute')
        cls.expert = _make_expert('expert@ep.com', cls.institution, 'Jane Roe')
        cls.idle_expert = _make_expert('idle@ep.com', cls.institution, 'Idle Expert')

        # Second institution — must never leak in.
        cls.other_user, cls.other_inst = _make_institution('other@ep.com', 'Other Institute')
        cls.other_expert = _make_expert('otherexpert@ep.com', cls.other_inst, 'Foreign Expert')

        cls.learner1 = _make_learner('l1@ep.com')
        cls.learner2 = _make_learner('l2@ep.com')
        now = timezone.now()

        # Institution course, expert on the roster, published + reviewed.
        cls.course = NidusCourse.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Data Science 101', description='d', status='published',
            avg_rating='4.50', review_count=10,
        )
        cls.course.instructors.add(cls.expert)

        # Content authored by the expert.
        CourseSection.objects.create(
            course=cls.course, title='S1', position=1, created_by=cls.expert,
        )
        section = CourseSection.objects.create(
            course=cls.course, title='S2', position=2, created_by=cls.expert,
        )
        Lecture.objects.create(
            section=section, title='L1',
            lecture_type=Lecture.LectureType.ARTICLE,
            article_content='content', created_by=cls.expert,
        )

        # Two enrollments: one completed (+cert), one active.
        enr_done = Enrollment.objects.create(
            user=cls.learner1, course=cls.course, is_active=True,
            progress_percent=100, completed_at=now,
        )
        Certificate.objects.create(
            enrollment=enr_done, learner_name='l1', course_title='Data Science 101', issued_at=now,
        )
        Enrollment.objects.create(
            user=cls.learner2, course=cls.course, is_active=True, progress_percent=40,
        )

        # Webinar hosted by the expert + one registration.
        webinar = Webinar.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            host_expert=cls.expert, title='Live Session', description='d',
            status='published', scheduled_at=now + timedelta(days=2), duration_minutes=60,
        )
        WebinarRegistration.objects.create(user=cls.learner1, webinar=webinar, is_active=True)

        # Foreign course/enrollment for the other institution's expert.
        foreign = NidusCourse.objects.create(
            created_by=cls.other_user, partner_institution=cls.other_inst,
            title='Foreign', description='d', status='published',
        )
        foreign.instructors.add(cls.other_expert)
        Enrollment.objects.create(user=cls.learner1, course=foreign, is_active=True)

    def auth(self, user):
        self.client.force_authenticate(user=user)


class ExpertPerformanceListTests(ExpertPerformanceTestBase):
    url = reverse('analytics:partner-expert-performance')

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_learner_forbidden(self):
        self.auth(self.learner1)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_lists_whole_roster_with_attribution(self):
        self.auth(self.inst_user)
        data = self.client.get(self.url).data['data']
        self.assertIn('attribution', data)
        ids = {e['expert']['id'] for e in data['experts']}
        # Both institution experts listed; foreign expert excluded.
        self.assertEqual(ids, {self.expert.pk, self.idle_expert.pk})

    def test_active_expert_metrics(self):
        self.auth(self.inst_user)
        experts = self.client.get(self.url).data['data']['experts']
        row = next(e for e in experts if e['expert']['id'] == self.expert.pk)
        self.assertEqual(row['courses_credited'], 1)
        self.assertEqual(row['published_courses'], 1)
        self.assertEqual(row['enrollments'], 2)          # foreign enrollment excluded
        self.assertEqual(row['completion_rate'], 50.0)   # 1 of 2
        self.assertEqual(row['certificates'], 1)
        self.assertEqual(row['avg_rating'], 4.5)
        self.assertEqual(row['content']['sections'], 2)
        self.assertEqual(row['content']['lectures'], 1)
        self.assertEqual(row['webinars_hosted'], 1)
        self.assertEqual(row['webinar_registrations'], 1)
        self.assertIsNotNone(row['last_active'])

    def test_idle_expert_zero_filled(self):
        self.auth(self.inst_user)
        experts = self.client.get(self.url).data['data']['experts']
        row = next(e for e in experts if e['expert']['id'] == self.idle_expert.pk)
        self.assertEqual(row['courses_credited'], 0)
        self.assertEqual(row['enrollments'], 0)
        self.assertEqual(row['completion_rate'], 0.0)
        self.assertEqual(row['avg_rating'], 0.0)
        self.assertEqual(row['content']['sections'], 0)
        self.assertIsNone(row['last_active'])


class ExpertPerformanceDetailTests(ExpertPerformanceTestBase):
    def _url(self, expert_id):
        return reverse('analytics:partner-expert-performance-detail', args=[expert_id])

    def test_detail_returns_single_expert(self):
        self.auth(self.inst_user)
        data = self.client.get(self._url(self.expert.pk)).data['data']
        self.assertEqual(data['expert']['expert']['id'], self.expert.pk)
        self.assertEqual(data['expert']['courses_credited'], 1)

    def test_foreign_expert_returns_404(self):
        self.auth(self.inst_user)
        r = self.client.get(self._url(self.other_expert.pk))
        self.assertEqual(r.status_code, 404)

    def test_unknown_expert_returns_404(self):
        self.auth(self.inst_user)
        r = self.client.get(self._url(999999))
        self.assertEqual(r.status_code, 404)


class ExpertPerformanceAttributionTests(APITestCase):
    """Attribution rules: co-teaching credit, creator+instructor dedup,
    removed-expert exclusion, and multi-course summation."""

    @classmethod
    def setUpTestData(cls):
        cls.inst_user, cls.institution = _make_institution('inst2@ep.com', 'Beta Institute')
        cls.expert_a = _make_expert('a@ep.com', cls.institution, 'Expert A')
        cls.expert_b = _make_expert('b@ep.com', cls.institution, 'Expert B')

        # Removed expert — deactivated after onboarding; must not appear.
        cls.removed = _make_expert('removed@ep.com', cls.institution, 'Removed Expert')
        InstructorProfile.objects.filter(user=cls.removed).update(
            affiliation_status='removed', is_verified=False,
        )

        # Co-taught course: both A and B on the roster → each credited once.
        cls.cotaught = NidusCourse.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Co-Taught', description='d', status='published',
        )
        cls.cotaught.instructors.add(cls.expert_a, cls.expert_b)

        # A second course credited to A only — proves per-expert summation.
        cls.solo = NidusCourse.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Solo A', description='d', status='draft',
        )
        cls.solo.instructors.add(cls.expert_a)

        # Course where A is BOTH creator and instructor → counted once, not twice.
        cls.self_made = NidusCourse.objects.create(
            created_by=cls.expert_a, partner_institution=cls.institution,
            title='Self Made', description='d', status='published',
        )
        cls.self_made.instructors.add(cls.expert_a)

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def _rows(self):
        self.auth(self.inst_user)
        return self.client.get(reverse('analytics:partner-expert-performance')).data['data']['experts']

    def test_removed_expert_excluded_from_roster(self):
        ids = {e['expert']['id'] for e in self._rows()}
        self.assertNotIn(self.removed.pk, ids)
        self.assertEqual(ids, {self.expert_a.pk, self.expert_b.pk})

    def test_cotaught_course_credited_to_each_instructor(self):
        rows = {e['expert']['id']: e for e in self._rows()}
        # B is only on the co-taught course.
        self.assertEqual(rows[self.expert_b.pk]['courses_credited'], 1)

    def test_multi_course_summation_and_creator_instructor_dedup(self):
        rows = {e['expert']['id']: e for e in self._rows()}
        # A is on co-taught + solo + self-made (creator & instructor) = 3 distinct.
        self.assertEqual(rows[self.expert_a.pk]['courses_credited'], 3)
        # Only co-taught + self-made are published.
        self.assertEqual(rows[self.expert_a.pk]['published_courses'], 2)

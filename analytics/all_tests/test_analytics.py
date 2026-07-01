"""Partner-institution analytics dashboard.

Covers the summary KPI payload, trend series, top-courses ranking, permission
gating, cross-institution isolation, and the empty-institution zero-fill.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from authentication.models import InstructorProfile, PartnerInstitutionProfile, User
from courses.models import Certificate, Enrollment, NidusCourse
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


def _make_learner(email):
    return User.objects.create_user(
        email=email, password='pw12345!', full_name=email.split('@')[0],
        user_type='learner', is_email_verified=True,
    )


class AnalyticsTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst_user, cls.institution = _make_institution('inst@example.com', 'Acme Institute')
        cls.other_user, cls.other_inst = _make_institution('other@example.com', 'Other Institute')

        # Active rostered expert.
        cls.expert = User.objects.create_user(
            email='expert@example.com', password='pw12345!', full_name='Dr Expert',
            user_type='instructor', is_email_verified=True,
        )
        InstructorProfile.objects.filter(user=cls.expert).update(
            is_verified=True, affiliated_institution=cls.institution,
            affiliation_status='active', onboarding_source='institution',
        )

        cls.learner1 = _make_learner('l1@example.com')
        cls.learner2 = _make_learner('l2@example.com')

        now = timezone.now()

        # Two published courses + one draft, all owned by the institution.
        cls.course_pub_a = NidusCourse.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Published A', description='desc', status='published',
            avg_rating=4.5, review_count=10,
        )
        cls.course_pub_b = NidusCourse.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Published B', description='desc', status='published',
            avg_rating=3.5, review_count=2,
        )
        cls.course_draft = NidusCourse.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Draft C', description='desc', status='draft',
        )

        # Course A: one completed enrollment (with certificate) + one active.
        cls.enr_completed = Enrollment.objects.create(
            user=cls.learner1, course=cls.course_pub_a, is_active=True,
            progress_percent=100, completed_at=now, last_accessed_at=now,
        )
        Certificate.objects.create(
            enrollment=cls.enr_completed, learner_name='L1',
            course_title='Published A', issued_at=now,
        )
        cls.enr_active = Enrollment.objects.create(
            user=cls.learner2, course=cls.course_pub_a, is_active=True,
            progress_percent=40, last_accessed_at=now,
        )
        # Course B: one active enrollment, never accessed (not an active learner).
        Enrollment.objects.create(
            user=cls.learner1, course=cls.course_pub_b, is_active=True,
            progress_percent=0,
        )

        # Webinars: one upcoming, one completed (past), one draft.
        cls.webinar_upcoming = Webinar.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Upcoming', description='d', status='published',
            scheduled_at=now + timedelta(days=3), duration_minutes=60,
        )
        cls.webinar_past = Webinar.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Past', description='d', status='published',
            scheduled_at=now - timedelta(days=3), duration_minutes=60,
        )
        Webinar.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Draft W', description='d', status='draft',
        )
        WebinarRegistration.objects.create(
            user=cls.learner1, webinar=cls.webinar_upcoming, is_active=True,
        )
        WebinarRegistration.objects.create(
            user=cls.learner2, webinar=cls.webinar_past, is_active=True,
        )

        # Other institution — must never leak into Acme's numbers.
        cls.other_course = NidusCourse.objects.create(
            created_by=cls.other_user, partner_institution=cls.other_inst,
            title='Foreign', description='desc', status='published',
        )
        Enrollment.objects.create(
            user=cls.learner1, course=cls.other_course, is_active=True,
        )

    def auth(self, user):
        self.client.force_authenticate(user=user)


class AnalyticsSummaryTests(AnalyticsTestBase):
    url = reverse('analytics:partner-summary')

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_non_institution_forbidden(self):
        self.auth(self.learner1)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_course_metrics(self):
        self.auth(self.inst_user)
        data = self.client.get(self.url).data['data']['courses']
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['published'], 2)
        self.assertEqual(data['draft'], 1)
        self.assertEqual(data['status_breakdown']['published'], 2)
        self.assertEqual(data['total_reviews'], 12)
        # Weighted: (4.5*10 + 3.5*2) / 12 = 4.33
        self.assertEqual(data['avg_rating'], 4.33)

    def test_enrollment_metrics(self):
        self.auth(self.inst_user)
        data = self.client.get(self.url).data['data']['enrollments']
        self.assertEqual(data['active'], 3)          # foreign enrollment excluded
        self.assertEqual(data['active_learners'], 2)  # two accessed within window
        self.assertEqual(data['completion_rate'], 33.3)  # 1 of 3 completed

    def test_certificate_metrics(self):
        self.auth(self.inst_user)
        data = self.client.get(self.url).data['data']['certificates']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['this_month'], 1)

    def test_webinar_metrics(self):
        self.auth(self.inst_user)
        data = self.client.get(self.url).data['data']['webinars']
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['published'], 2)
        self.assertEqual(data['upcoming'], 1)
        self.assertEqual(data['completed'], 1)
        self.assertEqual(data['registrations'], 2)
        self.assertFalse(data['attendance_tracking_enabled'])

    def test_roster_and_revenue(self):
        self.auth(self.inst_user)
        data = self.client.get(self.url).data['data']
        self.assertEqual(data['roster']['experts_active'], 1)
        self.assertFalse(data['revenue']['enabled'])
        self.assertIsNone(data['revenue']['estimated_gross'])
        self.assertIn('composite', data['engagement_score'])

    def test_empty_institution_zero_filled(self):
        empty_user, _ = _make_institution('empty@example.com', 'Empty Inst')
        self.auth(empty_user)
        data = self.client.get(self.url).data['data']
        self.assertEqual(data['courses']['total'], 0)
        self.assertEqual(data['enrollments']['active'], 0)
        self.assertEqual(data['enrollments']['completion_rate'], 0.0)
        self.assertIsNone(data['enrollments']['growth']['growth_pct'])
        self.assertEqual(data['certificates']['total'], 0)
        self.assertEqual(data['webinars']['total'], 0)


class AnalyticsTrendTests(AnalyticsTestBase):
    def test_enrollment_trend_contiguous(self):
        self.auth(self.inst_user)
        url = reverse('analytics:partner-enrollment-trend')
        data = self.client.get(url, {'granularity': 'monthly', 'periods': 6}).data['data']
        self.assertEqual(data['granularity'], 'monthly')
        self.assertEqual(data['periods'], 6)
        self.assertEqual(len(data['series']), 6)

    def test_periods_clamped(self):
        self.auth(self.inst_user)
        url = reverse('analytics:partner-enrollment-trend')
        data = self.client.get(url, {'periods': 999}).data['data']
        self.assertEqual(data['periods'], 24)  # MAX_TREND_PERIODS

    def test_weekly_granularity(self):
        self.auth(self.inst_user)
        url = reverse('analytics:partner-certificate-trend')
        data = self.client.get(url, {'granularity': 'weekly', 'periods': 4}).data['data']
        self.assertEqual(len(data['series']), 4)

    def _issue_cert(self, email, issued_at):
        learner = _make_learner(email)
        enr = Enrollment.objects.create(
            user=learner, course=self.course_pub_b, is_active=True,
            progress_percent=100, completed_at=issued_at,
        )
        Certificate.objects.create(
            enrollment=enr, learner_name=email, course_title='B', issued_at=issued_at,
        )

    def test_monthly_oldest_bucket_covers_full_month(self):
        # Regression (B1): a cert issued at the very start of the current month
        # must be counted by a periods=1 monthly series, not dropped because the
        # filter started at "today". Base already has one cert issued ~now.
        now = timezone.now()
        self._issue_cert('early-month@example.com', now.replace(day=1, hour=0, minute=10))
        self.auth(self.inst_user)
        url = reverse('analytics:partner-certificate-trend')
        data = self.client.get(url, {'granularity': 'monthly', 'periods': 1}).data['data']
        self.assertEqual(len(data['series']), 1)
        self.assertEqual(data['series'][0]['count'], 2)

    def test_weekly_oldest_bucket_covers_full_week(self):
        # Regression (B1): a cert issued on Monday of the current week must be
        # counted by a periods=1 weekly series even when today is later in the week.
        now = timezone.now()
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=10, second=0, microsecond=0)
        self._issue_cert('monday@example.com', monday)
        self.auth(self.inst_user)
        url = reverse('analytics:partner-certificate-trend')
        data = self.client.get(url, {'granularity': 'weekly', 'periods': 1}).data['data']
        self.assertEqual(len(data['series']), 1)
        self.assertEqual(data['series'][0]['count'], 2)

    def test_bucket_starts_are_truncated(self):
        # Regression (B1/B2): bucket starts must align with TruncWeek (Monday) and
        # TruncMonth (day 1) so filter + series keys match the DB grouping exactly.
        from analytics.services.analytics_service import _bucket_starts

        weekly = _bucket_starts(timezone.now(), 'weekly', 5)
        self.assertEqual(len(weekly), 5)
        self.assertTrue(all(s.weekday() == 0 and s.hour == 0 and s.minute == 0 for s in weekly))

        monthly = _bucket_starts(timezone.now(), 'monthly', 5)
        self.assertEqual(len(monthly), 5)
        self.assertTrue(all(s.day == 1 and s.hour == 0 for s in monthly))


class AnalyticsTopCoursesTests(AnalyticsTestBase):
    url = reverse('analytics:partner-top-courses')

    def test_ranked_by_enrollments(self):
        self.auth(self.inst_user)
        data = self.client.get(self.url, {'sort': 'enrollments'}).data['data']
        # Course A has 2 active enrollments → ranked first. Foreign course absent.
        self.assertEqual(data[0]['title'], 'Published A')
        self.assertEqual(data[0]['enrollments'], 2)
        titles = [c['title'] for c in data]
        self.assertNotIn('Foreign', titles)

    def test_limit_clamped(self):
        self.auth(self.inst_user)
        data = self.client.get(self.url, {'limit': 1}).data['data']
        self.assertEqual(len(data), 1)

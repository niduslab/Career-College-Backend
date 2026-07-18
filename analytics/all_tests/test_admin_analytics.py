"""Platform-wide (admin) analytics dashboard.

Covers the summary KPI payload (incl. real revenue), trend series, funnel,
top-courses ranking, and admin-only permission gating.
"""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import PartnerInstitutionProfile, User
from courses.models import Certificate, Enrollment, NidusCourse
from payments.models import Order
from webinars.models import Webinar, WebinarRegistration


def _learner(email):
    return User.objects.create_user(
        email=email, password='pw12345!', full_name=email.split('@')[0],
        user_type='learner', is_email_verified=True,
    )


class AdminAnalyticsBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='admin@example.com', password='pw12345!', full_name='Admin',
            user_type='admin', is_email_verified=True, is_staff=True,
        )
        cls.inst_user = User.objects.create_user(
            email='inst@example.com', password='pw12345!', full_name='Acme',
            user_type='partner_institution', is_email_verified=True,
        )
        PartnerInstitutionProfile.objects.filter(user=cls.inst_user).update(
            institution_name='Acme', is_verified=True, is_active=True,
        )
        cls.institution = cls.inst_user.partner_institution_profile
        cls.instructor = User.objects.create_user(
            email='teach@example.com', password='pw12345!', full_name='Teacher',
            user_type='instructor', is_email_verified=True,
        )
        cls.l1 = _learner('l1@example.com')
        cls.l2 = _learner('l2@example.com')
        cls.l3 = _learner('l3@example.com')

        now = timezone.now()

        cls.course_a = NidusCourse.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Course A', description='d', status='published',
            avg_rating=Decimal('4.5'), review_count=10,
        )
        cls.course_b = NidusCourse.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Course B', description='d', status='published',
            avg_rating=Decimal('3.5'), review_count=2,
        )
        cls.course_draft = NidusCourse.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Draft', description='d', status='draft',
        )

        # Enrollments: 1 paid+completed (with cert), 2 free active.
        cls.enr_done = Enrollment.objects.create(
            user=cls.l1, course=cls.course_a, is_active=True,
            enrollment_type='paid', progress_percent=100,
            completed_at=now, last_accessed_at=now,
        )
        Certificate.objects.create(
            enrollment=cls.enr_done, learner_name='L1',
            course_title='Course A', issued_at=now,
        )
        Enrollment.objects.create(
            user=cls.l2, course=cls.course_a, is_active=True,
            enrollment_type='free', progress_percent=40, last_accessed_at=now,
        )
        Enrollment.objects.create(
            user=cls.l3, course=cls.course_b, is_active=True,
            enrollment_type='free', progress_percent=0,
        )

        cls.webinar = Webinar.objects.create(
            created_by=cls.inst_user, partner_institution=cls.institution,
            title='Webinar', description='d', status='published',
            scheduled_at=now + timedelta(days=2), duration_minutes=60,
        )
        WebinarRegistration.objects.create(user=cls.l1, webinar=cls.webinar, is_active=True)

        # Orders: 2 PAID (course 1000 + webinar 500), 1 non-paid (ignored).
        Order.objects.create(
            user=cls.l1, course=cls.course_a, amount=Decimal('1000.00'),
            tran_id='t-course', status='paid',
        )
        Order.objects.create(
            user=cls.l2, webinar=cls.webinar, amount=Decimal('500.00'),
            tran_id='t-webinar', status='paid',
        )
        Order.objects.create(
            user=cls.l3, course=cls.course_b, amount=Decimal('999.00'),
            tran_id='t-initiated', status='initiated',
        )

    def auth_admin(self):
        self.client.force_authenticate(user=self.admin)


class AdminSummaryTests(AdminAnalyticsBase):
    url = reverse('analytics:admin-summary')

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_non_admin_forbidden(self):
        for user in (self.l1, self.inst_user, self.instructor):
            self.client.force_authenticate(user=user)
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_admin_via_jwt_bearer(self):
        token = RefreshToken.for_user(self.admin).access_token
        client = self.client_class()
        resp = client.get(self.url, HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(resp.status_code, 200)

    def test_user_metrics(self):
        self.auth_admin()
        data = self.client.get(self.url).data['data']['users']
        self.assertEqual(data['total'], 6)
        self.assertEqual(data['by_type']['learner'], 3)
        self.assertEqual(data['by_type']['instructor'], 1)
        self.assertEqual(data['by_type']['partner_institution'], 1)
        self.assertEqual(data['by_type']['admin'], 1)
        self.assertEqual(data['email_verified'], 6)

    def test_course_metrics_platform_wide(self):
        self.auth_admin()
        data = self.client.get(self.url).data['data']['courses']
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['published'], 2)
        self.assertEqual(data['draft'], 1)
        self.assertEqual(data['total_reviews'], 12)
        self.assertEqual(data['avg_rating'], 4.33)  # (4.5*10 + 3.5*2)/12

    def test_enrollment_metrics(self):
        self.auth_admin()
        data = self.client.get(self.url).data['data']['enrollments']
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['active'], 3)
        self.assertEqual(data['completed'], 1)
        self.assertEqual(data['completion_rate'], 33.3)
        self.assertEqual(data['by_type'], {'free': 2, 'paid': 1})

    def test_revenue_enabled_and_real(self):
        self.auth_admin()
        data = self.client.get(self.url).data['data']['revenue']
        self.assertTrue(data['enabled'])
        self.assertEqual(data['currency'], 'BDT')
        self.assertEqual(data['gross'], 1500.0)          # only PAID orders
        self.assertEqual(data['paid_orders'], 2)
        self.assertEqual(data['by_item_type'], {'course': 1000.0, 'webinar': 500.0})

    def test_certificate_and_webinar_metrics(self):
        self.auth_admin()
        data = self.client.get(self.url).data['data']
        self.assertEqual(data['certificates']['total'], 1)
        self.assertEqual(data['webinars']['total'], 1)
        self.assertEqual(data['webinars']['upcoming'], 1)
        self.assertEqual(data['webinars']['registrations'], 1)


class AdminEmptyPlatformTests(APITestCase):
    """No data except the admin itself → clean zeros, revenue enabled but 0."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='solo-admin@example.com', password='pw12345!', full_name='Admin',
            user_type='admin', is_email_verified=True, is_staff=True,
        )

    def test_zeros(self):
        self.client.force_authenticate(user=self.admin)
        data = self.client.get(reverse('analytics:admin-summary')).data['data']
        self.assertEqual(data['courses']['total'], 0)
        self.assertEqual(data['enrollments']['total'], 0)
        self.assertEqual(data['enrollments']['completion_rate'], 0.0)
        self.assertIsNone(data['enrollments']['growth_pct'])
        self.assertTrue(data['revenue']['enabled'])
        self.assertEqual(data['revenue']['gross'], 0)
        self.assertIsNone(data['revenue']['growth_pct'])


class AdminTrendTests(AdminAnalyticsBase):
    def test_enrollment_trend_contiguous(self):
        self.auth_admin()
        data = self.client.get(
            reverse('analytics:admin-enrollments-trend'),
            {'granularity': 'monthly', 'periods': 6},
        ).data['data']
        self.assertEqual(data['periods'], 6)
        self.assertEqual(len(data['series']), 6)
        self.assertIn('count', data['series'][0])

    def test_periods_clamped(self):
        self.auth_admin()
        data = self.client.get(
            reverse('analytics:admin-users-trend'), {'periods': 999},
        ).data['data']
        self.assertEqual(data['periods'], 24)

    def test_revenue_trend_sums_not_counts(self):
        self.auth_admin()
        data = self.client.get(
            reverse('analytics:admin-revenue-trend'),
            {'granularity': 'monthly', 'periods': 1},
        ).data['data']
        self.assertEqual(len(data['series']), 1)
        # Both paid orders were created this month → gross 1500 in the bucket.
        self.assertEqual(data['series'][0]['value'], 1500.0)

    def test_trend_endpoints_reject_non_admin(self):
        names = (
            'analytics:admin-users-trend', 'analytics:admin-enrollments-trend',
            'analytics:admin-certificates-trend', 'analytics:admin-revenue-trend',
        )
        for name in names:
            url = reverse(name)
            self.assertEqual(self.client.get(url).status_code, 401)  # no token
            self.client.force_authenticate(user=self.l1)
            self.assertEqual(self.client.get(url).status_code, 403)  # learner
            self.client.force_authenticate(user=None)


class AdminFunnelTests(AdminAnalyticsBase):
    url = reverse('analytics:admin-funnel')

    def test_non_admin_forbidden(self):
        self.client.force_authenticate(user=self.l1)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_funnel_distinct_learners(self):
        self.auth_admin()
        stages = {s['key']: s for s in self.client.get(self.url).data['data']['stages']}
        self.assertEqual(stages['signup']['count'], 3)      # 3 learners
        self.assertEqual(stages['enrolled']['count'], 3)    # all 3 enrolled
        self.assertEqual(stages['completed']['count'], 1)
        self.assertEqual(stages['certified']['count'], 1)
        self.assertEqual(stages['completed']['from_prev_pct'], 33.3)
        self.assertNotIn('from_prev_pct', stages['signup'])  # first stage has no previous

    def test_funnel_stays_monotonic_when_enroller_leaves_learner_pool(self):
        """A soft-deleted and a role-changed enroller must not push a later stage
        above `signup` — every stage counts the same current-learner population."""
        self.auth_admin()
        # l2 soft-deleted, l3 role-changed to instructor: both keep their enrollment
        # rows but drop out of the learner signup count.
        User.objects.filter(pk=self.l2.pk).update(is_deleted=True)
        User.objects.filter(pk=self.l3.pk).update(user_type='instructor')

        stages = {s['key']: s for s in self.client.get(self.url).data['data']['stages']}
        counts = [stages[k]['count'] for k in ('signup', 'enrolled', 'completed', 'certified')]
        self.assertEqual(stages['signup']['count'], 1)   # only l1 remains a learner
        self.assertEqual(stages['enrolled']['count'], 1)  # l2/l3 enrollments excluded
        self.assertEqual(counts, sorted(counts, reverse=True))  # monotonic non-increasing
        for stage in ('enrolled', 'completed', 'certified'):
            self.assertLessEqual(stages[stage]['from_prev_pct'], 100.0)


class AdminTopCoursesTests(AdminAnalyticsBase):
    url = reverse('analytics:admin-top-courses')

    def test_ranked_by_enrollments(self):
        self.auth_admin()
        data = self.client.get(self.url, {'sort': 'enrollments'}).data['data']
        self.assertEqual(data[0]['title'], 'Course A')  # 2 active enrollments
        self.assertEqual(data[0]['enrollments'], 2)

    def test_limit_clamped(self):
        self.auth_admin()
        data = self.client.get(self.url, {'limit': 1}).data['data']
        self.assertEqual(len(data), 1)

    def test_sort_rating_and_invalid_fallback(self):
        self.auth_admin()
        by_rating = self.client.get(self.url, {'sort': 'rating'}).data['data']
        self.assertEqual(by_rating[0]['title'], 'Course A')  # 4.5 > 3.5
        # Invalid sort falls back to enrollments (Course A leads on both here).
        fallback = self.client.get(self.url, {'sort': 'bogus'}).data['data']
        self.assertEqual(fallback[0]['title'], 'Course A')

    def test_rejects_non_admin(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)  # no token
        self.client.force_authenticate(user=self.l1)
        self.assertEqual(self.client.get(self.url).status_code, 403)  # learner

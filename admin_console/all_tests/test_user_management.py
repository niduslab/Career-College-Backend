"""
Tests for admin user management (Sprint 8, Task 3): list/search, detail,
suspend/reactivate, role change, audit log, and the re-auth gate on mutations.
"""
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from admin_console.all_models import AdminActionLog
from authentication.models import User

_SHARED_LOGIN = '/api/v1/auth/login/'
_NOTIF_UNREAD = '/api/v1/notifications/unread-count/'


class AdminUserManagementTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='admin@example.com', password='pw12345!',
            full_name='Admin User', user_type='admin',
            is_email_verified=True, is_staff=True,
        )
        cls.admin2 = User.objects.create_user(
            email='admin2@example.com', password='pw12345!',
            full_name='Second Admin', user_type='admin',
            is_email_verified=True, is_staff=True,
        )
        cls.learner = User.objects.create_user(
            email='learner@example.com', password='pw12345!',
            full_name='Learner One', user_type='learner',
            is_email_verified=True,
        )
        cls.instructor = User.objects.create_user(
            email='teach@example.com', password='pw12345!',
            full_name='Teacher Person', user_type='instructor',
            is_email_verified=True,
        )

    def setUp(self):
        cache.clear()  # reset admin-login throttle
        self.list_url = reverse('admin_console:user-list')
        self.audit_url = reverse('admin_console:audit-list')
        # Establish a fresh admin session via the shared login (stamps
        # admin_login_at for the re-auth gate).
        resp = self.client.post(
            _SHARED_LOGIN,
            {'email': 'admin@example.com', 'password': 'pw12345!'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK

    # --- list / search / filter ------------------------------------------
    def test_list_returns_all_users_paginated(self):
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['success'])
        self.assertEqual(resp.data['data']['count'], 4)

    def test_search_by_email(self):
        resp = self.client.get(self.list_url, {'search': 'learner@'})
        results = resp.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['email'], 'learner@example.com')

    def test_filter_by_user_type(self):
        resp = self.client.get(self.list_url, {'user_type': 'admin'})
        self.assertEqual(resp.data['data']['count'], 2)

    def test_invalid_user_type_filter_400(self):
        resp = self.client.get(self.list_url, {'user_type': 'wizard'})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_sort_400(self):
        resp = self.client.get(self.list_url, {'sort': 'bogus'})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # --- detail -----------------------------------------------------------
    def test_detail_includes_state_flags(self):
        url = reverse('admin_console:user-detail', args=[self.learner.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('is_restricted_by_admin', resp.data['data'])
        self.assertIn('deletion_reason', resp.data['data'])

    def test_detail_missing_404(self):
        url = reverse('admin_console:user-detail', args=[999999])
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    # --- suspend ----------------------------------------------------------
    def test_suspend_flips_both_flags_and_logs(self):
        url = reverse('admin_console:user-suspend', args=[self.learner.pk])
        resp = self.client.post(url, {'reason': 'spam'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.learner.refresh_from_db()
        self.assertTrue(self.learner.is_restricted_by_admin)
        self.assertFalse(self.learner.is_active)
        self.assertTrue(
            AdminActionLog.objects.filter(
                target_user=self.learner, action='suspend', reason='spam'
            ).exists()
        )

    def test_suspended_user_cannot_login(self):
        url = reverse('admin_console:user-suspend', args=[self.learner.pk])
        self.client.post(url, {}, format='json')

        fresh = self.client_class()
        resp = fresh.post(
            _SHARED_LOGIN,
            {'email': 'learner@example.com', 'password': 'pw12345!'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_suspend_kills_existing_access_token(self):
        token = RefreshToken.for_user(self.learner).access_token
        fresh = self.client_class()
        # Token works before suspension.
        self.assertEqual(
            fresh.get(_NOTIF_UNREAD, HTTP_AUTHORIZATION=f'Bearer {token}').status_code,
            status.HTTP_200_OK,
        )
        self.client.post(reverse('admin_console:user-suspend', args=[self.learner.pk]), {}, format='json')
        # is_active=False → SimpleJWT rejects the still-valid token.
        self.assertEqual(
            fresh.get(_NOTIF_UNREAD, HTTP_AUTHORIZATION=f'Bearer {token}').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_cannot_suspend_self(self):
        url = reverse('admin_console:user-suspend', args=[self.admin.pk])
        resp = self.client.post(url, {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_cannot_suspend_another_admin(self):
        url = reverse('admin_console:user-suspend', args=[self.admin2.pk])
        resp = self.client.post(url, {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_double_suspend_422(self):
        url = reverse('admin_console:user-suspend', args=[self.learner.pk])
        self.client.post(url, {}, format='json')
        resp = self.client.post(url, {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # --- reactivate -------------------------------------------------------
    def test_reactivate_reverses_suspend(self):
        self.client.post(reverse('admin_console:user-suspend', args=[self.learner.pk]), {}, format='json')
        resp = self.client.post(reverse('admin_console:user-reactivate', args=[self.learner.pk]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.learner.refresh_from_db()
        self.assertFalse(self.learner.is_restricted_by_admin)
        self.assertTrue(self.learner.is_active)

    def test_reactivate_when_not_suspended_422(self):
        resp = self.client.post(reverse('admin_console:user-reactivate', args=[self.learner.pk]))
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # --- role change ------------------------------------------------------
    def test_role_change_switches_type_and_provisions_profile(self):
        url = reverse('admin_console:user-role', args=[self.learner.pk])
        resp = self.client.post(url, {'user_type': 'instructor'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.learner.refresh_from_db()
        self.assertEqual(self.learner.user_type, 'instructor')
        self.assertTrue(hasattr(self.learner, 'instructor_profile'))
        self.assertTrue(
            AdminActionLog.objects.filter(target_user=self.learner, action='role_change').exists()
        )

    def test_role_change_grant_staff(self):
        url = reverse('admin_console:user-role', args=[self.learner.pk])
        resp = self.client.post(url, {'is_staff': True}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.learner.refresh_from_db()
        self.assertTrue(self.learner.is_staff)

    def test_role_change_same_type_422(self):
        url = reverse('admin_console:user-role', args=[self.learner.pk])
        resp = self.client.post(url, {'user_type': 'learner'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_cannot_change_own_role(self):
        url = reverse('admin_console:user-role', args=[self.admin.pk])
        resp = self.client.post(url, {'user_type': 'learner'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_role_change_invalid_type_400(self):
        url = reverse('admin_console:user-role', args=[self.learner.pk])
        resp = self.client.post(url, {'user_type': 'wizard'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_role_change_is_staff_string_rejected(self):
        # "false" (string) must NOT be coerced to True — reject ambiguous input.
        url = reverse('admin_console:user-role', args=[self.learner.pk])
        resp = self.client.post(url, {'is_staff': 'not-a-bool'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.learner.refresh_from_db()
        self.assertFalse(self.learner.is_staff)

    def test_role_change_is_staff_boolean_false_revokes(self):
        self.learner.is_staff = True
        self.learner.save(update_fields=['is_staff'])
        url = reverse('admin_console:user-role', args=[self.learner.pk])
        resp = self.client.post(url, {'is_staff': False}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.learner.refresh_from_db()
        self.assertFalse(self.learner.is_staff)

    # --- audit log --------------------------------------------------------
    def test_audit_log_lists_and_filters(self):
        self.client.post(reverse('admin_console:user-suspend', args=[self.learner.pk]), {}, format='json')
        self.client.post(reverse('admin_console:user-role', args=[self.instructor.pk]),
                         {'is_staff': True}, format='json')

        resp = self.client.get(self.audit_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data['data']['count'], 2)

        filtered = self.client.get(self.audit_url, {'action': 'suspend'})
        actions = {row['action'] for row in filtered.data['data']['results']}
        self.assertEqual(actions, {'suspend'})

    def test_audit_metadata_snapshots_emails(self):
        # Attribution must survive later deletion of actor/target → snapshot emails.
        self.client.post(reverse('admin_console:user-suspend', args=[self.learner.pk]), {}, format='json')
        row = AdminActionLog.objects.get(target_user=self.learner, action='suspend')
        self.assertEqual(row.metadata['actor_email'], 'admin@example.com')
        self.assertEqual(row.metadata['target_email'], 'learner@example.com')

    # --- throttling -------------------------------------------------------
    def test_mutations_are_rate_limited(self):
        # 30/min per admin; the 31st mutating request is throttled (429).
        # Suspend-self returns 422 but still counts against the throttle.
        url = reverse('admin_console:user-suspend', args=[self.admin.pk])
        last = None
        for _ in range(31):
            last = self.client.post(url, {}, format='json')
        self.assertEqual(last.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_search_min_length_enforced(self):
        resp = self.client.get(self.list_url, {'search': 'a'})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # --- auth gate --------------------------------------------------------
    def test_mutation_allowed_for_jwt_admin(self):
        # Mutations use the base admin gate (no re-auth / fresh-session
        # requirement), so a JWT-authenticated admin may suspend.
        token = RefreshToken.for_user(self.admin).access_token
        jwt_client = self.client_class()
        url = reverse('admin_console:user-suspend', args=[self.learner.pk])
        resp = jwt_client.post(url, {}, format='json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_list_allowed_for_jwt_admin(self):
        # Reads use the base admin gate.
        token = RefreshToken.for_user(self.admin).access_token
        jwt_client = self.client_class()
        resp = jwt_client.get(self.list_url, HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

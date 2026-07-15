"""
Tests for admin device/session tracking + remote logout (Sprint 8).

Covers: session record created on admin login (with parsed user-agent), the
own-sessions list, single revoke, revoke-others, the 404 no-access rule, and
that non-admins never get a record.
"""
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from admin_console.all_models import AdminSession
from authentication.models import User

_CHROME_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
_FIREFOX_UA = (
    'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0'
)


class AdminSessionTrackingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='admin@example.com', password='pw12345!',
            full_name='Admin User', user_type='admin',
            is_email_verified=True, is_staff=True,
        )
        cls.learner = User.objects.create_user(
            email='learner@example.com', password='pw12345!',
            full_name='Learner User', user_type='learner',
            is_email_verified=True,
        )

    def setUp(self):
        cache.clear()  # reset the shared-login throttle between tests
        self.login_url = '/api/v1/auth/login/'  # shared login opens the admin session
        self.list_url = reverse('admin_console:session-list')
        self.revoke_others_url = reverse('admin_console:session-revoke-others')
        self.session_url = reverse('admin_console:auth-session')

    def _login(self, client, ua):
        resp = client.post(
            self.login_url,
            {'email': 'admin@example.com', 'password': 'pw12345!'},
            format='json',
            HTTP_USER_AGENT=ua,
        )
        # Shared login also issues JWT cookies; drop them so these tests exercise
        # the *session* path only (otherwise a revoked session still authenticates
        # via the lingering access_token cookie).
        for name in ('access_token', 'refresh_token'):
            client.cookies.pop(name, None)
        return resp

    # --- capture on login -------------------------------------------------
    def test_admin_login_records_session_with_parsed_ua(self):
        self._login(self.client, _CHROME_UA)
        row = AdminSession.objects.get(user=self.admin)
        self.assertIn('Chrome', row.browser)
        self.assertIn('Windows', row.os)
        self.assertEqual(row.user_agent, _CHROME_UA)

    def test_non_admin_login_creates_no_record(self):
        # A learner logs in via the shared login: JWT only, no django session,
        # so the login-signal receiver records nothing.
        resp = self.client.post(
            self.login_url,
            {'email': 'learner@example.com', 'password': 'pw12345!'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(AdminSession.objects.count(), 0)

    # --- list -------------------------------------------------------------
    def test_list_shows_own_sessions_with_is_current(self):
        self._login(self.client, _CHROME_UA)
        other = APIClient()
        self._login(other, _FIREFOX_UA)

        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data['data']['results']
        self.assertEqual(len(results), 2)
        current = [r for r in results if r['is_current']]
        self.assertEqual(len(current), 1)  # exactly one is the calling device

    # --- single revoke ----------------------------------------------------
    def test_revoke_other_session_logs_it_out(self):
        self._login(self.client, _CHROME_UA)
        other = APIClient()
        self._login(other, _FIREFOX_UA)

        # Find the non-current row from the caller's perspective.
        results = self.client.get(self.list_url).data['data']['results']
        other_id = [r['id'] for r in results if not r['is_current']][0]

        revoke_url = reverse('admin_console:session-revoke', args=[other_id])
        resp = self.client.delete(revoke_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # The revoked device's session is dead.
        self.assertEqual(
            other.get(self.session_url).status_code, status.HTTP_403_FORBIDDEN
        )
        # Caller still alive.
        self.assertEqual(
            self.client.get(self.session_url).status_code, status.HTTP_200_OK
        )

    def test_revoke_unknown_id_404(self):
        self._login(self.client, _CHROME_UA)
        revoke_url = reverse('admin_console:session-revoke', args=[999999])
        resp = self.client.delete(revoke_url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_revoke_another_admins_session(self):
        other_admin = User.objects.create_user(
            email='admin2@example.com', password='pw12345!',
            full_name='Admin Two', user_type='admin',
            is_email_verified=True, is_staff=True,
        )
        victim = APIClient()
        victim.force_login(other_admin)
        victim_row = AdminSession.objects.create(
            user=other_admin, session_key='x' * 40, ip_address='1.2.3.4',
        )

        self._login(self.client, _CHROME_UA)
        revoke_url = reverse('admin_console:session-revoke', args=[victim_row.pk])
        resp = self.client.delete(revoke_url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(AdminSession.objects.filter(pk=victim_row.pk).exists())

    # --- revoke others ----------------------------------------------------
    def test_revoke_others_kills_all_but_current(self):
        self._login(self.client, _CHROME_UA)
        other = APIClient()
        self._login(other, _FIREFOX_UA)

        resp = self.client.post(self.revoke_others_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['data']['revoked'], 1)

        self.assertEqual(
            other.get(self.session_url).status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get(self.session_url).status_code, status.HTTP_200_OK
        )

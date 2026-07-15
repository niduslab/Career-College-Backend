"""
Tests for the admin session established by the shared login.

The admin console has no login endpoint of its own — `POST /api/v1/auth/login/`
opens the session (+ primes CSRF) for admins. These tests cover that session
reaching the admin-console base view, logout, the anon gate, and the JWT
fallback for tooling.
"""
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import User

_SHARED_LOGIN = '/api/v1/auth/login/'


class AdminSessionAuthTests(APITestCase):
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
        cache.clear()  # reset the shared login throttle between tests
        self.session_url = reverse('admin_console:auth-session')

    def _shared_login(self, email):
        return self.client.post(
            _SHARED_LOGIN, {'email': email, 'password': 'pw12345!'}, format='json',
        )

    # --- session established by the shared login --------------------------
    def test_admin_shared_login_opens_session(self):
        resp = self._shared_login('admin@example.com')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('sessionid', resp.cookies)
        self.assertIn('csrftoken', resp.cookies)

        # The session reaches the admin-console base view.
        session_resp = self.client.get(self.session_url)
        self.assertEqual(session_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(session_resp.data['data']['user_id'], self.admin.pk)
        self.assertIn('idle_timeout_seconds', session_resp.data['data'])

    def test_non_admin_shared_login_gets_no_session(self):
        resp = self._shared_login('learner@example.com')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotIn('sessionid', resp.cookies)
        self.assertNotIn('csrftoken', resp.cookies)

    # --- base-view gate ---------------------------------------------------
    def test_session_endpoint_requires_auth(self):
        self.assertEqual(
            self.client.get(self.session_url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_logout_rejects_another_users_refresh_token(self):
        # A caller must not be able to blacklist someone else's refresh token.
        self._shared_login('admin@example.com')
        others_token = str(RefreshToken.for_user(self.learner))
        resp = self.client.post(
            _SHARED_LOGIN.replace('login', 'logout'),
            {'refresh': others_token}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_shared_logout_also_flushes_admin_session(self):
        # Logout must be symmetric with login: the shared login opened a session
        # for the admin, so the shared logout must end it (not just the JWT).
        login = self._shared_login('admin@example.com')
        refresh = login.cookies['refresh_token'].value
        resp = self.client.post('/api/v1/auth/logout/', {'refresh': refresh}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Drop the (now-cleared) JWT cookies; the session must be dead too.
        for name in ('access_token', 'refresh_token'):
            self.client.cookies.pop(name, None)
        self.assertEqual(self.client.get(self.session_url).status_code, status.HTTP_403_FORBIDDEN)

    # --- JWT fallback on the base view ------------------------------------
    def test_jwt_admin_can_use_base_view(self):
        token = RefreshToken.for_user(self.admin).access_token
        resp = self.client.get(self.session_url, HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['data']['user_id'], self.admin.pk)

    def test_jwt_learner_forbidden_on_base_view(self):
        token = RefreshToken.for_user(self.learner).access_token
        resp = self.client.get(self.session_url, HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

"""
Tests for the session-based admin login (Sprint 8, item 1).

Covers: CSRF priming, admin login success/failure paths, the admin-role gate
(403 vs 400), session liveness, logout, and the JWT fallback on the base view.
"""
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import User


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
        # AnonRateThrottle counts live in the cache; reset so repeated login
        # attempts across tests don't trip the 10/min admin-login throttle.
        cache.clear()
        self.csrf_url = reverse('admin_console:auth-csrf')
        self.login_url = reverse('admin_console:auth-login')
        self.logout_url = reverse('admin_console:auth-logout')
        self.session_url = reverse('admin_console:auth-session')

    # --- CSRF -------------------------------------------------------------
    def test_csrf_endpoint_sets_cookie(self):
        response = self.client.get(self.csrf_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertIn('csrftoken', response.cookies)

    # --- Login success ----------------------------------------------------
    def test_admin_login_success_establishes_session(self):
        response = self.client.post(
            self.login_url,
            {'email': 'admin@example.com', 'password': 'pw12345!'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['email'], 'admin@example.com')
        self.assertIn('sessionid', response.cookies)

        # Session is live: the who-am-I endpoint now resolves the admin.
        session_response = self.client.get(self.session_url)
        self.assertEqual(session_response.status_code, status.HTTP_200_OK)
        self.assertEqual(session_response.data['data']['user_id'], self.admin.pk)
        self.assertIn('idle_timeout_seconds', session_response.data['data'])

    # --- Login failure paths ---------------------------------------------
    def test_non_admin_valid_credentials_forbidden(self):
        response = self.client.post(
            self.login_url,
            {'email': 'learner@example.com', 'password': 'pw12345!'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data['success'])
        self.assertNotIn('sessionid', response.cookies)

    def test_bad_credentials_generic_400(self):
        response = self.client.post(
            self.login_url,
            {'email': 'admin@example.com', 'password': 'wrong-password'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])

    def test_unknown_email_generic_400(self):
        response = self.client.post(
            self.login_url,
            {'email': 'nobody@example.com', 'password': 'pw12345!'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_restricted_admin_rejected(self):
        self.admin.is_restricted_by_admin = True
        self.admin.save(update_fields=['is_restricted_by_admin'])
        response = self.client.post(
            self.login_url,
            {'email': 'admin@example.com', 'password': 'pw12345!'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- Session endpoint auth -------------------------------------------
    def test_session_endpoint_requires_auth(self):
        response = self.client.get(self.session_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # --- Logout -----------------------------------------------------------
    def test_logout_flushes_session(self):
        self.client.post(
            self.login_url,
            {'email': 'admin@example.com', 'password': 'pw12345!'},
            format='json',
        )
        logout_response = self.client.post(self.logout_url)
        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)

        # Session is gone: who-am-I now fails.
        session_response = self.client.get(self.session_url)
        self.assertEqual(session_response.status_code, status.HTTP_403_FORBIDDEN)

    # --- JWT fallback on the base view ------------------------------------
    def test_jwt_admin_can_use_base_view(self):
        token = RefreshToken.for_user(self.admin).access_token
        response = self.client.get(
            self.session_url,
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['user_id'], self.admin.pk)

    def test_jwt_learner_forbidden_on_base_view(self):
        token = RefreshToken.for_user(self.learner).access_token
        response = self.client.get(
            self.session_url,
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

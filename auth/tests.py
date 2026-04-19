from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from auth.models import User


GOOGLE_TOKEN_RESPONSE = {
    'access_token': 'ya29.mock-access-token',
    'token_type': 'Bearer',
    'expires_in': 3600,
    'id_token': 'mock.id.token',
}

GOOGLE_PROFILE = {
    'sub': 'google-user-123',
    'email': 'learner@example.com',
    'email_verified': True,
    'full_name': 'Google Learner',
    'given_name': 'Google',
    'family_name': 'Learner',
    'picture': 'https://example.com/avatar.png',
}

GOOGLE_SETTINGS = {
    'GOOGLE_CLIENT_ID': 'test-client-id',
    'GOOGLE_CLIENT_SECRET': 'test-client-secret',
    'GOOGLE_CALLBACK_URL': 'http://localhost:8000/api/v1/auth/google/callback/',
    'FRONTEND_URL': 'http://localhost:3000',
    'FRONTEND_GOOGLE_CALLBACK': 'http://localhost:3000/auth/google/callback',
    'FRONTEND_ERROR_URL': 'http://localhost:3000/auth/error',
}


# ---------------------------------------------------------------------------
# GET /auth/google/ — redirect to Google consent
# ---------------------------------------------------------------------------

@override_settings(**GOOGLE_SETTINGS)
class GoogleAuthRedirectViewTests(APITestCase):
    def test_redirect_to_google_consent(self):
        url = reverse('auth:google-redirect')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('accounts.google.com', response['Location'])
        self.assertIn('client_id=test-client-id', response['Location'])

    @override_settings(GOOGLE_CLIENT_ID='')
    def test_redirect_returns_503_when_not_configured(self):
        url = reverse('auth:google-redirect')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(response.data['success'])


# ---------------------------------------------------------------------------
# GET /auth/google/callback/ — forward code to frontend OR handle exchange
# ---------------------------------------------------------------------------

@override_settings(**GOOGLE_SETTINGS)
class GoogleAuthCallbackViewTests(APITestCase):
    """Tests for callback in frontend mode (FRONTEND_GOOGLE_CALLBACK is set)."""

    def test_callback_redirects_to_frontend_with_code(self):
        url = reverse('auth:google-callback')
        response = self.client.get(url, {'code': 'auth-code-123', 'state': 'abc'})
        self.assertEqual(response.status_code, 302)
        location = response['Location']
        self.assertIn('http://localhost:3000/auth/google/callback', location)
        self.assertIn('code=auth-code-123', location)
        self.assertIn('state=abc', location)

    def test_callback_redirects_to_error_url_on_error(self):
        url = reverse('auth:google-callback')
        response = self.client.get(url, {'error': 'access_denied'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('http://localhost:3000/auth/error', response['Location'])

    def test_callback_redirects_to_error_url_when_no_code(self):
        url = reverse('auth:google-callback')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('http://localhost:3000/auth/error', response['Location'])


BACKEND_ONLY_SETTINGS = {
    **GOOGLE_SETTINGS,
    'FRONTEND_GOOGLE_CALLBACK': '',
}


@override_settings(**BACKEND_ONLY_SETTINGS)
class GoogleAuthCallbackBackendOnlyTests(APITestCase):
    """Tests for callback in backend-only mode (no frontend URL)."""

    @patch('auth.all_views.google_views.fetch_google_profile')
    @patch('auth.all_views.google_views.exchange_code_for_tokens')
    def test_callback_exchanges_code_and_returns_json(self, mock_exchange, mock_profile):
        mock_exchange.return_value = GOOGLE_TOKEN_RESPONSE
        mock_profile.return_value = GOOGLE_PROFILE

        url = reverse('auth:google-callback')
        # Simulate session user_type set by the redirect view
        session = self.client.session
        session['google_oauth_user_type'] = 'learner'
        session.save()

        response = self.client.get(url, {'code': 'valid-code', 'state': 'abc'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['auth_provider'], 'google')
        self.assertIn('access_token', response.cookies)
        self.assertIn('refresh_token', response.cookies)

    def test_callback_returns_400_on_error(self):
        url = reverse('auth:google-callback')
        response = self.client.get(url, {'error': 'access_denied'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])

    def test_callback_returns_400_when_no_code(self):
        url = reverse('auth:google-callback')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])


# ---------------------------------------------------------------------------
# POST /auth/google/exchange-token/ — full exchange flow
# ---------------------------------------------------------------------------

@override_settings(**GOOGLE_SETTINGS)
class GoogleExchangeTokenViewTests(APITestCase):
    def setUp(self):
        self.url = reverse('auth:google-exchange-token')

    @patch('auth.all_views.google_views.fetch_google_profile')
    @patch('auth.all_views.google_views.exchange_code_for_tokens')
    def test_creates_new_learner_and_sets_cookies(self, mock_exchange, mock_profile):
        mock_exchange.return_value = GOOGLE_TOKEN_RESPONSE
        mock_profile.return_value = GOOGLE_PROFILE

        response = self.client.post(
            self.url, {'code': 'valid-code', 'user_type': 'learner'}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertTrue(response.data['data']['is_new_user'])
        self.assertEqual(response.data['data']['user_type'], 'learner')
        self.assertEqual(response.data['data']['auth_provider'], 'google')

        # JWT HttpOnly cookies must be set
        self.assertIn('access_token', response.cookies)
        self.assertIn('refresh_token', response.cookies)
        self.assertTrue(response.cookies['access_token']['httponly'])
        self.assertTrue(response.cookies['refresh_token']['httponly'])

        # User exists and is properly configured
        user = User.objects.get(email='learner@example.com')
        self.assertTrue(user.is_email_verified)
        self.assertTrue(user.is_verified)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(
            SocialAccount.objects.filter(user=user, provider='google', uid='google-user-123').exists()
        )

    @patch('auth.all_views.google_views.fetch_google_profile')
    @patch('auth.all_views.google_views.exchange_code_for_tokens')
    def test_creates_instructor_not_verified(self, mock_exchange, mock_profile):
        mock_exchange.return_value = GOOGLE_TOKEN_RESPONSE
        mock_profile.return_value = {
            **GOOGLE_PROFILE,
            'email': 'instructor@example.com',
            'full_name': 'Google Instructor',
        }

        response = self.client.post(
            self.url, {'code': 'valid-code', 'user_type': 'instructor'}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['user_type'], 'instructor')

        user = User.objects.get(email='instructor@example.com')
        self.assertFalse(user.is_verified)

    @patch('auth.all_views.google_views.fetch_google_profile')
    @patch('auth.all_views.google_views.exchange_code_for_tokens')
    def test_existing_user_signs_in(self, mock_exchange, mock_profile):
        mock_exchange.return_value = GOOGLE_TOKEN_RESPONSE
        mock_profile.return_value = GOOGLE_PROFILE

        user = User.objects.create_user(
            email='learner@example.com',
            password='Password123!',
            full_name='Existing User',
            user_type='learner',
            is_email_verified=True,
        )

        response = self.client.post(self.url, {'code': 'valid-code'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['data']['is_new_user'])
        self.assertEqual(response.data['data']['user_id'], user.pk)

    @patch('auth.all_views.google_views.fetch_google_profile')
    @patch('auth.all_views.google_views.exchange_code_for_tokens')
    def test_rejects_deleted_user(self, mock_exchange, mock_profile):
        mock_exchange.return_value = GOOGLE_TOKEN_RESPONSE
        mock_profile.return_value = GOOGLE_PROFILE

        User.objects.create_user(
            email='learner@example.com',
            password='Password123!',
            full_name='Deleted User',
            user_type='learner',
            is_email_verified=True,
            is_deleted=True,
            is_active=False,
        )

        response = self.client.post(self.url, {'code': 'valid-code'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data['success'])

    @patch('auth.all_views.google_views.fetch_google_profile')
    @patch('auth.all_views.google_views.exchange_code_for_tokens')
    def test_rejects_partner_institution_user(self, mock_exchange, mock_profile):
        mock_exchange.return_value = GOOGLE_TOKEN_RESPONSE
        mock_profile.return_value = {
            **GOOGLE_PROFILE,
            'email': 'contact@academy.edu',
        }

        User.objects.create_user(
            email='contact@academy.edu',
            password='Password123!',
            full_name='Academy Contact',
            user_type='partner_institution',
            is_email_verified=True,
        )

        response = self.client.post(self.url, {'code': 'valid-code'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data['success'])

    def test_rejects_partner_institution_user_type_param(self):
        response = self.client.post(
            self.url, {'code': 'valid-code', 'user_type': 'partner_institution'}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_type', response.data['errors'])

    def test_rejects_missing_code(self):
        response = self.client.post(self.url, {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])

    @patch('auth.all_views.google_views.fetch_google_profile')
    @patch('auth.all_views.google_views.exchange_code_for_tokens')
    def test_social_account_conflict_different_user(self, mock_exchange, mock_profile):
        mock_exchange.return_value = GOOGLE_TOKEN_RESPONSE
        mock_profile.return_value = GOOGLE_PROFILE

        # Create a different user already linked to this Google sub
        other_user = User.objects.create_user(
            email='other@example.com',
            password='Password123!',
            full_name='Other User',
            user_type='learner',
            is_email_verified=True,
        )
        SocialAccount.objects.create(
            user=other_user, provider='google', uid='google-user-123', extra_data={},
        )

        response = self.client.post(self.url, {'code': 'valid-code'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(response.data['success'])

    @patch('auth.all_views.google_views.fetch_google_profile')
    @patch('auth.all_views.google_views.exchange_code_for_tokens')
    def test_fills_missing_full_name_on_existing_user(self, mock_exchange, mock_profile):
        mock_exchange.return_value = GOOGLE_TOKEN_RESPONSE
        mock_profile.return_value = GOOGLE_PROFILE

        user = User(
            email='learner@example.com',
            full_name='',
            user_type='learner',
            is_email_verified=False,
        )
        user.set_password('Password123!')
        # Bypass CustomUserManager full_name validation
        user.save()

        response = self.client.post(self.url, {'code': 'valid-code'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.full_name, 'Google Learner')
        self.assertTrue(user.is_email_verified)

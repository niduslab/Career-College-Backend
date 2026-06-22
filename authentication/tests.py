from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User


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
        url = reverse('authentication:google-redirect')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('accounts.google.com', response['Location'])
        self.assertIn('client_id=test-client-id', response['Location'])

    @override_settings(GOOGLE_CLIENT_ID='')
    def test_redirect_returns_503_when_not_configured(self):
        url = reverse('authentication:google-redirect')
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
        url = reverse('authentication:google-callback')
        response = self.client.get(url, {'code': 'auth-code-123', 'state': 'abc'})
        self.assertEqual(response.status_code, 302)
        location = response['Location']
        self.assertIn('http://localhost:3000/auth/google/callback', location)
        self.assertIn('code=auth-code-123', location)
        self.assertIn('state=abc', location)

    def test_callback_redirects_to_error_url_on_error(self):
        url = reverse('authentication:google-callback')
        response = self.client.get(url, {'error': 'access_denied'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('http://localhost:3000/auth/error', response['Location'])

    def test_callback_redirects_to_error_url_when_no_code(self):
        url = reverse('authentication:google-callback')
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

    @patch('authentication.all_views.google_views.fetch_google_profile')
    @patch('authentication.all_views.google_views.exchange_code_for_tokens')
    def test_callback_exchanges_code_and_returns_json(self, mock_exchange, mock_profile):
        mock_exchange.return_value = GOOGLE_TOKEN_RESPONSE
        mock_profile.return_value = GOOGLE_PROFILE

        url = reverse('authentication:google-callback')
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
        url = reverse('authentication:google-callback')
        response = self.client.get(url, {'error': 'access_denied'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])

    def test_callback_returns_400_when_no_code(self):
        url = reverse('authentication:google-callback')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])


# ---------------------------------------------------------------------------
# POST /auth/google/exchange-token/ — full exchange flow
# ---------------------------------------------------------------------------

@override_settings(**GOOGLE_SETTINGS)
class GoogleExchangeTokenViewTests(APITestCase):
    def setUp(self):
        self.url = reverse('authentication:google-exchange-token')

    @patch('authentication.all_views.google_views.fetch_google_profile')
    @patch('authentication.all_views.google_views.exchange_code_for_tokens')
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

    @patch('authentication.all_views.google_views.fetch_google_profile')
    @patch('authentication.all_views.google_views.exchange_code_for_tokens')
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

    @patch('authentication.all_views.google_views.fetch_google_profile')
    @patch('authentication.all_views.google_views.exchange_code_for_tokens')
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

    @patch('authentication.all_views.google_views.fetch_google_profile')
    @patch('authentication.all_views.google_views.exchange_code_for_tokens')
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

    @patch('authentication.all_views.google_views.fetch_google_profile')
    @patch('authentication.all_views.google_views.exchange_code_for_tokens')
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

    @patch('authentication.all_views.google_views.fetch_google_profile')
    @patch('authentication.all_views.google_views.exchange_code_for_tokens')
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

    @patch('authentication.all_views.google_views.fetch_google_profile')
    @patch('authentication.all_views.google_views.exchange_code_for_tokens')
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


# ---------------------------------------------------------------------------
# provision_expert — institution onboards an expert with a preset password
# ---------------------------------------------------------------------------

class ProvisionExpertTests(APITestCase):
    def setUp(self):
        from authentication.models import PartnerInstitutionProfile

        institution_user = User.objects.create_user(
            email='institution@example.com',
            password='InstPass123!',
            full_name='Acme University',
            user_type='partner_institution',
            is_email_verified=True,
        )
        # Profile auto-created by signal; mark it verified + active.
        self.institution = institution_user.partner_institution_profile
        self.institution.is_verified = True
        self.institution.is_active = True
        self.institution.save(update_fields=['is_verified', 'is_active'])
        assert isinstance(self.institution, PartnerInstitutionProfile)

        from authentication.models import Department
        self.department = Department.objects.create(
            institution=self.institution, name='Computer Science',
        )

    @patch('notifications.tasks.send_notification_email_task.delay')
    @patch('authentication.tasks.send_expert_credentials_email_task.delay')
    def test_provision_creates_verified_loginable_expert(self, mock_delay, _mock_notif_delay):
        from authentication.services.expert_service import provision_expert

        with self.captureOnCommitCallbacks(execute=True):
            profile = provision_expert(
                self.institution,
                full_name='Jane Expert',
                email='jane.expert@example.com',
                department_id=self.department.id,
            )

        user = profile.user
        # Account is loginable immediately (no OTP step).
        self.assertTrue(user.is_email_verified)
        self.assertEqual(user.user_type, 'instructor')
        # Institution vouches — profile auto-verified.
        self.assertTrue(profile.is_verified)
        self.assertEqual(profile.affiliation_status, 'active')
        self.assertEqual(profile.department, self.department)

        # Credentials email enqueued async (not an OTP) with a usable password.
        self.assertTrue(mock_delay.called)
        sent_user_pk, sent_password = mock_delay.call_args.args[0], mock_delay.call_args.args[1]
        self.assertEqual(sent_user_pk, user.pk)
        self.assertTrue(sent_password)
        self.assertTrue(user.check_password(sent_password))
        # No lingering activation OTP.
        self.assertIsNone(user.otp_code)

    @patch('notifications.tasks.send_notification_email_task.delay')
    @patch('authentication.tasks.send_expert_credentials_email_task.delay')
    def test_provision_does_not_persist_password_in_notification(self, mock_delay, _mock_notif_delay):
        from authentication.services.expert_service import provision_expert
        from notifications.models import Notification

        with self.captureOnCommitCallbacks(execute=True):
            profile = provision_expert(
                self.institution,
                full_name='Bob Expert',
                email='bob.expert@example.com',
            )

        sent_password = mock_delay.call_args.args[1]
        notif = Notification.objects.filter(recipient=profile.user).first()
        self.assertIsNotNone(notif)
        self.assertNotIn(sent_password, str(notif.data))


# ---------------------------------------------------------------------------
# Institution department management (CRUD) + cross-institution validation
# ---------------------------------------------------------------------------

def _make_verified_institution(email, name):
    user = User.objects.create_user(
        email=email, password='InstPass123!', full_name=name,
        user_type='partner_institution', is_email_verified=True,
    )
    profile = user.partner_institution_profile
    profile.is_verified = True
    profile.is_active = True
    profile.save(update_fields=['is_verified', 'is_active'])
    return user, profile


class InstitutionDepartmentAPITests(APITestCase):
    def setUp(self):
        self.user, self.institution = _make_verified_institution(
            'inst@example.com', 'Acme University',
        )
        self.other_user, self.other_institution = _make_verified_institution(
            'other@example.com', 'Other University',
        )
        self.client.force_authenticate(self.user)

    def test_create_department(self):
        resp = self.client.post(
            reverse('authentication:institution-department-list-create'),
            {'name': 'Computer Science'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['data']['name'], 'Computer Science')
        self.assertTrue(resp.data['data']['is_active'])

    def test_duplicate_name_case_insensitive_422(self):
        from authentication.models import Department
        Department.objects.create(institution=self.institution, name='Physics')
        resp = self.client.post(
            reverse('authentication:institution-department-list-create'),
            {'name': 'physics'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_list_active_only_by_default(self):
        from authentication.models import Department
        Department.objects.create(institution=self.institution, name='Active Dept')
        Department.objects.create(
            institution=self.institution, name='Dead Dept', is_active=False,
        )
        # Another institution's dept must never appear.
        Department.objects.create(institution=self.other_institution, name='Foreign Dept')

        resp = self.client.get(reverse('authentication:institution-department-list-create'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [d['name'] for d in resp.data['data']['results']]
        self.assertEqual(names, ['Active Dept'])

    def test_rename_department(self):
        from authentication.models import Department
        dept = Department.objects.create(institution=self.institution, name='Old Name')
        resp = self.client.patch(
            reverse('authentication:institution-department-detail', kwargs={'department_id': dept.id}),
            {'name': 'New Name'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        dept.refresh_from_db()
        self.assertEqual(dept.name, 'New Name')

    def test_delete_soft_deactivates(self):
        from authentication.models import Department
        dept = Department.objects.create(institution=self.institution, name='Temp')
        resp = self.client.delete(
            reverse('authentication:institution-department-detail', kwargs={'department_id': dept.id}),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        dept.refresh_from_db()
        self.assertFalse(dept.is_active)  # row kept, just deactivated

    def test_foreign_department_404(self):
        from authentication.models import Department
        foreign = Department.objects.create(institution=self.other_institution, name='Foreign')
        resp = self.client.get(
            reverse('authentication:institution-department-detail', kwargs={'department_id': foreign.id}),
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_unverified_institution_403(self):
        unv_user = User.objects.create_user(
            email='unv@example.com', password='InstPass123!', full_name='Unverified Inst',
            user_type='partner_institution', is_email_verified=True,
        )  # profile stays unverified
        self.client.force_authenticate(unv_user)
        resp = self.client.get(reverse('authentication:institution-department-list-create'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class ExpertDepartmentValidationTests(APITestCase):
    def setUp(self):
        from authentication.models import Department
        self.user, self.institution = _make_verified_institution(
            'inst2@example.com', 'Acme University',
        )
        _, self.other_institution = _make_verified_institution(
            'other2@example.com', 'Other University',
        )
        self.foreign_dept = Department.objects.create(
            institution=self.other_institution, name='Foreign Dept',
        )

    @patch('notifications.tasks.send_notification_email_task.delay')
    @patch('authentication.tasks.send_expert_credentials_email_task.delay')
    def test_foreign_department_rejected_422(self, _m1, _m2):
        from authentication.services.expert_service import provision_expert, ExpertError
        with self.assertRaises(ExpertError) as ctx:
            provision_expert(
                self.institution,
                full_name='Jane Expert',
                email='jane@example.com',
                department_id=self.foreign_dept.id,
            )
        self.assertEqual(ctx.exception.http_status, 422)
        # No account created when validation fails before the txn.
        self.assertFalse(User.objects.filter(email='jane@example.com').exists())

    @patch('notifications.tasks.send_notification_email_task.delay')
    @patch('authentication.tasks.send_expert_credentials_email_task.delay')
    def test_omitted_department_is_null(self, _m1, _m2):
        from authentication.services.expert_service import provision_expert
        with self.captureOnCommitCallbacks(execute=True):
            profile = provision_expert(
                self.institution, full_name='No Dept', email='nodept@example.com',
            )
        self.assertIsNone(profile.department)


# ---------------------------------------------------------------------------
# Async auth-email wiring — views enqueue the Celery task, not a sync send
# ---------------------------------------------------------------------------

class AsyncOTPEmailWiringTests(APITestCase):
    @patch('authentication.tasks.send_otp_email_task.delay')
    def test_register_enqueues_otp_task(self, mock_delay):
        resp = self.client.post(reverse('authentication:register'), {
            'email': 'newlearner@example.com',
            'full_name': 'New Learner',
            'password': 'StrongPass123!',
            'confirm_password': 'StrongPass123!',
            'user_type': 'learner',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        user = User.objects.get(email='newlearner@example.com')
        mock_delay.assert_called_once_with(user.pk, user.otp_code, 'registration')

    @patch('authentication.tasks.send_otp_email_task.delay', side_effect=Exception('broker down'))
    def test_register_returns_503_when_enqueue_fails(self, _mock_delay):
        resp = self.client.post(reverse('authentication:register'), {
            'email': 'enqfail@example.com',
            'full_name': 'Enqueue Fail',
            'password': 'StrongPass123!',
            'confirm_password': 'StrongPass123!',
            'user_type': 'learner',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(resp.data['otp_sent'])
        # Account is still created (committed before the enqueue) — recoverable via resend.
        self.assertTrue(User.objects.filter(email='enqfail@example.com').exists())

    @patch('authentication.tasks.send_otp_email_task.delay')
    def test_resend_enqueues_otp_task(self, mock_delay):
        User.objects.create_user(
            email='unverified@example.com', password='StrongPass123!',
            full_name='Unverified User', user_type='learner', is_email_verified=False,
        )
        resp = self.client.post(reverse('authentication:otp-resend'), {
            'email': 'unverified@example.com', 'purpose': 'registration',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(mock_delay.called)
        self.assertEqual(mock_delay.call_args.args[2], 'registration')

    @patch('authentication.tasks.send_otp_email_task.delay')
    def test_forgot_password_enqueues_task_for_known_email(self, mock_delay):
        User.objects.create_user(
            email='verified@example.com', password='StrongPass123!',
            full_name='Verified User', user_type='learner', is_email_verified=True,
        )
        resp = self.client.post(reverse('authentication:password-forgot'), {
            'email': 'verified@example.com',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(mock_delay.called)
        self.assertEqual(mock_delay.call_args.args[2], 'password_reset')

    @patch('authentication.tasks.send_otp_email_task.delay')
    def test_forgot_password_unknown_email_is_silent_noop(self, mock_delay):
        resp = self.client.post(reverse('authentication:password-forgot'), {
            'email': 'nobody@example.com',
        }, format='json')

        # Generic 200 (no user enumeration) and no task enqueued.
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(mock_delay.called)


class CredentialsEmailRenderTests(APITestCase):
    """Exercises the real template render + send (locmem backend) so a broken
    expert_credentials.html template is caught, not silently swallowed."""

    def test_send_credentials_email_renders_and_sends(self):
        from authentication.utils import send_credentials_email

        user = User.objects.create_user(
            email='expert@example.com', password='ignored-hash',
            full_name='Jane Expert', user_type='instructor', is_email_verified=True,
        )

        ok = send_credentials_email(user, 'TempPass123!', 'Acme University')

        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn('expert@example.com', msg.body)
        self.assertIn('TempPass123!', msg.body)
        self.assertIn('Acme University', msg.body)
        # HTML alternative renders the same credentials.
        html_body = msg.alternatives[0][0]
        self.assertIn('TempPass123!', html_body)

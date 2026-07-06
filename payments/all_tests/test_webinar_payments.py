from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from payments.all_tests.factories import make_user, valid_validation_response
from payments.models import Order
from payments.services import finalize_payment
from webinars.models import Webinar, WebinarRegistration

CHECKOUT_URL = reverse('payments:checkout')

_SESSION_OK = {'status': 'SUCCESS', 'GatewayPageURL': 'https://sandbox.sslcommerz.com/gw/pay/web'}


def make_webinar(institution_user, *, slug, price='0.00', published=True, max_capacity=None,
                 scheduled_in_days=7):
    return Webinar.objects.create(
        created_by=institution_user,
        partner_institution=institution_user.partner_institution_profile,
        title=slug.replace('-', ' ').title(),
        slug=slug,
        description='Webinar payment test.',
        status=(
            Webinar.WebinarStatus.PUBLISHED if published else Webinar.WebinarStatus.DRAFT
        ),
        price=Decimal(price),
        max_capacity=max_capacity,
        scheduled_at=timezone.now() + timedelta(days=scheduled_in_days),
        duration_minutes=60,
        meeting_url='https://meet.example.com/x',
    )


@override_settings(SSLCOMMERZ_STORE_ID='test-store')
class WebinarPaymentTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inst_user = make_user('winst@pay.com', user_type='partner_institution')
        cls.inst_user.partner_institution_profile.institution_name = 'Pay Institute'
        cls.inst_user.partner_institution_profile.is_verified = True
        cls.inst_user.partner_institution_profile.is_active = True
        cls.inst_user.partner_institution_profile.save()

        cls.learner = make_user('wlearner@pay.com')
        cls.paid_webinar = make_webinar(cls.inst_user, slug='paid-webinar', price='15.00')
        cls.free_webinar = make_webinar(cls.inst_user, slug='free-webinar', price='0.00')

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.learner)

    def _checkout(self, slug):
        return self.client.post(CHECKOUT_URL, {'webinar_slug': slug}, format='json')

    def _order(self, webinar=None, tran_suffix='01', **kwargs):
        defaults = dict(
            user=self.learner,
            webinar=webinar or self.paid_webinar,
            amount=Decimal('15.00'),
            tran_id=f'CCWEBINAR000000000000000{tran_suffix}',
            status=Order.Status.PROCESSING,
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    # ── Checkout ──

    def test_both_slugs_returns_400(self):
        self.auth()
        response = self.client.post(
            CHECKOUT_URL,
            {'course_slug': 'x', 'webinar_slug': 'y'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_free_webinar_checkout_returns_422(self):
        self.auth()
        response = self._checkout(self.free_webinar.slug)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_already_registered_returns_422(self):
        WebinarRegistration.objects.create(
            user=self.learner, webinar=self.paid_webinar, is_active=True,
        )
        self.auth()
        response = self._checkout(self.paid_webinar.slug)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_past_webinar_returns_422(self):
        past = make_webinar(self.inst_user, slug='past-webinar', price='15.00', scheduled_in_days=-1)
        self.auth()
        response = self._checkout(past.slug)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_full_webinar_returns_422(self):
        capped = make_webinar(self.inst_user, slug='full-webinar', price='15.00', max_capacity=1)
        other = make_user('wother@pay.com')
        WebinarRegistration.objects.create(user=other, webinar=capped, is_active=True)
        self.auth()
        response = self._checkout(capped.slug)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('payments.services.order_service.initiate_session', return_value=_SESSION_OK)
    def test_happy_path_creates_webinar_order(self, mock_initiate):
        self.auth()
        response = self._checkout(self.paid_webinar.slug)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data['data']
        self.assertEqual(data['item_type'], 'webinar')
        self.assertEqual(data['amount'], '15.00')

        order = Order.objects.get(tran_id=data['tran_id'])
        self.assertEqual(order.webinar, self.paid_webinar)
        self.assertIsNone(order.course)
        self.assertEqual(order.status, Order.Status.PROCESSING)

    # ── Finalize ──

    def test_finalize_creates_active_registration(self):
        order = self._order(tran_suffix='02')
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order),
        ):
            finalize_payment(order.tran_id, 'VAL0001')

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(
            WebinarRegistration.objects.filter(
                user=self.learner, webinar=self.paid_webinar, is_active=True,
            ).exists()
        )

    def test_finalize_honors_payment_even_at_capacity(self):
        capped = make_webinar(self.inst_user, slug='cap-race-webinar', price='15.00', max_capacity=1)
        other = make_user('wrace@pay.com')
        WebinarRegistration.objects.create(user=other, webinar=capped, is_active=True)
        order = self._order(webinar=capped, tran_suffix='03')

        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order),
        ):
            finalize_payment(order.tran_id, 'VAL0001')

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        # Money moved → registration honored despite the cap (logged overshoot).
        self.assertTrue(
            WebinarRegistration.objects.filter(
                user=self.learner, webinar=capped, is_active=True,
            ).exists()
        )

    # ── Registration gate ──

    def test_paid_webinar_free_register_rejected_without_purchase(self):
        self.auth()
        response = self.client.post(
            reverse('webinars:webinar-register', kwargs={'slug': self.paid_webinar.slug})
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertFalse(
            WebinarRegistration.objects.filter(
                user=self.learner, webinar=self.paid_webinar,
            ).exists()
        )

    def test_paid_webinar_register_succeeds_with_paid_order(self):
        self._order(tran_suffix='04', status=Order.Status.PAID)
        self.auth()
        response = self.client.post(
            reverse('webinars:webinar-register', kwargs={'slug': self.paid_webinar.slug})
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            WebinarRegistration.objects.filter(
                user=self.learner, webinar=self.paid_webinar, is_active=True,
            ).exists()
        )

    def test_free_webinar_register_unchanged(self):
        self.auth()
        response = self.client.post(
            reverse('webinars:webinar-register', kwargs={'slug': self.free_webinar.slug})
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Enrollment
from notifications.models import Notification, NotificationEventType
from payments.all_tests.factories import (
    make_course,
    make_user,
    signed_callback,
    valid_validation_response,
)
from payments.models import Order
from payments.services import PaymentError

IPN_URL = reverse('payments:ipn')
SUCCESS_URL = reverse('payments:success')
FAIL_URL = reverse('payments:fail')
CANCEL_URL = reverse('payments:cancel')


@override_settings(SSLCOMMERZ_STORE_ID='test-store')
class CallbackTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = make_user('cb_ins@pay.com', user_type='instructor')
        cls.learner = make_user('cb_learner@pay.com')
        cls.course = make_course(cls.instructor, slug='callback-course', price='25.00')

    def _order(self, tran_suffix='01', **kwargs):
        defaults = dict(
            user=self.learner,
            course=self.course,
            amount=Decimal('25.00'),
            tran_id=f'CCCALLBACK00000000000000{tran_suffix}',
            status=Order.Status.PROCESSING,
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    # ── IPN ──

    def test_ipn_valid_finalizes_payment(self):
        order = self._order('01')
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order),
        ):
            response = self.client.post(IPN_URL, {
                'tran_id': order.tran_id, 'val_id': 'VAL0001', 'status': 'VALID',
            })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(
            Enrollment.objects.filter(
                user=self.learner, course=self.course, is_active=True,
            ).exists()
        )

    def test_ipn_unknown_tran_id_returns_200(self):
        response = self.client.post(IPN_URL, {
            'tran_id': 'CCUNKNOWN0000000000000001', 'val_id': 'VAL0001', 'status': 'VALID',
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_ipn_transient_gateway_failure_signals_retry(self):
        # A gateway-unreachable error (PaymentError 503) must NOT be acked as
        # 200 — that would strand the order in `processing` with no retry.
        order = self._order('07')
        with patch(
            'payments.services.order_service.validate_transaction',
            side_effect=PaymentError('Payment gateway is currently unavailable. Please try again.', 503),
        ):
            response = self.client.post(IPN_URL, {
                'tran_id': order.tran_id, 'val_id': 'VAL0001', 'status': 'VALID',
            })
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)  # untouched, awaits retry

    def test_ipn_validation_rejected_is_acked_200(self):
        # A permanent rejection (422) must be acked so the gateway stops retrying.
        order = self._order('08')
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order, amount='0.01'),  # tamper → 422
        ):
            response = self.client.post(IPN_URL, {
                'tran_id': order.tran_id, 'val_id': 'VAL0001', 'status': 'VALID',
            })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FAILED)

    def test_ipn_failed_marks_order_and_notifies(self):
        order = self._order('02')
        payload = signed_callback(tran_id=order.tran_id, status='FAILED')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(IPN_URL, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FAILED)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.learner,
                event_type=NotificationEventType.PAYMENT_FAILED,
            ).exists()
        )

    def test_ipn_unsigned_failed_is_ignored(self):
        # No verify_sign → the body-trusted FAILED path must not fire.
        order = self._order('20')
        response = self.client.post(IPN_URL, {'tran_id': order.tran_id, 'status': 'FAILED'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)  # untouched

    def test_ipn_forged_signature_failed_is_ignored(self):
        order = self._order('21')
        response = self.client.post(IPN_URL, {
            'tran_id': order.tran_id, 'status': 'FAILED',
            'verify_key': 'tran_id,status', 'verify_sign': 'deadbeefdeadbeefdeadbeefdeadbeef',
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)

    def test_ipn_needs_no_authentication(self):
        # No force_authenticate anywhere in this class — implicit, but assert
        # the endpoint doesn't 401.
        response = self.client.post(IPN_URL, {})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # ── Success redirect ──

    def test_success_redirects_to_frontend_and_finalizes(self):
        order = self._order('03')
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order),
        ):
            response = self.client.post(SUCCESS_URL, {
                'tran_id': order.tran_id, 'val_id': 'VAL0001', 'status': 'VALID',
            })

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn('/payment/success', response['Location'])
        self.assertIn(order.tran_id, response['Location'])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)

    def test_success_with_bad_validation_redirects_to_fail(self):
        order = self._order('04')
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order, amount='0.01'),
        ):
            response = self.client.post(SUCCESS_URL, {
                'tran_id': order.tran_id, 'val_id': 'VAL0001', 'status': 'VALID',
            })

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn('/payment/fail', response['Location'])

    # ── Fail / cancel ──

    def test_fail_callback_cannot_clobber_paid_order(self):
        order = self._order('05', status=Order.Status.PAID)
        payload = signed_callback(tran_id=order.tran_id, status='FAILED')
        response = self.client.post(FAIL_URL, payload)

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)  # terminal, untouched

    def test_cancel_callback_marks_cancelled(self):
        order = self._order('06')
        payload = signed_callback(tran_id=order.tran_id, status='CANCELLED')
        response = self.client.post(CANCEL_URL, payload)

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn('/payment/cancel', response['Location'])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_unsigned_cancel_callback_leaves_order_untouched(self):
        order = self._order('22')
        response = self.client.post(CANCEL_URL, {'tran_id': order.tran_id, 'status': 'CANCELLED'})
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)  # still redirects
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)  # but not cancelled

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from courses.models import Enrollment
from payments.all_tests.factories import make_course, make_user, valid_validation_response
from payments.models import Order
from payments.services import PaymentError, reconcile_pending_order, verify_callback_signature
from payments.tasks import reap_stale_processing_orders_task


def _query_response(order, status='VALID', val_id='VAL0001'):
    """Shape of the SSLCommerz transaction-query API response."""
    return {
        'APIConnect': 'DONE',
        'no_of_trans_found': 1,
        'element': [{
            'tran_id': order.tran_id,
            'val_id': val_id,
            'status': status,
            'amount': str(order.amount),
            'currency': order.currency,
            'store_id': 'test-store',
        }],
    }


@override_settings(SSLCOMMERZ_STORE_ID='test-store')
class ReconcilePendingOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = make_user('rec_ins@pay.com', user_type='instructor')
        cls.learner = make_user('rec_learner@pay.com')
        cls.course = make_course(cls.instructor, slug='reconcile-course', price='20.00')

    def _order(self, suffix='01', **kwargs):
        defaults = dict(
            user=self.learner, course=self.course, amount=Decimal('20.00'),
            tran_id=f'CCRECONCILE0000000000000{suffix}', status=Order.Status.PROCESSING,
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def test_reconcile_valid_finalizes(self):
        order = self._order('01')
        with patch(
            'payments.services.order_service.query_transaction',
            return_value=_query_response(order, status='VALID'),
        ), patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order),
        ):
            outcome = reconcile_pending_order(order)

        self.assertEqual(outcome, 'paid')
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(
            Enrollment.objects.filter(user=self.learner, course=self.course, is_active=True).exists()
        )

    def test_reconcile_failed_marks_failed(self):
        order = self._order('02')
        with patch(
            'payments.services.order_service.query_transaction',
            return_value=_query_response(order, status='FAILED'),
        ):
            outcome = reconcile_pending_order(order)

        self.assertEqual(outcome, 'failed')
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FAILED)

    def test_reconcile_not_found_stays_pending(self):
        order = self._order('03')
        with patch(
            'payments.services.order_service.query_transaction',
            return_value={'APIConnect': 'DONE', 'no_of_trans_found': 0, 'element': []},
        ):
            outcome = reconcile_pending_order(order)

        self.assertEqual(outcome, 'pending')
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)


@override_settings(SSLCOMMERZ_STORE_ID='test-store')
class ReaperTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = make_user('reap_ins@pay.com', user_type='instructor')
        cls.learner = make_user('reap_learner@pay.com')
        cls.course = make_course(cls.instructor, slug='reap-course', price='20.00')

    def _stale_order(self, suffix, age_minutes, status=Order.Status.PROCESSING):
        order = Order.objects.create(
            user=self.learner, course=self.course, amount=Decimal('20.00'),
            tran_id=f'CCREAP00000000000000000{suffix}', status=status,
        )
        # updated_at is auto_now; force it into the past.
        past = timezone.now() - timedelta(minutes=age_minutes)
        Order.objects.filter(pk=order.pk).update(updated_at=past)
        order.refresh_from_db()
        return order

    def test_fresh_orders_skipped(self):
        self._stale_order('01', age_minutes=1)  # younger than 15-min cutoff
        with patch('payments.services.order_service.query_transaction') as q:
            tally = reap_stale_processing_orders_task.run()
        q.assert_not_called()
        self.assertEqual(tally, {'paid': 0, 'failed': 0, 'pending': 0, 'abandoned': 0, 'errors': 0})

    def test_stale_valid_order_finalized(self):
        order = self._stale_order('02', age_minutes=30)
        with patch(
            'payments.services.order_service.query_transaction',
            return_value=_query_response(order, status='VALID'),
        ), patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order),
        ):
            tally = reap_stale_processing_orders_task.run()
        self.assertEqual(tally['paid'], 1)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)

    def test_old_pending_order_abandoned(self):
        order = self._stale_order('03', age_minutes=60 * 25)  # older than 24h
        with patch(
            'payments.services.order_service.query_transaction',
            return_value={'element': []},
        ):
            tally = reap_stale_processing_orders_task.run()
        self.assertEqual(tally['abandoned'], 1)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FAILED)

    def test_gateway_down_leaves_order_for_retry(self):
        order = self._stale_order('04', age_minutes=30)
        with patch(
            'payments.services.order_service.query_transaction',
            side_effect=PaymentError('gateway down', 503),
        ):
            tally = reap_stale_processing_orders_task.run()
        self.assertEqual(tally['errors'], 1)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)  # untouched, retried next run


class SignatureVerificationTests(TestCase):
    @override_settings(SSLCOMMERZ_STORE_PASSWORD='secret')
    def test_valid_signature_accepted(self):
        from payments.all_tests.factories import signed_callback
        payload = signed_callback(tran_id='CCX', status='VALID', amount='20.00')
        self.assertTrue(verify_callback_signature(payload))

    @override_settings(SSLCOMMERZ_STORE_PASSWORD='secret')
    def test_tampered_field_rejected(self):
        from payments.all_tests.factories import signed_callback
        payload = signed_callback(tran_id='CCX', status='VALID', amount='20.00')
        payload['amount'] = '0.01'  # tamper after signing
        self.assertFalse(verify_callback_signature(payload))

    def test_missing_signature_rejected(self):
        self.assertFalse(verify_callback_signature({'tran_id': 'CCX', 'status': 'FAILED'}))

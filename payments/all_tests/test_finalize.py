from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from courses.models import Enrollment, NidusCourse
from payments.all_tests.factories import make_course, make_user, valid_validation_response
from payments.models import Order
from payments.services import PaymentError, finalize_payment


@override_settings(SSLCOMMERZ_STORE_ID='test-store')
class FinalizePaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = make_user('fin_ins@pay.com', user_type='instructor')
        cls.learner = make_user('fin_learner@pay.com')
        cls.course = make_course(cls.instructor, slug='finalize-course', price='99.50')

    def _order(self, tran_suffix='01', **kwargs):
        defaults = dict(
            user=self.learner,
            course=self.course,
            amount=Decimal('99.50'),
            tran_id=f'CCFINALIZE00000000000000{tran_suffix}',
            status=Order.Status.PROCESSING,
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def test_happy_path_marks_paid_and_enrolls(self):
        order = self._order()
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order),
        ):
            result = finalize_payment(order.tran_id, 'VAL0001')

        result.refresh_from_db()
        self.assertEqual(result.status, Order.Status.PAID)
        self.assertEqual(result.val_id, 'VAL0001')
        self.assertIsNotNone(result.paid_at)

        enrollment = Enrollment.objects.get(user=self.learner, course=self.course)
        self.assertTrue(enrollment.is_active)
        self.assertEqual(enrollment.enrollment_type, Enrollment.EnrollmentType.PAID)

    def test_validated_status_accepted(self):
        order = self._order('02')
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order, status='VALIDATED'),
        ):
            finalize_payment(order.tran_id, 'VAL0001')
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)

    def test_unknown_tran_id_raises_404(self):
        with self.assertRaises(PaymentError) as ctx:
            finalize_payment('CCDOESNOTEXIST00000000001', 'VAL0001')
        self.assertEqual(ctx.exception.http_status, 404)

    def _assert_rejected(self, order, response_overrides):
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order, **response_overrides),
        ):
            with self.assertRaises(PaymentError) as ctx:
                finalize_payment(order.tran_id, 'VAL0001')
        self.assertEqual(ctx.exception.http_status, 422)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FAILED)
        self.assertFalse(
            Enrollment.objects.filter(user=order.user, course=order.course).exists()
        )

    def test_failed_gateway_status_rejected(self):
        self._assert_rejected(self._order('03'), {'status': 'FAILED'})

    def test_amount_tamper_rejected(self):
        self._assert_rejected(self._order('04'), {'amount': '1.00'})

    def test_currency_mismatch_rejected(self):
        self._assert_rejected(self._order('05'), {'currency': 'USD'})

    def test_store_id_mismatch_rejected(self):
        self._assert_rejected(self._order('06'), {'store_id': 'attacker-store'})

    def test_double_finalize_is_idempotent(self):
        order = self._order('07')
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order),
        ) as mock_validate:
            finalize_payment(order.tran_id, 'VAL0001')
            finalize_payment(order.tran_id, 'VAL0001')

        # Second call short-circuited before the network hop.
        self.assertEqual(mock_validate.call_count, 1)
        self.assertEqual(
            Enrollment.objects.filter(user=self.learner, course=self.course).count(), 1,
        )
        self.assertEqual(
            Order.objects.filter(
                user=self.learner, course=self.course, status=Order.Status.PAID,
            ).count(),
            1,
        )

    def test_unpublished_course_still_enrolls(self):
        order = self._order('08')
        NidusCourse.objects.filter(pk=self.course.pk).update(
            status=NidusCourse.CourseStatus.ARCHIVED, is_published=False,
        )
        try:
            with patch(
                'payments.services.order_service.validate_transaction',
                return_value=valid_validation_response(order),
            ):
                finalize_payment(order.tran_id, 'VAL0001')

            order.refresh_from_db()
            self.assertEqual(order.status, Order.Status.PAID)
            self.assertTrue(
                Enrollment.objects.filter(
                    user=self.learner, course=self.course, is_active=True,
                ).exists()
            )
        finally:
            NidusCourse.objects.filter(pk=self.course.pk).update(
                status=NidusCourse.CourseStatus.PUBLISHED, is_published=True,
            )

    def test_duplicate_payment_flagged_for_refund(self):
        self._order('09', status=Order.Status.PAID)
        second = self._order('10')
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(second),
        ):
            result = finalize_payment(second.tran_id, 'VAL0002')

        result.refresh_from_db()
        self.assertEqual(result.status, Order.Status.FAILED)
        self.assertTrue(result.gateway_payload.get('requires_refund'))
        # Enrollment still granted — the learner did pay.
        self.assertTrue(
            Enrollment.objects.filter(
                user=self.learner, course=self.course, is_active=True,
            ).exists()
        )

    def test_missing_store_id_tolerated_in_sandbox(self):
        # SANDBOX defaults True in this class's override — absent store_id skips.
        order = self._order('12')
        resp = valid_validation_response(order)
        del resp['store_id']
        with patch(
            'payments.services.order_service.validate_transaction', return_value=resp,
        ):
            finalize_payment(order.tran_id, 'VAL0001')
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)

    @override_settings(SSLCOMMERZ_SANDBOX=False)
    def test_missing_store_id_rejected_in_production(self):
        order = self._order('13')
        resp = valid_validation_response(order)
        del resp['store_id']
        with patch(
            'payments.services.order_service.validate_transaction', return_value=resp,
        ):
            with self.assertRaises(PaymentError) as ctx:
                finalize_payment(order.tran_id, 'VAL0001')
        self.assertEqual(ctx.exception.http_status, 422)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FAILED)
        self.assertFalse(
            Enrollment.objects.filter(user=self.learner, course=self.course).exists()
        )

    def test_already_enrolled_learner_does_not_break_finalize(self):
        Enrollment.objects.create(
            user=self.learner, course=self.course,
            enrollment_type=Enrollment.EnrollmentType.ADMIN_GRANTED, is_active=True,
        )
        order = self._order('11')
        with patch(
            'payments.services.order_service.validate_transaction',
            return_value=valid_validation_response(order),
        ):
            finalize_payment(order.tran_id, 'VAL0001')
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)

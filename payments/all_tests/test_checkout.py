from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Enrollment
from payments.all_tests.factories import make_course, make_user
from payments.models import Order
from payments.services import PaymentError

CHECKOUT_URL = reverse('payments:checkout')

_SESSION_OK = {'status': 'SUCCESS', 'GatewayPageURL': 'https://sandbox.sslcommerz.com/gw/pay/abc'}


class CheckoutTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = make_user('ins@pay.com', user_type='instructor')
        cls.learner = make_user('learner@pay.com')
        cls.paid_course = make_course(cls.instructor, slug='paid-course', price='49.00')
        cls.free_course = make_course(cls.instructor, slug='free-course', price='0.00')

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.learner)

    def _post(self, slug):
        return self.client.post(CHECKOUT_URL, {'course_slug': slug}, format='json')

    def test_requires_authentication(self):
        response = self._post(self.paid_course.slug)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_learner_forbidden(self):
        self.auth(self.instructor)
        response = self._post(self.paid_course.slug)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_missing_course_slug_returns_400(self):
        self.auth()
        response = self.client.post(CHECKOUT_URL, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('course_slug', response.data['errors'])

    def test_free_course_returns_422(self):
        self.auth()
        response = self._post(self.free_course.slug)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(Order.objects.count(), 0)

    def test_already_enrolled_returns_422(self):
        Enrollment.objects.create(
            user=self.learner, course=self.paid_course,
            enrollment_type=Enrollment.EnrollmentType.PAID, is_active=True,
        )
        self.auth()
        response = self._post(self.paid_course.slug)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_already_paid_returns_422(self):
        Order.objects.create(
            user=self.learner, course=self.paid_course,
            amount=Decimal('49.00'), tran_id='CCPAIDALREADY000000000001',
            status=Order.Status.PAID,
        )
        self.auth()
        response = self._post(self.paid_course.slug)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch('payments.services.order_service.initiate_session', return_value=_SESSION_OK)
    def test_happy_path_creates_processing_order(self, mock_initiate):
        self.auth()
        response = self._post(self.paid_course.slug)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data['data']
        self.assertEqual(data['gateway_url'], _SESSION_OK['GatewayPageURL'])
        self.assertEqual(data['amount'], '49.00')
        self.assertEqual(data['currency'], 'BDT')

        order = Order.objects.get(tran_id=data['tran_id'])
        self.assertEqual(order.status, Order.Status.PROCESSING)
        self.assertEqual(order.amount, Decimal('49.00'))
        self.assertEqual(order.user, self.learner)
        mock_initiate.assert_called_once()

    @patch('payments.services.order_service.initiate_session', return_value=_SESSION_OK)
    def test_recheckout_cancels_stale_pending_order(self, mock_initiate):
        self.auth()
        first = self._post(self.paid_course.slug).data['data']
        second = self._post(self.paid_course.slug).data['data']

        self.assertNotEqual(first['tran_id'], second['tran_id'])
        self.assertEqual(
            Order.objects.get(tran_id=first['tran_id']).status, Order.Status.CANCELLED,
        )
        self.assertEqual(
            Order.objects.get(tran_id=second['tran_id']).status, Order.Status.PROCESSING,
        )

    @patch(
        'payments.services.order_service.initiate_session',
        side_effect=PaymentError('Payment gateway is currently unavailable. Please try again.', 503),
    )
    def test_gateway_down_returns_503_and_fails_order(self, mock_initiate):
        self.auth()
        response = self._post(self.paid_course.slug)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        order = Order.objects.get(user=self.learner, course=self.paid_course)
        self.assertEqual(order.status, Order.Status.FAILED)

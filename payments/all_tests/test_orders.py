from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from payments.all_tests.factories import make_course, make_user
from payments.models import Order

LIST_URL = reverse('payments:order-list')


class OrderEndpointTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = make_user('ord_ins@pay.com', user_type='instructor')
        cls.learner = make_user('ord_learner@pay.com')
        cls.other_learner = make_user('ord_other@pay.com')
        cls.course = make_course(cls.instructor, slug='orders-course', price='30.00')

        cls.paid_order = Order.objects.create(
            user=cls.learner, course=cls.course, amount=Decimal('30.00'),
            tran_id='CCORDERS0000000000000001', status=Order.Status.PAID,
        )
        cls.failed_order = Order.objects.create(
            user=cls.learner, course=cls.course, amount=Decimal('30.00'),
            tran_id='CCORDERS0000000000000002', status=Order.Status.FAILED,
        )
        cls.foreign_order = Order.objects.create(
            user=cls.other_learner, course=cls.course, amount=Decimal('30.00'),
            tran_id='CCORDERS0000000000000003', status=Order.Status.PAID,
        )

    def auth(self, user=None):
        self.client.force_authenticate(user=user or self.learner)

    def test_list_requires_authentication(self):
        self.assertEqual(self.client.get(LIST_URL).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_returns_own_orders_only(self):
        self.auth()
        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['data']['results']
        tran_ids = {row['tran_id'] for row in results}
        self.assertEqual(tran_ids, {self.paid_order.tran_id, self.failed_order.tran_id})

    def test_list_status_filter(self):
        self.auth()
        response = self.client.get(LIST_URL, {'status': 'paid'})
        results = response.data['data']['results']
        self.assertEqual([row['tran_id'] for row in results], [self.paid_order.tran_id])

    def test_list_invalid_status_returns_400(self):
        self.auth()
        response = self.client.get(LIST_URL, {'status': 'garbage'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_detail_own_order(self):
        self.auth()
        response = self.client.get(
            reverse('payments:order-detail', args=[self.paid_order.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['tran_id'], self.paid_order.tran_id)
        self.assertEqual(data['course_slug'], self.course.slug)
        # Raw gateway internals must never be serialized.
        self.assertNotIn('gateway_payload', data)
        self.assertNotIn('val_id', data)

    def test_detail_cross_user_returns_404(self):
        self.auth()
        response = self.client.get(
            reverse('payments:order-detail', args=[self.foreign_order.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

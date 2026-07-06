import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEmailVerified, IsLearnerUser
from courses.all_models.course_models import NidusCourse
from payments.services import PaymentError, create_checkout
from webinars.models import Webinar

logger = logging.getLogger(__name__)


class PaymentCheckoutView(APIView):
    """POST /api/v1/payments/checkout/ → open an SSLCommerz hosted-checkout session.

    Body: exactly one of {"course_slug": "..."} or {"webinar_slug": "..."}.
    Returns the GatewayPageURL the frontend redirects the browser to.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request):
        course_slug = request.data.get('course_slug')
        webinar_slug = request.data.get('webinar_slug')
        if bool(course_slug) == bool(webinar_slug):
            return Response(
                {
                    'success': False,
                    'message': 'Validation failed.',
                    'errors': {
                        'course_slug': ['Provide exactly one of course_slug or webinar_slug.'],
                        'webinar_slug': ['Provide exactly one of course_slug or webinar_slug.'],
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        course = webinar = None
        if course_slug:
            course = get_object_or_404(NidusCourse, slug=course_slug, is_published=True)
        else:
            webinar = get_object_or_404(Webinar, slug=webinar_slug, is_published=True)

        try:
            order, gateway_url = create_checkout(request.user, course=course, webinar=webinar)
        except PaymentError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception(
                'Checkout failed unexpectedly: user=%s slug=%s',
                request.user.pk, course_slug or webinar_slug,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Checkout session created.',
                'data': {
                    'gateway_url': gateway_url,
                    'order_id': order.pk,
                    'tran_id': order.tran_id,
                    'item_type': order.item_type,
                    'amount': str(order.amount),
                    'currency': order.currency,
                },
            },
            status=status.HTTP_201_CREATED,
        )

from django.urls import path

from payments.views import (
    OrderDetailView,
    OrderListView,
    PaymentCancelView,
    PaymentCheckoutView,
    PaymentFailView,
    PaymentIPNView,
    PaymentSuccessView,
)

app_name = 'payments'

urlpatterns = [
    path('checkout/', PaymentCheckoutView.as_view(), name='checkout'),
    # Gateway callbacks (unauthenticated; authenticity via the validation API).
    path('ipn/', PaymentIPNView.as_view(), name='ipn'),
    path('success/', PaymentSuccessView.as_view(), name='success'),
    path('fail/', PaymentFailView.as_view(), name='fail'),
    path('cancel/', PaymentCancelView.as_view(), name='cancel'),
    # Learner order history.
    path('orders/', OrderListView.as_view(), name='order-list'),
    path('orders/<int:pk>/', OrderDetailView.as_view(), name='order-detail'),
]

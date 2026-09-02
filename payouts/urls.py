from django.urls import path

from payouts.views import (
    AdminPayoutAccountListView,
    AdminPayoutAccountVerifyView,
    AdminPayoutDetailView,
    AdminPayoutGenerateView,
    AdminPayoutListView,
    AdminPayoutMarkPaidView,
    AdminPayoutReviewView,
    MyPayoutAccountView,
    MyPayoutListView,
)

app_name = 'payouts'

urlpatterns = [
    path('payout-account/me/', MyPayoutAccountView.as_view(), name='my-payout-account'),
    path('my-payouts/', MyPayoutListView.as_view(), name='my-payouts'),

    path('admin/payout-accounts/', AdminPayoutAccountListView.as_view(), name='admin-payout-account-list'),
    path('admin/payout-accounts/<int:pk>/verify/', AdminPayoutAccountVerifyView.as_view(), name='admin-payout-account-verify'),

    path('admin/payouts/generate/', AdminPayoutGenerateView.as_view(), name='admin-payout-generate'),
    path('admin/payouts/', AdminPayoutListView.as_view(), name='admin-payout-list'),
    path('admin/payouts/<int:pk>/', AdminPayoutDetailView.as_view(), name='admin-payout-detail'),
    path('admin/payouts/<int:pk>/review/', AdminPayoutReviewView.as_view(), name='admin-payout-review'),
    path('admin/payouts/<int:pk>/mark-paid/', AdminPayoutMarkPaidView.as_view(), name='admin-payout-mark-paid'),
]

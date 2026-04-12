from django.urls import path

from id_verification.views import (
    AdminVerificationDetailView,
    AdminVerificationListView,
    AdminVerificationReviewView,
    VerificationCreateView,
    VerificationDetailView,
    VerificationListView,
    VerificationSubmitView,
    VerificationUpdateView,
)

app_name = 'id_verification'

urlpatterns = [
    # ── Instructor endpoints ──
    path('create/', VerificationCreateView.as_view(), name='verification-create'),
    path('<int:pk>/update/', VerificationUpdateView.as_view(), name='verification-update'),
    path('<int:pk>/submit/', VerificationSubmitView.as_view(), name='verification-submit'),
    path('my/', VerificationListView.as_view(), name='verification-my-list'),
    path('my/<int:pk>/', VerificationDetailView.as_view(), name='verification-my-detail'),

    # ── Admin endpoints ──
    path('admin/list/', AdminVerificationListView.as_view(), name='admin-verification-list'),
    path('admin/<int:pk>/', AdminVerificationDetailView.as_view(), name='admin-verification-detail'),
    path('admin/<int:pk>/review/', AdminVerificationReviewView.as_view(), name='admin-verification-review'),
]
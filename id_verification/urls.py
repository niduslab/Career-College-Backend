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
    InstitutionVerificationCreateView,
    InstitutionVerificationDetailView,
    InstitutionVerificationListView,
    InstitutionVerificationSubmitView,
    InstitutionVerificationUpdateView,
    AdminInstitutionVerificationDetailView,
    AdminInstitutionVerificationListView,
    AdminInstitutionVerificationReviewView,
)

app_name = 'id_verification'

urlpatterns = [
    # ── Instructor endpoints ──
    path('create/', VerificationCreateView.as_view(), name='verification-create'),
    path('<int:pk>/update/', VerificationUpdateView.as_view(), name='verification-update'),
    path('<int:pk>/submit/', VerificationSubmitView.as_view(), name='verification-submit'),
    path('my/', VerificationListView.as_view(), name='verification-my-list'),
    path('my/<int:pk>/', VerificationDetailView.as_view(), name='verification-my-detail'),

    # ── Admin endpoints (instructor verification) ──
    path('admin/list/', AdminVerificationListView.as_view(), name='admin-verification-list'),
    path('admin/<int:pk>/', AdminVerificationDetailView.as_view(), name='admin-verification-detail'),
    path('admin/<int:pk>/review/', AdminVerificationReviewView.as_view(), name='admin-verification-review'),

    # ── Institution endpoints ──
    path('institution/create/', InstitutionVerificationCreateView.as_view(), name='institution-verification-create'),
    path('institution/<int:pk>/update/', InstitutionVerificationUpdateView.as_view(), name='institution-verification-update'),
    path('institution/<int:pk>/submit/', InstitutionVerificationSubmitView.as_view(), name='institution-verification-submit'),
    path('institution/my/', InstitutionVerificationListView.as_view(), name='institution-verification-my-list'),
    path('institution/my/<int:pk>/', InstitutionVerificationDetailView.as_view(), name='institution-verification-my-detail'),

    # ── Admin endpoints (institution verification) ──
    path('admin/institution/list/', AdminInstitutionVerificationListView.as_view(), name='admin-institution-verification-list'),
    path('admin/institution/<int:pk>/', AdminInstitutionVerificationDetailView.as_view(), name='admin-institution-verification-detail'),
    path('admin/institution/<int:pk>/review/', AdminInstitutionVerificationReviewView.as_view(), name='admin-institution-verification-review'),
]

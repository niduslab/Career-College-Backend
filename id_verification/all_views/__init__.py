from id_verification.all_views.instructor_views import (
    VerificationCreateView,
    VerificationUpdateView,
    VerificationSubmitView,
    VerificationListView,
    VerificationDetailView,
)
from id_verification.all_views.admin_views import (
    AdminVerificationListView,
    AdminVerificationDetailView,
    AdminVerificationReviewView,
)

__all__ = [
    'VerificationCreateView',
    'VerificationUpdateView',
    'VerificationSubmitView',
    'VerificationListView',
    'VerificationDetailView',
    'AdminVerificationListView',
    'AdminVerificationDetailView',
    'AdminVerificationReviewView',
]

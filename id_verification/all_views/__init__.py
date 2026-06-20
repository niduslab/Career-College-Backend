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
    AdminInstitutionVerificationListView,
    AdminInstitutionVerificationDetailView,
    AdminInstitutionVerificationReviewView,
)
from id_verification.all_views.institution_views import (
    InstitutionVerificationCreateView,
    InstitutionVerificationUpdateView,
    InstitutionVerificationSubmitView,
    InstitutionVerificationListView,
    InstitutionVerificationDetailView,
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
    'InstitutionVerificationCreateView',
    'InstitutionVerificationUpdateView',
    'InstitutionVerificationSubmitView',
    'InstitutionVerificationListView',
    'InstitutionVerificationDetailView',
    'AdminInstitutionVerificationListView',
    'AdminInstitutionVerificationDetailView',
    'AdminInstitutionVerificationReviewView',
]

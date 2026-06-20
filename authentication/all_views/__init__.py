from authentication.all_views.auth_views import (
    LogoutView,
    TokenRefreshView,
    UserLoginView,
    UserRegistrationView,
)
from authentication.all_views.google_views import (
    GoogleAuthCallbackView,
    GoogleAuthRedirectView,
    GoogleExchangeTokenView,
)
from authentication.all_views.linkedin_views import (
    LinkedInAuthRedirectView,
    LinkedInAuthCallbackView,
    LinkedInExchangeTokenView,
)
from authentication.all_views.otp_views import VerifyOTPView, ResendOTPView
from authentication.all_views.password_views import ForgotPasswordView, ResetPasswordView, ChangePasswordView
from authentication.all_views.profile_views import (
    MyProfileView,
    EducationListCreateView,
    EducationDetailView,
    WorkExperienceListCreateView,
    WorkExperienceDetailView,
    PublicProfileDetailView,
    PublicLearnerListView,
    PublicInstructorListView,
    PublicInstitutionListView,
)
from authentication.all_views.partner_views import (
    InstitutionExpertListCreateView,
    InstitutionExpertDetailView,
)

__all__ = [
    'GoogleAuthRedirectView',
    'GoogleAuthCallbackView',
    'GoogleExchangeTokenView',
    'LinkedInAuthRedirectView',
    'LinkedInAuthCallbackView',
    'LinkedInExchangeTokenView',
    'UserRegistrationView',
    'UserLoginView',
    'LogoutView',
    'VerifyOTPView',
    'ResendOTPView',
    'ForgotPasswordView',
    'ResetPasswordView',
    'ChangePasswordView',
    'MyProfileView',
    'EducationListCreateView',
    'EducationDetailView',
    'WorkExperienceListCreateView',
    'WorkExperienceDetailView',
    'PublicProfileDetailView',
    'PublicLearnerListView',
    'PublicInstructorListView',
    'PublicInstitutionListView',
    'InstitutionExpertListCreateView',
    'InstitutionExpertDetailView',
    'TokenRefreshView',
]

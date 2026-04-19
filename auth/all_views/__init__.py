from auth.all_views.auth_views import (
    LogoutView,
    TokenRefreshView,
    UserLoginView,
    UserRegistrationView,
)
from auth.all_views.google_views import (
    GoogleAuthCallbackView,
    GoogleAuthRedirectView,
    GoogleExchangeTokenView,
)
from auth.all_views.linkedin_views import (
    LinkedInAuthRedirectView,
    LinkedInAuthCallbackView,
    LinkedInExchangeTokenView,
)
from auth.all_views.otp_views import VerifyOTPView, ResendOTPView
from auth.all_views.password_views import ForgotPasswordView, ResetPasswordView, ChangePasswordView
from auth.all_views.profile_views import (
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
    'TokenRefreshView',
]

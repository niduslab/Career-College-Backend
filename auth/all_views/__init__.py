from auth.all_views.auth_views import UserRegistrationView, UserLoginView, LogoutView
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
]

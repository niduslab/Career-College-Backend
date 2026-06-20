from django.urls import path
from authentication.views import (
    GoogleAuthCallbackView,
    GoogleAuthRedirectView,
    GoogleExchangeTokenView,
    LinkedInAuthRedirectView,
    LinkedInAuthCallbackView,
    LinkedInExchangeTokenView,
    UserRegistrationView,
    UserLoginView,
    LogoutView,
    TokenRefreshView,
    VerifyOTPView,
    ResendOTPView,
    ForgotPasswordView,
    ResetPasswordView,
    ChangePasswordView,
    MyProfileView,
    EducationListCreateView,
    EducationDetailView,
    WorkExperienceListCreateView,
    WorkExperienceDetailView,
    PublicProfileDetailView,
    PublicLearnerListView,
    PublicInstructorListView,
    PublicInstitutionListView,
    InstitutionExpertListCreateView,
    InstitutionExpertDetailView,
)

app_name = 'authentication'

urlpatterns = [
    # Registration & Login
    path('register/', UserRegistrationView.as_view(), name='register'),
    path('login/', UserLoginView.as_view(), name='login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('logout/', LogoutView.as_view(), name='logout'),

    # Google OAuth (authorization-code flow)
    path('google/', GoogleAuthRedirectView.as_view(), name='google-redirect'),
    path('google/callback/', GoogleAuthCallbackView.as_view(), name='google-callback'),
    path('google/exchange-token/', GoogleExchangeTokenView.as_view(), name='google-exchange-token'),

    # LinkedIn OAuth (authorization-code flow)
    path('linkedin/', LinkedInAuthRedirectView.as_view(), name='linkedin-redirect'),
    path('linkedin/callback/', LinkedInAuthCallbackView.as_view(), name='linkedin-callback'),
    path('linkedin/exchange-token/', LinkedInExchangeTokenView.as_view(), name='linkedin-exchange-token'),

    # OTP
    path('otp/verify/', VerifyOTPView.as_view(), name='otp-verify'),
    path('otp/resend/', ResendOTPView.as_view(), name='otp-resend'),

    # Password
    path('password/forgot/', ForgotPasswordView.as_view(), name='password-forgot'),
    path('password/reset/', ResetPasswordView.as_view(), name='password-reset'),
    path('password/change/', ChangePasswordView.as_view(), name='password-change'),

    # ── Private profile management (authenticated) ───────────
    path('profile/me/', MyProfileView.as_view(), name='my-profile'),
    path('profile/me/education/', EducationListCreateView.as_view(), name='my-education-list'),
    path('profile/me/education/<int:pk>/', EducationDetailView.as_view(), name='my-education-detail'),
    path('profile/me/work-experience/', WorkExperienceListCreateView.as_view(), name='my-work-experience-list'),
    path('profile/me/work-experience/<int:pk>/', WorkExperienceDetailView.as_view(), name='my-work-experience-detail'),

    # ── Partner institution: expert management ───────────────
    path('partner/experts/', InstitutionExpertListCreateView.as_view(), name='institution-expert-list-create'),
    path('partner/experts/<int:expert_id>/', InstitutionExpertDetailView.as_view(), name='institution-expert-detail'),

    # ── Public profile browsing ──────────────────────────────
    path('profiles/learners/', PublicLearnerListView.as_view(), name='public-learner-list'),
    path('profiles/instructors/', PublicInstructorListView.as_view(), name='public-instructor-list'),
    path('profiles/institutions/', PublicInstitutionListView.as_view(), name='public-institution-list'),
    path('profiles/<slug:slug>/', PublicProfileDetailView.as_view(), name='public-profile-detail'),
]

from django.urls import path

from admin_console.views import (
    AdminLoginView,
    AdminLogoutView,
    AdminSessionListView,
    AdminSessionRevokeOthersView,
    AdminSessionRevokeView,
    AdminSessionView,
    CsrfTokenView,
)

app_name = 'admin_console'

urlpatterns = [
    # Session-based admin authentication
    path('auth/csrf/', CsrfTokenView.as_view(), name='auth-csrf'),
    path('auth/login/', AdminLoginView.as_view(), name='auth-login'),
    path('auth/logout/', AdminLogoutView.as_view(), name='auth-logout'),
    path('auth/session/', AdminSessionView.as_view(), name='auth-session'),

    # Device/session management (list + remote logout)
    path('sessions/', AdminSessionListView.as_view(), name='session-list'),
    path('sessions/revoke-others/', AdminSessionRevokeOthersView.as_view(), name='session-revoke-others'),
    path('sessions/<int:pk>/', AdminSessionRevokeView.as_view(), name='session-revoke'),
]

from django.urls import path

from admin_console.views import (
    AdminAuditLogListView,
    AdminSessionListView,
    AdminSessionRevokeOthersView,
    AdminSessionRevokeView,
    AdminSessionView,
    AdminUserDetailView,
    AdminUserListView,
    AdminUserReactivateView,
    AdminUserRoleView,
    AdminUserSuspendView,
    PlatformSettingsView,
)

app_name = 'admin_console'

urlpatterns = [
    path('auth/session/', AdminSessionView.as_view(), name='auth-session'),

    # Device/session management (list + remote logout)
    path('sessions/', AdminSessionListView.as_view(), name='session-list'),
    path('sessions/revoke-others/', AdminSessionRevokeOthersView.as_view(), name='session-revoke-others'),
    path('sessions/<int:pk>/', AdminSessionRevokeView.as_view(), name='session-revoke'),

    # User management
    path('users/', AdminUserListView.as_view(), name='user-list'),
    path('users/<int:pk>/', AdminUserDetailView.as_view(), name='user-detail'),
    path('users/<int:pk>/suspend/', AdminUserSuspendView.as_view(), name='user-suspend'),
    path('users/<int:pk>/reactivate/', AdminUserReactivateView.as_view(), name='user-reactivate'),
    path('users/<int:pk>/role/', AdminUserRoleView.as_view(), name='user-role'),

    # Platform settings (branding + default authorized signatory)
    path('platform-settings/', PlatformSettingsView.as_view(), name='platform-settings'),

    # Audit log
    path('audit/', AdminAuditLogListView.as_view(), name='audit-list'),
]

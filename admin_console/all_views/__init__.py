from admin_console.all_views.auth_views import AdminSessionView
from admin_console.all_views.platform_settings_views import PlatformSettingsView
from admin_console.all_views.session_views import (
    AdminSessionListView,
    AdminSessionRevokeOthersView,
    AdminSessionRevokeView,
)
from admin_console.all_views.user_views import (
    AdminAuditLogListView,
    AdminUserDetailView,
    AdminUserListView,
    AdminUserReactivateView,
    AdminUserRoleView,
    AdminUserSuspendView,
)

__all__ = [
    'AdminSessionView',
    'AdminSessionListView',
    'AdminSessionRevokeView',
    'AdminSessionRevokeOthersView',
    'AdminUserListView',
    'AdminUserDetailView',
    'AdminUserSuspendView',
    'AdminUserReactivateView',
    'AdminUserRoleView',
    'AdminAuditLogListView',
    'PlatformSettingsView',
]

from django.urls import path

from .views import (
    MarkReadView,
    NotificationListView,
    NotificationPreferenceView,
    UnreadCountView,
)

app_name = 'notifications'

urlpatterns = [
    path('', NotificationListView.as_view(), name='list'),
    path('mark-read/', MarkReadView.as_view(), name='mark-read'),
    path('unread-count/', UnreadCountView.as_view(), name='unread-count'),
    path('preferences/', NotificationPreferenceView.as_view(), name='preferences'),
]

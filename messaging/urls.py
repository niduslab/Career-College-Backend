from django.urls import path

from messaging.views import (
    ConversationCreateView,
    ConversationDetailView,
    ConversationListView,
    MarkConversationReadView,
    UnreadConversationCountView,
)

app_name = 'messaging'

urlpatterns = [
    # List all conversations for the calling user (learner or instructor).
    path('conversations/', ConversationListView.as_view(), name='conversation-list'),

    # Count of conversations with unread messages (nav/inbox badge).
    path('conversations/unread-count/', UnreadConversationCountView.as_view(), name='unread-conversation-count'),

    # Start a new conversation (learner only — body required).
    path('conversations/create/', ConversationCreateView.as_view(), name='conversation-create'),

    # Thread detail: metadata + paginated messages.
    path('conversations/<int:conversation_id>/', ConversationDetailView.as_view(), name='conversation-detail'),

    # Follow-up messages are sent over the WebSocket `messaging` stream, not REST.

    # Mark all messages in a thread as read (updates the caller's read cursor).
    path('conversations/<int:conversation_id>/read/', MarkConversationReadView.as_view(), name='mark-read'),
]

from django.urls import path

from messaging.views import (
    ConversationCreateView,
    ConversationDetailView,
    ConversationListView,
    MarkConversationReadView,
    SendMessageView,
)

app_name = 'messaging'

urlpatterns = [
    # List all conversations for the calling user (learner or instructor).
    path('conversations/', ConversationListView.as_view(), name='conversation-list'),

    # Start a new conversation (learner only — body required).
    path('conversations/create/', ConversationCreateView.as_view(), name='conversation-create'),

    # Thread detail: metadata + paginated messages.
    path('conversations/<int:conversation_id>/', ConversationDetailView.as_view(), name='conversation-detail'),

    # Send a message in an existing thread.
    path('conversations/<int:conversation_id>/messages/', SendMessageView.as_view(), name='send-message'),

    # Mark all messages in a thread as read (updates *_last_read_at).
    path('conversations/<int:conversation_id>/read/', MarkConversationReadView.as_view(), name='mark-read'),
]

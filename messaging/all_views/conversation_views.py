"""
Messaging REST endpoints.

Routes (all under /api/v1/messaging/):
    GET  conversations/                        → ConversationListView
    POST conversations/create/                 → ConversationCreateView
    GET  conversations/<int:conversation_id>/  → ConversationDetailView
    POST conversations/<int:conversation_id>/read/ → MarkConversationReadView

Follow-up messages are sent over the WebSocket `messaging` stream only (there is
no REST send endpoint); the conversation opener is persisted by the create view.

Access-denied policy (project convention):
    Numeric conversation/message IDs → 404 on no-access.
"""

import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import (
    IsEmailVerified,
    IsInstructorUser,
    IsLearnerUser,
    IsPartnerInstitutionUser,
)
from messaging.models import Conversation
from messaging.serializers import (
    ConversationCreateSerializer,
    ConversationSerializer,
    MessageSerializer,
)
from messaging.services import (
    MessagingError,
    get_conversation_for_participant,
    get_messages,
    get_unread_conversation_count,
    list_conversations,
    mark_read,
    start_conversation,
)

logger = logging.getLogger(__name__)

# Learners, instructors, and partner institutions all use messaging.
_MESSAGING_USERS = IsLearnerUser | IsInstructorUser | IsPartnerInstitutionUser


class ConversationListView(APIView):
    """
    GET — Paginated list of the caller's conversations, newest-updated first.
    Accessible to both learners and instructors.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, _MESSAGING_USERS]

    def get(self, request):
        qs = list_conversations(request.user)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ConversationSerializer(page, many=True, context={'request': request})
        paginated = paginator.get_paginated_response(serializer.data)
        paginated.data = {'success': True, 'data': paginated.data}
        return paginated


class UnreadConversationCountView(APIView):
    """
    GET — Number of conversations with at least one unread message for the caller.

    Returns a single integer (not the total unread message count). Suited to a
    nav/inbox badge. Accessible to both learners and instructors.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, _MESSAGING_USERS]

    def get(self, request):
        try:
            count = get_unread_conversation_count(request.user)
        except Exception:
            logger.exception(
                'UnreadConversationCountView: unexpected error user=%s',
                request.user.pk,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'data': {'unread_conversations': count}},
            status=status.HTTP_200_OK,
        )


class _ResolutionError(Exception):
    """Internal: signals a 404/403 while resolving create-payload references."""

    def __init__(self, message, http_status):
        self.message = message
        self.http_status = http_status
        super().__init__(message)


class ConversationCreateView(APIView):
    """
    POST — Initiate a conversation. `conversation_type` (default
    learner_instructor) selects who may open it and which target/course fields
    are required. Learner↔instructor is learner-initiated; co_instructor is
    instructor-initiated; institution_expert is institution-initiated.

    Atomically creates the Conversation + first Message. If the thread already
    exists, returns it with HTTP 200 so the client navigates to the open thread.
    The send-gate is enforced in the service (returns 403 on violation).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, _MESSAGING_USERS]

    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vd = serializer.validated_data
        try:
            course, target = self._resolve_course_and_target(request.user, vd)
        except _ResolutionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )

        try:
            conversation, created = start_conversation(
                conversation_type=vd['conversation_type'],
                initiator=request.user,
                target=target,
                course=course,
                opener_body=vd['body'],
            )
        except MessagingError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception(
                'ConversationCreateView: unexpected error user=%s type=%s',
                request.user.pk, vd['conversation_type'],
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        message_text = 'Conversation started.' if created else 'Conversation already exists.'
        return Response(
            {
                'success': True,
                'message': message_text,
                'data': ConversationSerializer(conversation, context={'request': request}).data,
            },
            status=http_status,
        )

    def _resolve_course_and_target(self, user, vd):
        """Resolve (course_or_none, target_user) per conversation_type. Numeric IDs → 404."""
        from authentication.models import User
        from courses.models import NidusCourse

        ctype = vd['conversation_type']
        CType = Conversation.ConversationType

        course = None
        course_id = vd.get('course_id')
        if course_id is not None:
            try:
                course = NidusCourse.objects.get(pk=course_id)
            except NidusCourse.DoesNotExist:
                raise _ResolutionError('Course not found.', status.HTTP_404_NOT_FOUND)

        if ctype == CType.LEARNER_INSTRUCTOR:
            target_id, label = vd['instructor_id'], 'Instructor'
        elif ctype == CType.CO_INSTRUCTOR:
            target_id, label = vd['peer_instructor_id'], 'Instructor'
        else:  # institution_expert — institution-initiated
            if user.user_type != 'partner_institution':
                raise _ResolutionError(
                    'Only a partner institution can open this conversation.',
                    status.HTTP_403_FORBIDDEN,
                )
            target_id, label = vd['expert_user_id'], 'Expert'

        try:
            target = User.objects.get(pk=target_id, user_type='instructor')
        except User.DoesNotExist:
            raise _ResolutionError(f'{label} not found.', status.HTTP_404_NOT_FOUND)

        return course, target


class ConversationDetailView(APIView):
    """
    GET — Conversation metadata + paginated messages (oldest-first).

    Numeric ID → 404 on no-access.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, _MESSAGING_USERS]

    def get(self, request, conversation_id: int):
        try:
            conversation = get_conversation_for_participant(request.user, conversation_id)
        except Conversation.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Conversation not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        messages_qs = get_messages(conversation)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(messages_qs, request)
        msg_serializer = MessageSerializer(page, many=True, context={'request': request})
        paginated = paginator.get_paginated_response(msg_serializer.data)

        # Embed conversation metadata alongside the paginated message list.
        paginated.data = {
            'success': True,
            'data': {
                'conversation': ConversationSerializer(
                    conversation, context={'request': request}
                ).data,
                'messages': paginated.data,
            },
        }
        return paginated


class MarkConversationReadView(APIView):
    """
    POST — Mark all messages in a conversation as read for the caller.

    Updates the caller's *_last_read_at timestamp in one query. No body required.

    Numeric ID → 404 on no-access.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, _MESSAGING_USERS]

    def post(self, request, conversation_id: int):
        try:
            mark_read(request.user, conversation_id)
        except Conversation.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Conversation not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception:
            logger.exception(
                'MarkConversationReadView: unexpected error user=%s conversation=%s',
                request.user.pk, conversation_id,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Marked as read.'},
            status=status.HTTP_200_OK,
        )

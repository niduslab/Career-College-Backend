"""
Messaging REST endpoints.

Routes (all under /api/v1/messaging/):
    GET  conversations/                     → ConversationListView
    POST conversations/                     → ConversationCreateView   (learner only)
    GET  conversations/<int:conversation_id>/          → ConversationDetailView
    POST conversations/<int:conversation_id>/messages/ → SendMessageView
    POST conversations/<int:conversation_id>/read/     → MarkConversationReadView

Access-denied policy (project convention):
    Numeric conversation/message IDs → 404 on no-access.
"""

import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsInstructorUser, IsLearnerUser
from messaging.models import Conversation
from messaging.serializers import (
    ConversationCreateSerializer,
    ConversationSerializer,
    MessageSerializer,
    SendMessageSerializer,
)
from messaging.services import (
    MessagingError,
    get_conversation_for_participant,
    get_messages,
    get_or_create_conversation,
    list_conversations,
    mark_read,
    send_message,
)

logger = logging.getLogger(__name__)

# Both user types share most messaging endpoints.
_MESSAGING_USERS = IsLearnerUser | IsInstructorUser


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


class ConversationCreateView(APIView):
    """
    POST — Initiate a new conversation. Learner-only.

    Atomically creates the Conversation row + first Message. If the
    (learner, instructor, course) triad already exists, returns the existing
    conversation with HTTP 200 so the client can navigate to the open thread.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vd = serializer.validated_data

        # Resolve course and instructor up-front; use 404 not 403 (numeric IDs).
        try:
            from courses.models import NidusCourse
            course = NidusCourse.objects.prefetch_related('instructors').get(pk=vd['course_id'])
        except NidusCourse.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            from authentication.models import User
            instructor = User.objects.get(pk=vd['instructor_id'], user_type='instructor')
        except User.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Instructor not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            conversation, created = get_or_create_conversation(
                learner=request.user,
                instructor=instructor,
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
                'ConversationCreateView: unexpected error user=%s course=%s instructor=%s',
                request.user.pk, vd['course_id'], vd['instructor_id'],
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


class SendMessageView(APIView):
    """
    POST — Append a message to an existing conversation.

    Both learners and instructors can send. Send-gate (enrollment / instructor
    membership) is enforced inside the service.

    Numeric ID → 404 on no-access.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, _MESSAGING_USERS]

    def post(self, request, conversation_id: int):
        serializer = SendMessageSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            message = send_message(
                user=request.user,
                conversation_id=conversation_id,
                body=serializer.validated_data['body'],
            )
        except Conversation.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Conversation not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except MessagingError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception(
                'SendMessageView: unexpected error user=%s conversation=%s',
                request.user.pk, conversation_id,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Message sent.',
                'data': MessageSerializer(message, context={'request': request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


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

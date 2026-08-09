"""
Learner note endpoints.

Routes (all under /api/v1/courses/):
    GET/POST              notes/        -> LearnerNoteListCreateView
    GET/PATCH/DELETE      notes/<pk>/   -> LearnerNoteDetailView
"""

import logging

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsLearnerUser
from courses.models import LearnerNote
from courses.serializers import LearnerNoteReadSerializer, LearnerNoteWriteSerializer
from courses.services import (
    NoteError,
    create_note,
    delete_note,
    get_learner_notes,
    get_note,
    update_note,
)

logger = logging.getLogger(__name__)


def _validation_error_response(exc):
    """Django ValidationError → 400 with a per-field errors dict."""
    errors = exc.message_dict if hasattr(exc, 'message_dict') else {'detail': exc.messages}
    return Response(
        {'success': False, 'message': 'Validation failed.', 'errors': errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


class LearnerNoteListCreateView(APIView):
    """
    GET  /api/v1/courses/notes/  — paginated, filtered list of the caller's notes.
    POST /api/v1/courses/notes/  — create a note.

    Filters: ?course=<slug> &lecture_id=<int> &tag=<csv> &is_pinned=<bool>
             &search=<str> &ordering=<field>

    Multiple ?tag values AND together — a note must carry all of them. Pinned
    notes always sort first, whatever `ordering` is supplied. Invalid params
    return one 400 carrying every offending field.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        try:
            queryset = get_learner_notes(request.user, request.query_params)
        except ValidationError as e:
            return _validation_error_response(e)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = LearnerNoteReadSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response

    def post(self, request):
        serializer = LearnerNoteWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            note = create_note(request.user, serializer.validated_data)
        except NoteError as e:
            return Response({'success': False, 'message': e.message}, status=e.http_status)
        except ValidationError as e:
            return _validation_error_response(e)
        except Exception:
            logger.exception('Note creation failed for user=%s', request.user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Note created.',
                'data': LearnerNoteReadSerializer(note).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LearnerNoteDetailView(APIView):
    """
    GET / PATCH / DELETE /api/v1/courses/notes/{pk}/

    Numeric identifier → 404 (never 403) when the note is missing OR owned by
    another learner, per the project's access-denied policy for
    non-enumerable numeric IDs.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request, pk):
        try:
            note = get_note(request.user, pk)
        except LearnerNote.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Note not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': LearnerNoteReadSerializer(note).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, pk):
        serializer = LearnerNoteWriteSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            note = update_note(request.user, pk, serializer.validated_data)
        except LearnerNote.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Note not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except NoteError as e:
            return Response({'success': False, 'message': e.message}, status=e.http_status)
        except ValidationError as e:
            return _validation_error_response(e)
        except Exception:
            logger.exception('Note update failed for user=%s note=%s', request.user.pk, pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Note updated.',
                'data': LearnerNoteReadSerializer(note).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, pk):
        try:
            delete_note(request.user, pk)
        except LearnerNote.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Note not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'message': 'Note deleted.'},
            status=status.HTTP_200_OK,
        )

"""
Learning path endpoints.

Routes (all under /api/v1/courses/):
    GET             learning-paths/                       -> LearningPathListView
    GET             learning-paths/<slug>/                -> LearningPathDetailView
    GET             learning-paths/<slug>/progress/        -> LearningPathProgressView
    POST/DELETE     learning-paths/<slug>/enroll/          -> LearningPathEnrollView
    GET             my-learning-paths/                     -> MyLearningPathsView

    GET/POST        learning-paths/manage/                 -> LearningPathManageListView
    GET/PATCH/DELETE learning-paths/manage/<int:pk>/        -> LearningPathManageDetailView
    POST            learning-paths/manage/<int:pk>/milestones/                  -> LearningPathMilestoneCreateView
    DELETE          learning-paths/manage/<int:pk>/milestones/<int:milestone_id>/ -> LearningPathMilestoneDetailView
    POST            learning-paths/manage/<int:pk>/milestones/reorder/          -> LearningPathMilestoneReorderView

See docs/architecture/28-learning-paths.md for the full design and the
derived-progress rule (no separate progress ledger — always computed from
the learner's real course Enrollment rows).
"""

import logging

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsCourseCreator, IsEmailVerified, IsLearnerUser
from courses.models import NidusCourse
from courses.all_models.learning_path_models import LearningPath
from courses.serializers import (
    LearningPathDetailSerializer,
    LearningPathEnrollmentSerializer,
    LearningPathListSerializer,
    LearningPathManageSerializer,
    LearningPathProgressSerializer,
    MilestoneCreateSerializer,
    MilestoneReorderSerializer,
)
from courses.services import (
    LearningPathError,
    add_milestone,
    build_milestone_progress,
    enroll_in_path,
    get_my_paths,
    get_owned_path_or_404,
    get_owned_paths,
    get_path_progress_percent,
    get_published_path_by_slug,
    get_published_paths,
    is_enrolled_in_path,
    leave_path,
    remove_milestone,
    reorder_milestones,
)

logger = logging.getLogger(__name__)


def _error_response(exc: LearningPathError) -> Response:
    return Response({'success': False, 'message': exc.message}, status=exc.http_status)


# ---------------------------------------------------------------------------
# Public browse
# ---------------------------------------------------------------------------

class LearningPathListView(APIView):
    """GET /api/v1/courses/learning-paths/ — published paths, public."""

    permission_classes = []

    def get(self, request):
        queryset = get_published_paths()
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = LearningPathListSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class LearningPathDetailView(APIView):
    """GET /api/v1/courses/learning-paths/<slug>/ — public detail, no progress."""

    permission_classes = []

    def get(self, request, slug):
        try:
            path = get_published_path_by_slug(slug)
        except LearningPath.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Learning path not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = LearningPathDetailSerializer(path)
        return Response(
            {'success': True, 'message': 'Learning path retrieved.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Learner-facing
# ---------------------------------------------------------------------------

class LearningPathProgressView(APIView):
    """
    GET /api/v1/courses/learning-paths/<slug>/progress/

    Path detail plus the caller's derived per-milestone status and overall
    progress percent. Slug entry point → 403 when the path doesn't exist or
    isn't published (project's access-denied policy for slugs).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request, slug):
        try:
            path = get_published_path_by_slug(slug)
        except LearningPath.DoesNotExist:
            return Response(
                {'success': False, 'message': 'You do not have access to this learning path.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        milestones = list(path.milestones.all())
        progress_rows = build_milestone_progress(request.user, milestones)
        percent = get_path_progress_percent(progress_rows)
        enrolled = is_enrolled_in_path(request.user, path)

        serializer = LearningPathProgressSerializer(
            path,
            context={
                'progress_rows': progress_rows,
                'progress_percent': percent,
                'is_enrolled': enrolled,
            },
        )
        return Response(
            {'success': True, 'message': 'Learning path progress retrieved.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )


class LearningPathEnrollView(APIView):
    """
    POST   /api/v1/courses/learning-paths/{slug}/enroll/  — join (idempotent)
    DELETE /api/v1/courses/learning-paths/{slug}/enroll/  — leave

    Never touches the learner's course Enrollment rows — leaving a path
    doesn't unenroll them from any milestone course already joined.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, slug):
        path = get_object_or_404(LearningPath, slug=slug, status=LearningPath.Status.PUBLISHED)
        try:
            _enrollment, created = enroll_in_path(request.user, path)
        except ValidationError as e:
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception:
            logger.exception(
                'Learning path enroll failed for user=%s path=%s', request.user.pk, path.pk
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                'success': True,
                'message': 'Joined learning path.' if created else 'You are already on this path.',
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, slug):
        path = get_object_or_404(LearningPath, slug=slug, status=LearningPath.Status.PUBLISHED)
        if not leave_path(request.user, path):
            return Response(
                {'success': False, 'message': 'You are not enrolled in this learning path.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'message': 'Left learning path.'},
            status=status.HTTP_200_OK,
        )


class MyLearningPathsView(APIView):
    """
    GET /api/v1/courses/my-learning-paths/

    The caller's enrolled paths, each with derived per-milestone progress.
    Progress is computed in one batched pass across every enrolled path's
    milestone course ids, not per-path.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        enrollments = list(get_my_paths(request.user))

        all_milestones = [m for e in enrollments for m in e.path.milestones.all()]
        course_ids = [m.course_id for m in all_milestones]

        progress_by_path_id = {}
        percent_by_path_id = {}
        if course_ids:
            for e in enrollments:
                milestones = list(e.path.milestones.all())
                rows = build_milestone_progress(request.user, milestones)
                progress_by_path_id[e.path_id] = rows
                percent_by_path_id[e.path_id] = get_path_progress_percent(rows)

        serializer = LearningPathEnrollmentSerializer(
            enrollments,
            many=True,
            context={
                'progress_by_path_id': progress_by_path_id,
                'percent_by_path_id': percent_by_path_id,
            },
        )
        return Response(
            {'success': True, 'message': 'Learning paths retrieved.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Authoring (instructor / admin)
# ---------------------------------------------------------------------------

class LearningPathManageListView(APIView):
    """
    GET  /api/v1/courses/learning-paths/manage/  — own paths
    POST /api/v1/courses/learning-paths/manage/  — create a draft path
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]

    def get(self, request):
        queryset = get_owned_paths(request.user)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = LearningPathManageSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response

    def post(self, request):
        serializer = LearningPathManageSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        path = serializer.save(created_by=request.user, last_edited_by=request.user)
        return Response(
            {
                'success': True,
                'message': 'Learning path created.',
                'data': LearningPathManageSerializer(path).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LearningPathManageDetailView(APIView):
    """
    GET/PATCH/DELETE /api/v1/courses/learning-paths/manage/<int:pk>/

    Numeric ID → 404 on not-own, per the project's access-denied policy.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]

    def _get_path(self, request, pk):
        try:
            return get_owned_path_or_404(request.user, pk)
        except LearningPath.DoesNotExist:
            return None

    def get(self, request, pk):
        path = self._get_path(request, pk)
        if path is None:
            return Response(
                {'success': False, 'message': 'Learning path not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                'success': True,
                'message': 'Learning path retrieved.',
                'data': LearningPathManageSerializer(path).data,
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request, pk):
        path = self._get_path(request, pk)
        if path is None:
            return Response(
                {'success': False, 'message': 'Learning path not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = LearningPathManageSerializer(path, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save(last_edited_by=request.user)
        return Response(
            {
                'success': True,
                'message': 'Learning path updated.',
                'data': LearningPathManageSerializer(path).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, pk):
        path = self._get_path(request, pk)
        if path is None:
            return Response(
                {'success': False, 'message': 'Learning path not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        path.delete()
        return Response(
            {'success': True, 'message': 'Learning path deleted.'},
            status=status.HTTP_200_OK,
        )


class LearningPathMilestoneCreateView(APIView):
    """POST /api/v1/courses/learning-paths/manage/<int:pk>/milestones/"""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]

    def post(self, request, pk):
        try:
            path = get_owned_path_or_404(request.user, pk)
        except LearningPath.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Learning path not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = MilestoneCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        course = get_object_or_404(NidusCourse, pk=serializer.validated_data['course_id'])
        try:
            add_milestone(path, course, serializer.validated_data.get('title', ''))
        except LearningPathError as e:
            return _error_response(e)

        # `path` was fetched with prefetch_related('milestones__course') before
        # the new row existed — that cache is now stale. Re-fetch so the
        # response reflects the milestone we just created, instead of making
        # the frontend do a second round-trip (or worse, silently show stale
        # data until the next unrelated reload).
        path = get_owned_path_or_404(request.user, pk)

        return Response(
            {
                'success': True,
                'message': 'Milestone added.',
                'data': LearningPathManageSerializer(path).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LearningPathMilestoneDetailView(APIView):
    """DELETE /api/v1/courses/learning-paths/manage/<int:pk>/milestones/<int:milestone_id>/"""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]

    def delete(self, request, pk, milestone_id):
        try:
            path = get_owned_path_or_404(request.user, pk)
        except LearningPath.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Learning path not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            remove_milestone(path, milestone_id)
        except LearningPathError as e:
            return _error_response(e)
        return Response(
            {'success': True, 'message': 'Milestone removed.'},
            status=status.HTTP_200_OK,
        )


class LearningPathMilestoneReorderView(APIView):
    """POST /api/v1/courses/learning-paths/manage/<int:pk>/milestones/reorder/"""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]

    def post(self, request, pk):
        try:
            path = get_owned_path_or_404(request.user, pk)
        except LearningPath.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Learning path not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = MilestoneReorderSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reorder_milestones(path, serializer.validated_data['ordered_milestone_ids'])
        except LearningPathError as e:
            return _error_response(e)

        # Same stale-prefetch-cache issue as milestone create — re-fetch so
        # the response reflects the new positions.
        path = get_owned_path_or_404(request.user, pk)

        return Response(
            {
                'success': True,
                'message': 'Milestones reordered.',
                'data': LearningPathManageSerializer(path).data,
            },
            status=status.HTTP_200_OK,
        )

"""
Wishlist endpoints.

Routes (all under /api/v1/courses/):
    GET           wishlist/          -> WishlistListView
    POST/DELETE   <slug>/wishlist/   -> CourseWishlistView
"""

import logging

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsLearnerUser
from courses.models import NidusCourse
from courses.serializers import WishlistItemSerializer
from courses.services import add_to_wishlist, get_learner_wishlist, remove_from_wishlist

logger = logging.getLogger(__name__)


class WishlistListView(APIView):
    """
    GET /api/v1/courses/wishlist/

    Paginated list of the learner's wishlisted courses, most recently saved
    first. Each row nests the standard catalog card, with `is_wishlisted`
    pre-resolved to True so the heart renders filled without a second query.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        queryset = get_learner_wishlist(request.user)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = WishlistItemSerializer(page, many=True, context={
            'request': request,
            'wishlisted_course_ids': {item.course_id for item in page},
        })
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class CourseWishlistView(APIView):
    """
    POST   /api/v1/courses/{slug}/wishlist/  — add (idempotent)
    DELETE /api/v1/courses/{slug}/wishlist/  — remove

    Slug identifier → 404 when the course is missing or unpublished. POST
    returns 201 on first add and 200 when the course was already saved, so a
    double-tapped heart is never an error. DELETE returns 404 when the course
    was not on the wishlist.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, slug):
        course = get_object_or_404(NidusCourse, slug=slug, is_published=True)
        try:
            item, created = add_to_wishlist(request.user, course)
        except ValidationError as e:
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception:
            logger.exception(
                'Wishlist add failed for user=%s course=%s', request.user.pk, course.pk
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = WishlistItemSerializer(item, context={
            'request': request,
            'wishlisted_course_ids': {course.id},
        })
        return Response(
            {
                'success': True,
                'message': 'Course saved to your wishlist.' if created
                           else 'Course is already on your wishlist.',
                'data': serializer.data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, slug):
        course = get_object_or_404(NidusCourse, slug=slug, is_published=True)
        if not remove_from_wishlist(request.user, course):
            return Response(
                {'success': False, 'message': 'Course is not on your wishlist.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'message': 'Course removed from your wishlist.'},
            status=status.HTTP_200_OK,
        )

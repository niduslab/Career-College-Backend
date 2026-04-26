from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from courses.models import CourseSection, Lecture, NidusCourse
from courses.serializers import (
    CourseSectionCreateUpdateSerializer,
    CourseSectionSerializer,
    LectureCreateUpdateSerializer,
    LectureSerializer,
)
from courses.services import get_course_sections, get_section_lectures


class CourseSectionListAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_course(self, request, course_id):
        return get_object_or_404(NidusCourse, pk=course_id, instructors=request.user)

    def get(self, request, course_id):
        course = self._get_owned_course(request, course_id)
        queryset = get_course_sections(course)
        ordering = request.query_params.get('ordering')
        if ordering in ('position', '-position'):
            queryset = queryset.order_by(ordering, 'id')
        serializer = CourseSectionSerializer(queryset, many=True)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)


class CourseSectionCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_course(self, request, course_id):
        return get_object_or_404(NidusCourse, pk=course_id, instructors=request.user)

    def post(self, request, course_id):
        course = self._get_owned_course(request, course_id)
        serializer = CourseSectionCreateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        section = serializer.save(course=course)
        return Response(
            {
                'success': True,
                'message': 'Section created successfully.',
                'data': CourseSectionSerializer(section).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CourseSectionDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_section(self, request, section_id):
        return get_object_or_404(
            CourseSection.objects.select_related('course'),
            pk=section_id,
            course__instructors=request.user,
        )

    def get(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        return Response({'success': True, 'data': CourseSectionSerializer(section).data}, status=status.HTTP_200_OK)

    def patch(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        serializer = CourseSectionCreateUpdateSerializer(section, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        section = serializer.save()
        return Response(
            {'success': True, 'message': 'Section updated successfully.', 'data': CourseSectionSerializer(section).data},
            status=status.HTTP_200_OK,
        )

    def put(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        serializer = CourseSectionCreateUpdateSerializer(section, data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        section = serializer.save()
        return Response(
            {'success': True, 'message': 'Section replaced successfully.', 'data': CourseSectionSerializer(section).data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        section.delete()
        return Response({'success': True, 'message': 'Section deleted successfully.'}, status=status.HTTP_204_NO_CONTENT)


class LectureListAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_section(self, request, section_id):
        return get_object_or_404(
            CourseSection.objects.select_related('course'),
            pk=section_id,
            course__instructors=request.user,
        )

    def get(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        queryset = get_section_lectures(section)
        ordering = request.query_params.get('ordering')
        if ordering in ('position', '-position'):
            queryset = queryset.order_by(ordering, 'id')
        serializer = LectureSerializer(queryset, many=True)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)


class LectureCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_section(self, request, section_id):
        return get_object_or_404(
            CourseSection.objects.select_related('course'),
            pk=section_id,
            course__instructors=request.user,
        )

    def post(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        serializer = LectureCreateUpdateSerializer(data=request.data, context={'section': section})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lecture = serializer.save()
        return Response(
            {
                'success': True,
                'message': 'Lecture created successfully.',
                'data': LectureSerializer(lecture).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LectureDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_lecture(self, request, lecture_id):
        return get_object_or_404(
            Lecture.objects.select_related('section__course').prefetch_related('video_assets'),
            pk=lecture_id,
            section__course__instructors=request.user,
        )

    def get(self, request, lecture_id):
        lecture = self._get_owned_lecture(request, lecture_id)
        return Response({'success': True, 'data': LectureSerializer(lecture).data}, status=status.HTTP_200_OK)

    def patch(self, request, lecture_id):
        lecture = self._get_owned_lecture(request, lecture_id)
        serializer = LectureCreateUpdateSerializer(lecture, data=request.data, partial=True, context={'section': lecture.section})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lecture = serializer.save()
        return Response(
            {'success': True, 'message': 'Lecture updated successfully.', 'data': LectureSerializer(lecture).data},
            status=status.HTTP_200_OK,
        )

    def put(self, request, lecture_id):
        lecture = self._get_owned_lecture(request, lecture_id)
        serializer = LectureCreateUpdateSerializer(lecture, data=request.data, context={'section': lecture.section})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        lecture = serializer.save()
        return Response(
            {'success': True, 'message': 'Lecture replaced successfully.', 'data': LectureSerializer(lecture).data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, lecture_id):
        lecture = self._get_owned_lecture(request, lecture_id)
        lecture.delete()
        return Response({'success': True, 'message': 'Lecture deleted successfully.'}, status=status.HTTP_204_NO_CONTENT)

from courses.all_views.course_views import (
    CourseCreateAPIView,
    CourseDetailView,
    CourseListAPIView,
)
from courses.all_views.content_views import (
    CourseSectionDetailAPIView,
    CourseSectionCreateAPIView,
    CourseSectionListAPIView,
    LectureDetailAPIView,
    LectureCreateAPIView,
    LectureListAPIView,
)

__all__ = [
    'CourseListAPIView',
    'CourseCreateAPIView',
    'CourseDetailView',
    'CourseSectionListAPIView',
    'CourseSectionCreateAPIView',
    'CourseSectionDetailAPIView',
    'LectureListAPIView',
    'LectureCreateAPIView',
    'LectureDetailAPIView',
]

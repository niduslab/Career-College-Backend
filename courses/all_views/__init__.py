from courses.all_views.course_views import (
    CourseCreateAPIView,
    CourseDetailView,
    CourseListAPIView,
)
from courses.all_views.content_views import (
    CourseAudienceDetailAPIView,
    CourseAudienceListCreateAPIView,
    CourseLearningObjectiveDetailAPIView,
    CourseLearningObjectiveListCreateAPIView,
    CoursePreRequisiteDetailAPIView,
    CoursePreRequisiteListCreateAPIView,
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
    'CourseLearningObjectiveListCreateAPIView',
    'CourseLearningObjectiveDetailAPIView',
    'CoursePreRequisiteListCreateAPIView',
    'CoursePreRequisiteDetailAPIView',
    'CourseAudienceListCreateAPIView',
    'CourseAudienceDetailAPIView',
    'CourseSectionListAPIView',
    'CourseSectionCreateAPIView',
    'CourseSectionDetailAPIView',
    'LectureListAPIView',
    'LectureCreateAPIView',
    'LectureDetailAPIView',
]

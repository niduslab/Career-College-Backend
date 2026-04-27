from django.urls import path

from courses.views import (
    CourseAudienceDetailAPIView,
    CourseAudienceListCreateAPIView,
    CourseCreateAPIView,
    CourseDetailView,
    CourseLearningObjectiveDetailAPIView,
    CourseLearningObjectiveListCreateAPIView,
    CourseListAPIView,
    CoursePreRequisiteDetailAPIView,
    CoursePreRequisiteListCreateAPIView,
    CourseSectionCreateAPIView,
    CourseSectionDetailAPIView,
    CourseSectionListAPIView,
    LectureCreateAPIView,
    LectureDetailAPIView,
    LectureListAPIView,
)

app_name = 'courses'

urlpatterns = [
    path('', CourseListAPIView.as_view(), name='course-list'),
    path('create/', CourseCreateAPIView.as_view(), name='course-create'),
    path('<int:pk>/', CourseDetailView.as_view(), name='course-detail'),
    path('<int:course_id>/learning-objectives/', CourseLearningObjectiveListCreateAPIView.as_view(), name='learning-objective-list-create'),
    path('learning-objectives/<int:item_id>/', CourseLearningObjectiveDetailAPIView.as_view(), name='learning-objective-detail'),
    path('<int:course_id>/prerequisites/', CoursePreRequisiteListCreateAPIView.as_view(), name='prerequisite-list-create'),
    path('prerequisites/<int:item_id>/', CoursePreRequisiteDetailAPIView.as_view(), name='prerequisite-detail'),
    path('<int:course_id>/audiences/', CourseAudienceListCreateAPIView.as_view(), name='audience-list-create'),
    path('audiences/<int:item_id>/', CourseAudienceDetailAPIView.as_view(), name='audience-detail'),
    path('<int:course_id>/sections/', CourseSectionListAPIView.as_view(), name='section-list'),
    path('<int:course_id>/sections/create/', CourseSectionCreateAPIView.as_view(), name='section-create'),
    path('sections/<int:section_id>/', CourseSectionDetailAPIView.as_view(), name='section-detail'),
    path('sections/<int:section_id>/lectures/', LectureListAPIView.as_view(), name='lecture-list'),
    path('sections/<int:section_id>/lectures/create/', LectureCreateAPIView.as_view(), name='lecture-create'),
    path('lectures/<int:lecture_id>/', LectureDetailAPIView.as_view(), name='lecture-detail'),
]

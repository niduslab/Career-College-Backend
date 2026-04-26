from django.urls import path

from courses.views import (
    CourseCreateAPIView,
    CourseDetailView,
    CourseListAPIView,
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
    path('<int:course_id>/sections/', CourseSectionListAPIView.as_view(), name='section-list'),
    path('<int:course_id>/sections/create/', CourseSectionCreateAPIView.as_view(), name='section-create'),
    path('sections/<int:section_id>/', CourseSectionDetailAPIView.as_view(), name='section-detail'),
    path('sections/<int:section_id>/lectures/', LectureListAPIView.as_view(), name='lecture-list'),
    path('sections/<int:section_id>/lectures/create/', LectureCreateAPIView.as_view(), name='lecture-create'),
    path('lectures/<int:lecture_id>/', LectureDetailAPIView.as_view(), name='lecture-detail'),
]

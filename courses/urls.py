from django.urls import path

from courses.views import (
    AssignmentDetailAPIView,
    AssignmentListAPIView,
    AssignmentQuestionDetailAPIView,
    AssignmentQuestionListCreateAPIView,
    AssignmentQuestionReorderAPIView,
    CodingExerciseDetailAPIView,
    CodingExerciseLanguageConfigDetailAPIView,
    CodingExerciseLanguageConfigListCreateAPIView,
    CodingTestCaseDetailAPIView,
    CodingTestCaseListCreateAPIView,
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
    LectureDetailAPIView,
    LectureListAPIView,
    QuizAnswerDetailAPIView,
    QuizAnswerListCreateAPIView,
    QuizDetailAPIView,
    QuizQuestionDetailAPIView,
    QuizQuestionListCreateAPIView,
    SectionContentListCreateAPIView,
    SectionContentReorderAPIView,
)

app_name = 'courses'

urlpatterns = [
    # -------------------------------------------------------------------------
    # Courses
    # -------------------------------------------------------------------------
    path('', CourseListAPIView.as_view(), name='course-list'),
    path('create/', CourseCreateAPIView.as_view(), name='course-create'),
    path('<int:pk>/', CourseDetailView.as_view(), name='course-detail'),

    # -------------------------------------------------------------------------
    # Course metadata (objectives / prerequisites / audiences)
    # -------------------------------------------------------------------------
    path('<int:course_id>/learning-objectives/', CourseLearningObjectiveListCreateAPIView.as_view(), name='learning-objective-list-create'),
    path('learning-objectives/<int:item_id>/', CourseLearningObjectiveDetailAPIView.as_view(), name='learning-objective-detail'),
    path('<int:course_id>/prerequisites/', CoursePreRequisiteListCreateAPIView.as_view(), name='prerequisite-list-create'),
    path('prerequisites/<int:item_id>/', CoursePreRequisiteDetailAPIView.as_view(), name='prerequisite-detail'),
    path('<int:course_id>/audiences/', CourseAudienceListCreateAPIView.as_view(), name='audience-list-create'),
    path('audiences/<int:item_id>/', CourseAudienceDetailAPIView.as_view(), name='audience-detail'),

    # -------------------------------------------------------------------------
    # Sections
    # -------------------------------------------------------------------------
    path('<int:course_id>/sections/', CourseSectionListAPIView.as_view(), name='section-list'),
    path('<int:course_id>/sections/create/', CourseSectionCreateAPIView.as_view(), name='section-create'),
    path('sections/<int:section_id>/', CourseSectionDetailAPIView.as_view(), name='section-detail'),

    # -------------------------------------------------------------------------
    # Section content (curriculum list + unified create)
    # -------------------------------------------------------------------------
    path('sections/<int:section_id>/contents/', SectionContentListCreateAPIView.as_view(), name='section-content-list-create'),
    path('contents/<int:content_id>/reorder/', SectionContentReorderAPIView.as_view(), name='section-content-reorder'),

    # -------------------------------------------------------------------------
    # Lectures
    # -------------------------------------------------------------------------
    path('sections/<int:section_id>/lectures/', LectureListAPIView.as_view(), name='lecture-list'),
    path('lectures/<int:lecture_id>/', LectureDetailAPIView.as_view(), name='lecture-detail'),

    # -------------------------------------------------------------------------
    # Quizzes
    # -------------------------------------------------------------------------
    path('quizzes/<int:quiz_id>/', QuizDetailAPIView.as_view(), name='quiz-detail'),

    # -------------------------------------------------------------------------
    # Quiz questions
    # -------------------------------------------------------------------------
    path('quizzes/<int:quiz_id>/questions/', QuizQuestionListCreateAPIView.as_view(), name='quiz-question-list-create'),
    path('quiz-questions/<int:question_id>/', QuizQuestionDetailAPIView.as_view(), name='quiz-question-detail'),

    # -------------------------------------------------------------------------
    # Quiz answers
    # -------------------------------------------------------------------------
    path('quiz-questions/<int:question_id>/answers/', QuizAnswerListCreateAPIView.as_view(), name='quiz-answer-list-create'),
    path('quiz-answers/<int:answer_id>/', QuizAnswerDetailAPIView.as_view(), name='quiz-answer-detail'),

    # -------------------------------------------------------------------------
    # Assignments
    # -------------------------------------------------------------------------
    path('sections/<int:section_id>/assignments/', AssignmentListAPIView.as_view(), name='assignment-list-create'),
    path('assignments/<int:assignment_id>/', AssignmentDetailAPIView.as_view(), name='assignment-detail'),
    path('assignments/<int:assignment_id>/questions/', AssignmentQuestionListCreateAPIView.as_view(), name='assignment-question-list-create'),
    path('assignments/<int:assignment_id>/questions/reorder/', AssignmentQuestionReorderAPIView.as_view(), name='assignment-question-reorder'),
    path('assignment-questions/<int:question_id>/', AssignmentQuestionDetailAPIView.as_view(), name='assignment-question-detail'),

    # -------------------------------------------------------------------------
    # Coding exercises
    # -------------------------------------------------------------------------
    path('coding-exercises/<int:exercise_id>/', CodingExerciseDetailAPIView.as_view(), name='coding-exercise-detail'),
    path('coding-exercises/<int:exercise_id>/language-configs/', CodingExerciseLanguageConfigListCreateAPIView.as_view(), name='coding-lang-config-list-create'),
    path('coding-exercises/<int:exercise_id>/language-configs/<int:config_id>/', CodingExerciseLanguageConfigDetailAPIView.as_view(), name='coding-lang-config-detail'),
    path('coding-exercises/<int:exercise_id>/testcases/', CodingTestCaseListCreateAPIView.as_view(), name='coding-testcase-list-create'),
    path('coding-exercises/<int:exercise_id>/testcases/<int:tc_id>/', CodingTestCaseDetailAPIView.as_view(), name='coding-testcase-detail'),
]

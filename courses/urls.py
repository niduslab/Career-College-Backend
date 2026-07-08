from django.urls import path

from courses.views import (
    CertificateDownloadView,
    CertificateVerifyView,
    LearnerCertificateView,
    CourseReviewListView,
    CourseReviewSummaryView,
    MyReviewView,
    ReviewVoteView,
    CourseInstructorInviteCreateView,
    CourseInstructorInviteListView,
    CourseInstructorInviteRevokeView,
    InviteAcceptView,
    InviteDeclineView,
    MyInviteListView,
    InstitutionCourseInstructorView,
    CourseScheduleActivateView,
    CourseScheduleArchiveView,
    CourseScheduleDetailView,
    CourseScheduleListCreateView,
    CourseScheduleReworkView,
    AssignmentDetailAPIView,
    AssignmentListAPIView,
    AssignmentQuestionDetailAPIView,
    AssignmentQuestionListCreateAPIView,
    AssignmentQuestionReorderAPIView,
    CatalogCourseDetailView,
    CatalogCourseListView,
    CodingExerciseDetailAPIView,
    CodingExerciseLanguageConfigDetailAPIView,
    CodingExerciseLanguageConfigListCreateAPIView,
    CodingTestCaseDetailAPIView,
    CodingTestCaseListCreateAPIView,
    CourseAdminReviewView,
    CourseArchiveView,
    CourseInstitutionReviewView,
    CourseMarkFinishedView,
    CourseRestoreView,
    CourseAudienceDetailAPIView,
    CourseAudienceListCreateAPIView,
    CourseCreateAPIView,
    CourseDetailView,
    CourseEnrollView,
    CourseLearningObjectiveDetailAPIView,
    CourseLearningObjectiveListCreateAPIView,
    CourseListAPIView,
    CoursePreRequisiteDetailAPIView,
    CoursePreRequisiteListCreateAPIView,
    CourseReworkView,
    CourseSectionCreateAPIView,
    CourseSectionDetailAPIView,
    CourseSectionListAPIView,
    CourseSubmitForReviewView,
    CourseUnenrollView,
    LearnerAssignmentDetailView,
    LearnerAssignmentSubmissionDetailView,
    LearnerAssignmentSubmissionRetryView,
    LearnerAssignmentSubmitView,
    LearnerCodingExerciseDetailView,
    LearnerCodingRunView,
    LearnerCodingSubmissionDetailView,
    LearnerCodingSubmissionRetryView,
    LearnerCodingSubmitView,
    LearnerCodingTaskStatusView,
    LearnerCurriculumView,
    LearnerLectureDetailView,
    LearnerLectureProgressView,
    LearnerQuizDetailView,
    LearnerQuizSubmitView,
    LectureDetailAPIView,
    LectureListAPIView,
    MyCoursesDetailView,
    MyCoursesListView,
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
    # Public catalog (no auth required)
    # -------------------------------------------------------------------------
    path('catalog/', CatalogCourseListView.as_view(), name='catalog-list'),
    path('catalog/<slug:slug>/', CatalogCourseDetailView.as_view(), name='catalog-detail'),

    # -------------------------------------------------------------------------
    # Enrollment (authenticated learner)
    # -------------------------------------------------------------------------
    path('<slug:slug>/enroll/', CourseEnrollView.as_view(), name='course-enroll'),
    path('<slug:slug>/unenroll/', CourseUnenrollView.as_view(), name='course-unenroll'),
    path('my-courses/', MyCoursesListView.as_view(), name='my-courses-list'),
    path('my-courses/<slug:slug>/', MyCoursesDetailView.as_view(), name='my-courses-detail'),
    path('my-courses/<slug:slug>/certificate/', LearnerCertificateView.as_view(), name='my-courses-certificate'),

    # -------------------------------------------------------------------------
    # Certificate verification and download (public)
    # -------------------------------------------------------------------------
    path('certificates/<uuid:certificate_uid>/verify/', CertificateVerifyView.as_view(), name='certificate-verify'),
    path('certificates/<uuid:certificate_uid>/download/', CertificateDownloadView.as_view(), name='certificate-download'),

    # -------------------------------------------------------------------------
    # Course reviews (slug-based → 403/404; review vote is numeric → 404)
    # Literal path segments (summary/, my-review/) are declared before the
    # plain list path to avoid any ambiguity in future route additions.
    # -------------------------------------------------------------------------
    path('<slug:slug>/reviews/summary/', CourseReviewSummaryView.as_view(), name='course-review-summary'),
    path('<slug:slug>/reviews/my-review/', MyReviewView.as_view(), name='my-course-review'),
    path('<slug:slug>/reviews/', CourseReviewListView.as_view(), name='course-review-list'),
    path('reviews/<int:review_id>/vote/', ReviewVoteView.as_view(), name='review-vote'),

    # -------------------------------------------------------------------------
    # Learner consumption (Phase 1 — curriculum outline, lecture detail, progress)
    # -------------------------------------------------------------------------
    path('learn/<slug:slug>/curriculum/', LearnerCurriculumView.as_view(), name='learner-curriculum'),
    path('learn/lectures/<int:lecture_id>/', LearnerLectureDetailView.as_view(), name='learner-lecture-detail'),
    path('learn/lectures/<int:lecture_id>/progress/', LearnerLectureProgressView.as_view(), name='learner-lecture-progress'),
    path('learn/quizzes/<int:quiz_id>/', LearnerQuizDetailView.as_view(), name='learner-quiz-detail'),
    path('learn/quizzes/<int:quiz_id>/submit/', LearnerQuizSubmitView.as_view(), name='learner-quiz-submit'),
    path('learn/assignments/<int:assignment_id>/', LearnerAssignmentDetailView.as_view(), name='learner-assignment-detail'),
    path('learn/assignments/<int:assignment_id>/submit/', LearnerAssignmentSubmitView.as_view(), name='learner-assignment-submit'),
    path('learn/assignments/submissions/<int:submission_id>/', LearnerAssignmentSubmissionDetailView.as_view(), name='learner-assignment-submission-detail'),
    path('learn/assignments/submissions/<int:submission_id>/retry/', LearnerAssignmentSubmissionRetryView.as_view(), name='learner-assignment-submission-retry'),
    # Coding-exercise learner endpoints. Order matters: the `tasks/` and
    # `submissions/` literals must precede the numeric-ID detail route so
    # Django's URL resolver doesn't greedily match `<int:exercise_id>` for them.
    path('learn/coding-exercises/tasks/<str:task_id>/', LearnerCodingTaskStatusView.as_view(), name='learner-coding-task-status'),
    path('learn/coding-exercises/submissions/<int:submission_id>/', LearnerCodingSubmissionDetailView.as_view(), name='learner-coding-submission-detail'),
    path('learn/coding-exercises/submissions/<int:submission_id>/retry/', LearnerCodingSubmissionRetryView.as_view(), name='learner-coding-submission-retry'),
    path('learn/coding-exercises/<int:exercise_id>/', LearnerCodingExerciseDetailView.as_view(), name='learner-coding-exercise-detail'),
    path('learn/coding-exercises/<int:exercise_id>/run/', LearnerCodingRunView.as_view(), name='learner-coding-run'),
    path('learn/coding-exercises/<int:exercise_id>/submit/', LearnerCodingSubmitView.as_view(), name='learner-coding-submit'),

    # -------------------------------------------------------------------------
    # Instructor course management
    # -------------------------------------------------------------------------
    path('', CourseListAPIView.as_view(), name='course-list'),
    path('create/', CourseCreateAPIView.as_view(), name='course-create'),
    path('<int:pk>/', CourseDetailView.as_view(), name='course-detail'),

    # -------------------------------------------------------------------------
    # Co-instructor invitations
    # -------------------------------------------------------------------------
    path('<int:pk>/instructors/invite/', CourseInstructorInviteCreateView.as_view(), name='course-instructor-invite-create'),
    path('<int:pk>/instructors/invites/', CourseInstructorInviteListView.as_view(), name='course-instructor-invite-list'),
    path('<int:pk>/instructors/invites/<int:invite_id>/', CourseInstructorInviteRevokeView.as_view(), name='course-instructor-invite-revoke'),
    path('invites/my/', MyInviteListView.as_view(), name='my-invite-list'),
    path('invites/<uuid:token>/accept/', InviteAcceptView.as_view(), name='invite-accept'),
    path('invites/<uuid:token>/decline/', InviteDeclineView.as_view(), name='invite-decline'),

    # Partner institution: direct roster management (no accept step)
    path('<int:pk>/institution-instructors/', InstitutionCourseInstructorView.as_view(), name='institution-course-instructor-add'),
    path('<int:pk>/institution-instructors/<int:expert_user_id>/', InstitutionCourseInstructorView.as_view(), name='institution-course-instructor-remove'),

    # -------------------------------------------------------------------------
    # Course status transitions
    # -------------------------------------------------------------------------
    path('<int:pk>/submit/', CourseSubmitForReviewView.as_view(), name='course-submit'),
    path('<int:pk>/finish/', CourseMarkFinishedView.as_view(), name='course-finish'),
    path('<int:pk>/institution-review/', CourseInstitutionReviewView.as_view(), name='course-institution-review'),
    path('<int:pk>/review/', CourseAdminReviewView.as_view(), name='course-review'),
    path('<int:pk>/rework/', CourseReworkView.as_view(), name='course-rework'),
    path('<int:pk>/archive/', CourseArchiveView.as_view(), name='course-archive'),
    path('<int:pk>/restore/', CourseRestoreView.as_view(), name='course-restore'),

    # -------------------------------------------------------------------------
    # Course schedules (cohorts)
    # -------------------------------------------------------------------------
    path('<int:pk>/schedules/', CourseScheduleListCreateView.as_view(), name='course-schedule-list-create'),
    path('<int:pk>/schedules/<int:schedule_id>/', CourseScheduleDetailView.as_view(), name='course-schedule-detail'),
    path('<int:pk>/schedules/<int:schedule_id>/activate/', CourseScheduleActivateView.as_view(), name='course-schedule-activate'),
    path('<int:pk>/schedules/<int:schedule_id>/archive/', CourseScheduleArchiveView.as_view(), name='course-schedule-archive'),
    path('<int:pk>/schedules/<int:schedule_id>/rework/', CourseScheduleReworkView.as_view(), name='course-schedule-rework'),

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

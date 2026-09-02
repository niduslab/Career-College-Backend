from django.urls import path

from courses.views import (
    CourseCategoryDetailView,
    CourseCategoryListCreateView,
    AdminCertificateListView,
    CertificateDownloadView,
    CertificatePublicVerifyView,
    CertificateRestoreView,
    CertificateRevokeView,
    CertificateVerifyView,
    LearnerCertificateView,
    MyCertificateListView,
    LearnerActivityFeedView,
    LearnerContinueView,
    LearnerDashboardSummaryView,
    LearnerUpcomingView,
    CourseWishlistView,
    WishlistListView,
    LearnerNoteDetailView,
    LearnerNoteListCreateView,
    CourseReviewListView,
    CourseReviewSummaryView,
    MyReviewView,
    ReviewVoteView,
    CourseQuestionListView,
    CourseQuestionDetailView,
    QuestionReplyCreateView,
    QuestionReplyDetailView,
    QuestionPinView,
    QuestionUpvoteView,
    ReplyUpvoteView,
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
    AssignmentRubricPreviewAPIView,
    ArticleLecturePreviewAPIView,
    CourseOutlinePreviewAPIView,
    CatalogCourseDetailView,
    CatalogCourseListView,
    CodingExerciseDetailAPIView,
    CodingExerciseRunAPIView,
    CourseAdminCurriculumView,
    CourseAdminListView,
    CourseAdminPendingReviewListView,
    CourseAdminReviewView,
    CourseArchiveView,
    CourseInstitutionReviewQueueView,
    CourseInstitutionReviewView,
    CourseMarkFinishedView,
    CourseRestoreView,
    CourseCreateAPIView,
    CourseDetailView,
    CourseEnrollView,
    CourseListAPIView,
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
    QuizQuestionBulkCreateAPIView,
    QuizQuestionDetailAPIView,
    QuizQuestionListCreateAPIView,
    QuizQuestionsPreviewAPIView,
    SectionContentListCreateAPIView,
    SectionContentReorderAPIView,
    LearningPathListView,
    LearningPathDetailView,
    LearningPathProgressView,
    LearningPathEnrollView,
    MyLearningPathsView,
    LearningPathManageListView,
    LearningPathManageDetailView,
    LearningPathMilestoneCreateView,
    LearningPathMilestoneDetailView,
    LearningPathMilestoneReorderView,
)

app_name = 'courses'

urlpatterns = [
    # -------------------------------------------------------------------------
    # Public catalog (no auth required)
    # -------------------------------------------------------------------------
    path('catalog/', CatalogCourseListView.as_view(), name='catalog-list'),
    path('catalog/<slug:slug>/', CatalogCourseDetailView.as_view(), name='catalog-detail'),

    # -------------------------------------------------------------------------
    # Course categories (public list, admin create/update/delete)
    # -------------------------------------------------------------------------
    path('categories/', CourseCategoryListCreateView.as_view(), name='category-list-create'),
    path('categories/<int:pk>/', CourseCategoryDetailView.as_view(), name='category-detail'),

    # -------------------------------------------------------------------------
    # AI-assisted authoring (see docs/architecture/32-ai-course-outline-generator.md
    # and docs/architecture/34-ai-article-lecture-generator.md)
    #
    # ORDERING: literal-prefixed, declared above the `<slug:slug>/...` block and
    # the instructor block's `path('<int:pk>/', ...)` so neither can shadow it.
    # -------------------------------------------------------------------------
    path('ai/outline-preview/', CourseOutlinePreviewAPIView.as_view(), name='ai-outline-preview'),
    path('ai/article-lecture-preview/', ArticleLecturePreviewAPIView.as_view(), name='ai-article-lecture-preview'),
    path('ai/quiz-questions-preview/', QuizQuestionsPreviewAPIView.as_view(), name='ai-quiz-questions-preview'),

    # -------------------------------------------------------------------------
    # Enrollment (authenticated learner)
    # -------------------------------------------------------------------------
    path('<slug:slug>/enroll/', CourseEnrollView.as_view(), name='course-enroll'),
    path('<slug:slug>/unenroll/', CourseUnenrollView.as_view(), name='course-unenroll'),
    # Wishlist toggle. Safe beside enroll/unenroll: all three are
    # <slug>/<fixed-literal>, so they cannot shadow one another.
    path('<slug:slug>/wishlist/', CourseWishlistView.as_view(), name='course-wishlist'),
    path('my-courses/', MyCoursesListView.as_view(), name='my-courses-list'),
    path('my-courses/<slug:slug>/', MyCoursesDetailView.as_view(), name='my-courses-detail'),
    path('my-courses/<slug:slug>/certificate/', LearnerCertificateView.as_view(), name='my-courses-certificate'),

    # -------------------------------------------------------------------------
    # Learner dashboard aggregates, certificates list, wishlist, notes
    #
    # ORDERING: these are literal-prefixed and must stay above the instructor
    # block's `path('', ...)` / `path('<int:pk>/', ...)`. They are safe against
    # the `<slug:slug>/...` routes above because those all pin a fixed second
    # segment (enroll / unenroll / wishlist / reviews / questions).
    #
    # DO NOT nest any of these under `my-courses/` — `my-courses/<slug:slug>/`
    # (declared just above) would swallow them, e.g. `my-courses/certificates/`
    # resolves to MyCoursesDetailView with slug='certificates'.
    # -------------------------------------------------------------------------
    path('learner/dashboard/summary/', LearnerDashboardSummaryView.as_view(), name='learner-dashboard-summary'),
    path('learner/activity/', LearnerActivityFeedView.as_view(), name='learner-activity'),
    path('learner/upcoming/', LearnerUpcomingView.as_view(), name='learner-upcoming'),
    path('learner/continue/', LearnerContinueView.as_view(), name='learner-continue'),

    path('my-certificates/', MyCertificateListView.as_view(), name='my-certificates-list'),

    path('wishlist/', WishlistListView.as_view(), name='wishlist-list'),

    # -------------------------------------------------------------------------
    # Learning paths (see docs/architecture/28-learning-paths.md)
    # `manage/` and `manage/<int:pk>/...` (literal-prefixed, instructor/admin
    # authoring) are declared before `<slug:slug>/...` so "manage" is never
    # swallowed as a path slug. `my-learning-paths/` sits at the top level
    # (not nested under `learning-paths/`) for the same reason `my-courses/`
    # isn't nested under a slug route.
    # -------------------------------------------------------------------------
    path('learning-paths/manage/', LearningPathManageListView.as_view(), name='learning-path-manage-list'),
    path('learning-paths/manage/<int:pk>/', LearningPathManageDetailView.as_view(), name='learning-path-manage-detail'),
    path('learning-paths/manage/<int:pk>/milestones/', LearningPathMilestoneCreateView.as_view(), name='learning-path-milestone-create'),
    path('learning-paths/manage/<int:pk>/milestones/reorder/', LearningPathMilestoneReorderView.as_view(), name='learning-path-milestone-reorder'),
    path('learning-paths/manage/<int:pk>/milestones/<int:milestone_id>/', LearningPathMilestoneDetailView.as_view(), name='learning-path-milestone-detail'),

    path('my-learning-paths/', MyLearningPathsView.as_view(), name='my-learning-paths'),

    path('learning-paths/', LearningPathListView.as_view(), name='learning-path-list'),
    path('learning-paths/<slug:slug>/progress/', LearningPathProgressView.as_view(), name='learning-path-progress'),
    path('learning-paths/<slug:slug>/enroll/', LearningPathEnrollView.as_view(), name='learning-path-enroll'),
    path('learning-paths/<slug:slug>/', LearningPathDetailView.as_view(), name='learning-path-detail'),

    # Literal `notes/` precedes the numeric detail route.
    path('notes/', LearnerNoteListCreateView.as_view(), name='learner-note-list-create'),
    path('notes/<int:pk>/', LearnerNoteDetailView.as_view(), name='learner-note-detail'),

    # -------------------------------------------------------------------------
    # Certificate verification and download (public)
    # -------------------------------------------------------------------------
    path('certificates/<uuid:certificate_uid>/verify/', CertificateVerifyView.as_view(), name='certificate-verify'),
    path('certificates/<uuid:certificate_uid>/download/', CertificateDownloadView.as_view(), name='certificate-download'),
    path('certificates/<uuid:certificate_uid>/revoke/', CertificateRevokeView.as_view(), name='certificate-revoke'),
    path('certificates/<uuid:certificate_uid>/restore/', CertificateRestoreView.as_view(), name='certificate-restore'),
    # Accepts the human-readable certificate ID or the UUID. Declared after the
    # <uuid:...> routes so the typed converter wins for a bare UUID.
    path('certificates/verify/<str:identifier>/', CertificatePublicVerifyView.as_view(), name='certificate-public-verify'),

    # Admin certificate browser — the discovery surface for revoke/restore,
    # which otherwise need a UUID the admin has no way to look up.
    path('admin/certificates/', AdminCertificateListView.as_view(), name='admin-certificate-list'),

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
    # Course Q&A / discussion (enrolled learners + course instructors only)
    # Slug list/create → 403 on no access; numeric-ID routes → 404.
    # Literal segments (replies/, pin/, upvote/) precede bare numeric routes.
    # -------------------------------------------------------------------------
    path('<slug:slug>/questions/', CourseQuestionListView.as_view(), name='course-question-list'),
    path('questions/<int:question_id>/replies/', QuestionReplyCreateView.as_view(), name='question-reply-create'),
    path('questions/<int:question_id>/pin/', QuestionPinView.as_view(), name='question-pin'),
    path('questions/<int:question_id>/upvote/', QuestionUpvoteView.as_view(), name='question-upvote'),
    path('questions/<int:question_id>/', CourseQuestionDetailView.as_view(), name='course-question-detail'),
    path('replies/<int:reply_id>/upvote/', ReplyUpvoteView.as_view(), name='reply-upvote'),
    path('replies/<int:reply_id>/', QuestionReplyDetailView.as_view(), name='question-reply-detail'),

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
    path('institution-review-queue/', CourseInstitutionReviewQueueView.as_view(), name='course-institution-review-queue'),
    path('<int:pk>/institution-review/', CourseInstitutionReviewView.as_view(), name='course-institution-review'),
    path('admin/', CourseAdminListView.as_view(), name='course-admin-list'),
    path('admin/pending-review/', CourseAdminPendingReviewListView.as_view(), name='course-admin-pending-review'),
    path('<int:pk>/review/', CourseAdminReviewView.as_view(), name='course-review'),
    path('<int:pk>/review/curriculum/', CourseAdminCurriculumView.as_view(), name='course-admin-curriculum'),
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
    # Transactional batch create — one request instead of N questions + N*M answers.
    path('quizzes/<int:quiz_id>/questions/bulk/', QuizQuestionBulkCreateAPIView.as_view(), name='quiz-question-bulk-create'),
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
    path('assignments/rubric-preview/', AssignmentRubricPreviewAPIView.as_view(), name='assignment-rubric-preview'),
    path('assignment-questions/<int:question_id>/', AssignmentQuestionDetailAPIView.as_view(), name='assignment-question-detail'),

    # -------------------------------------------------------------------------
    # Coding exercises
    # -------------------------------------------------------------------------
    path('coding-exercises/<int:exercise_id>/', CodingExerciseDetailAPIView.as_view(), name='coding-exercise-detail'),
    path('coding-exercises/<int:exercise_id>/run/', CodingExerciseRunAPIView.as_view(), name='coding-exercise-instructor-run'),
]

from django.contrib import admin

from courses.models import (
    CodingExercise,
    CourseCategory,
    CourseInstructorInvite,
    CourseReview,
    CourseSection,
    Enrollment,
    Lecture,
    NidusCourse,
    Quiz,
    QuizAnswer,
    QuizQuestion,
    ReviewVote,
    SectionContent,
    VideoAsset,
    VideoProcessingJob,
    WatchProgress,
)


@admin.register(NidusCourse)
class NidusCourseAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'title',
        'status',
        'is_published',
        'category',
        'price',
        'language',
        'level',
        'created_at',
    )
    list_filter = ('status', 'is_published', 'level', 'language', 'category')
    search_fields = ('title', 'slug', 'created_by__email', 'created_by__full_name')
    readonly_fields = ('created_at', 'updated_at', 'published_at')
    raw_id_fields = ('created_by', 'partner_institution')
    filter_horizontal = ('instructors',)


@admin.register(CourseCategory)
class CourseCategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'slug', 'parent', 'is_active')
    list_filter = ('is_active', 'parent')
    search_fields = ('name', 'slug')
    ordering = ('name',)


@admin.register(CourseSection)
class CourseSectionAdmin(admin.ModelAdmin):
    list_display = ('id', 'course', 'title', 'position', 'created_at')
    list_filter = ('course',)
    search_fields = ('title', 'course__title')
    ordering = ('course', 'position', 'id')


@admin.register(SectionContent)
class SectionContentAdmin(admin.ModelAdmin):
    list_display = ('id', 'section', 'item_type', 'content_type', 'object_id', 'position', 'created_at')
    list_filter = ('item_type', 'content_type', 'section')
    search_fields = ('section__title', 'section__course__title')
    ordering = ('section', 'position', 'id')


@admin.register(Lecture)
class LectureAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'section', 'lecture_type', 'created_at')
    list_filter = ('lecture_type', 'section')
    search_fields = ('title', 'section__title', 'section__course__title')
    ordering = ('section', 'id')


@admin.register(VideoAsset)
class VideoAssetAdmin(admin.ModelAdmin):
    list_display = ('id', 'lecture', 'status', 'is_active', 'file_size', 'created_at')
    list_filter = ('status', 'is_active')
    search_fields = ('lecture__title', 'original_filename', 'mime_type')
    ordering = ('-created_at',)


@admin.register(VideoProcessingJob)
class VideoProcessingJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'video_asset', 'status', 'started_at', 'completed_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('video_asset__lecture__title',)
    ordering = ('-created_at',)




@admin.register(WatchProgress)
class WatchProgressAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'lecture', 'watched_seconds', 'is_completed', 'last_watched_at')
    list_filter = ('is_completed',)
    search_fields = ('user__email', 'user__full_name', 'lecture__title')
    ordering = ('-last_watched_at',)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'user',
        'course',
        'enrollment_type',
        'is_active',
        'progress_percent',
        'completed_at',
        'last_accessed_at',
        'created_at',
    )
    list_filter = ('is_active', 'enrollment_type', 'completed_at')
    search_fields = ('user__email', 'user__full_name', 'course__title', 'course__slug')
    raw_id_fields = ('user', 'course')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'section', 'created_at')
    list_filter = ('section',)
    search_fields = ('title', 'section__title', 'section__course__title')
    filter_horizontal = ('related_lectures',)
    ordering = ('-created_at',)


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display = ('id', 'quiz', 'position', 'question_text')
    list_filter = ('quiz',)
    search_fields = ('question_text', 'quiz__title')
    ordering = ('quiz', 'position', 'id')


@admin.register(QuizAnswer)
class QuizAnswerAdmin(admin.ModelAdmin):
    list_display = ('id', 'question', 'answer_text', 'is_correct')
    list_filter = ('is_correct', 'question__quiz')
    search_fields = ('answer_text', 'question__question_text', 'question__quiz__title')
    ordering = ('question', 'id')


@admin.register(CodingExercise)
class CodingExerciseAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'title',
        'section',
        'language',
        'time_limit_ms',
        'created_at',
    )
    list_filter = ('language', 'section')
    search_fields = ('title', 'section__title', 'section__course__title')
    ordering = ('-created_at',)


@admin.register(CourseReview)
class CourseReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'course', 'rating', 'is_published', 'helpful_count', 'not_helpful_count', 'created_at')
    list_filter = ('is_published', 'rating')
    search_fields = ('user__email', 'user__full_name', 'course__title', 'headline')
    raw_id_fields = ('user', 'course', 'enrollment')
    readonly_fields = ('created_at', 'updated_at', 'helpful_count', 'not_helpful_count')
    ordering = ('-created_at',)
    actions = ['unpublish_reviews', 'publish_reviews']

    @admin.action(description='Unpublish selected reviews')
    def unpublish_reviews(self, request, queryset):
        course_ids = list(queryset.values_list('course_id', flat=True).distinct())
        updated = queryset.update(is_published=False)
        from courses.services.review_service import _recalculate_course_avg
        for course_id in course_ids:
            _recalculate_course_avg(course_id)
        self.message_user(request, f'{updated} review(s) unpublished.')

    @admin.action(description='Publish selected reviews')
    def publish_reviews(self, request, queryset):
        course_ids = list(queryset.values_list('course_id', flat=True).distinct())
        updated = queryset.update(is_published=True)
        from courses.services.review_service import _recalculate_course_avg
        for course_id in course_ids:
            _recalculate_course_avg(course_id)
        self.message_user(request, f'{updated} review(s) published.')


@admin.register(ReviewVote)
class ReviewVoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'review', 'voter', 'is_helpful', 'created_at')
    list_filter = ('is_helpful',)
    search_fields = ('voter__email', 'voter__full_name', 'review__course__title')
    raw_id_fields = ('review', 'voter')
    ordering = ('-created_at',)


@admin.register(CourseInstructorInvite)
class CourseInstructorInviteAdmin(admin.ModelAdmin):
    list_display = ('id', 'course', 'invited_by', 'invited_user', 'status', 'expires_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('course__title', 'invited_by__email', 'invited_user__email')
    readonly_fields = ('token', 'created_at', 'updated_at', 'responded_at')
    ordering = ('-created_at',)

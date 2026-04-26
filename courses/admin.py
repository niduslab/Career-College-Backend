from django.contrib import admin

from courses.models import CourseAudience, CourseCategory, CourseLearningObjective, CoursePreRequisite, NidusCourse


class CourseLearningObjectiveInline(admin.TabularInline):
    model = CourseLearningObjective
    extra = 0
    fields = ('text', 'display_order')
    ordering = ('display_order', 'id')


class CoursePreRequisiteInline(admin.TabularInline):
    model = CoursePreRequisite
    extra = 0
    fields = ('text', 'display_order')
    ordering = ('display_order', 'id')


class CourseAudienceInline(admin.TabularInline):
    model = CourseAudience
    extra = 0
    fields = ('text', 'display_order')
    ordering = ('display_order', 'id')


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
    raw_id_fields = ('created_by',)
    filter_horizontal = ('instructors', 'partner_institutions')
    inlines = (CourseLearningObjectiveInline, CoursePreRequisiteInline, CourseAudienceInline)


@admin.register(CourseCategory)
class CourseCategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'slug', 'parent', 'is_active', 'display_order')
    list_filter = ('is_active', 'parent')
    search_fields = ('name', 'slug')
    ordering = ('display_order', 'name')


@admin.register(CourseLearningObjective)
class CourseLearningObjectiveAdmin(admin.ModelAdmin):
    list_display = ('id', 'course', 'text', 'display_order')
    search_fields = ('course__title', 'text')
    list_filter = ('course',)
    ordering = ('course', 'display_order', 'id')


@admin.register(CoursePreRequisite)
class CoursePreRequisiteAdmin(admin.ModelAdmin):
    list_display = ('id', 'course', 'text', 'display_order')
    search_fields = ('course__title', 'text')
    list_filter = ('course',)
    ordering = ('course', 'display_order', 'id')


@admin.register(CourseAudience)
class CourseAudienceAdmin(admin.ModelAdmin):
    list_display = ('id', 'course', 'text', 'display_order')
    search_fields = ('course__title', 'text')
    list_filter = ('course',)
    ordering = ('course', 'display_order', 'id')

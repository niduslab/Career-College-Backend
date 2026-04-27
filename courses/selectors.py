from courses.models import NidusCourse


def get_course_base_queryset():
    return NidusCourse.objects.select_related('created_by', 'category').prefetch_related(
        'instructors',
        'partner_institutions',
        'learning_objectives',
        'prerequisites',
        'audiences',
    )


def get_instructor_courses(instructor):
    return get_course_base_queryset().filter(instructors=instructor).distinct().order_by('-created_at')


def get_instructor_course(instructor, course_id):
    return get_course_base_queryset().filter(instructors=instructor, pk=course_id).first()

from courses.models import NidusCourse


def get_course_base_queryset():
    return NidusCourse.objects.select_related('created_by', 'category', 'partner_institution').prefetch_related(
        'instructors',
    )


def get_instructor_courses(instructor):
    return get_course_base_queryset().filter(instructors=instructor).distinct().order_by('-created_at')


def get_instructor_course(instructor, course_id):
    return get_course_base_queryset().filter(instructors=instructor, pk=course_id).first()

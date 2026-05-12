from rest_framework import status
from rest_framework.response import Response


def guard_editable(course):
    """Return a 422 Response if the course is locked for editing, else None."""
    if not course.is_editable():
        return Response(
            {
                'success': False,
                'message': (
                    f'This course is "{course.status}" and cannot be edited. '
                    'Only courses in draft or rejected status can be modified.'
                ),
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return None

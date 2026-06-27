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


def save_authored(serializer, user, **extra):
    """Save an AuthoredModel via its serializer, stamping authorship.

    `created_by` on create (no bound instance), `last_edited_by` on every save.
    Extra kwargs pass through to `serializer.save()`.
    """
    if serializer.instance is None:
        extra['created_by'] = user
    extra['last_edited_by'] = user
    return serializer.save(**extra)


def guard_owner(course, user):
    """Return a 403 Response if user is not the course owner, else None."""
    if course.created_by != user:
        return Response(
            {
                'success': False,
                'message': 'Only the course owner can perform this action.',
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None

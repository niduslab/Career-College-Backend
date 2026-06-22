import logging

logger = logging.getLogger(__name__)


class DepartmentError(Exception):
    """Raised on department business-rule violations. Carries an HTTP status."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.http_status = http_status


def list_departments(institution, active_only=True):
    """Departments owned by *institution*, ordered by name."""
    from authentication.models import Department
    qs = Department.objects.filter(institution=institution)
    if active_only:
        qs = qs.filter(is_active=True)
    return qs


def get_institution_department(institution, department_id):
    """
    Fetch one of this institution's departments by id.

    Raises Department.DoesNotExist when missing OR owned by another institution
    (numeric id → 404, never leak existence).
    """
    from authentication.models import Department
    return Department.objects.get(pk=department_id, institution=institution)


def create_department(institution, name):
    """Create a department for *institution*. Names are unique per institution
    (case-insensitive). Raises DepartmentError on a blank or duplicate name."""
    from django.db import IntegrityError
    from authentication.models import Department

    name = (name or '').strip()
    if not name:
        raise DepartmentError('Department name is required.')

    if Department.objects.filter(institution=institution, name__iexact=name).exists():
        raise DepartmentError('A department with this name already exists.', http_status=422)

    try:
        return Department.objects.create(institution=institution, name=name)
    except IntegrityError:
        # Lost a race against a concurrent create of the same name.
        raise DepartmentError('A department with this name already exists.', http_status=422)


def rename_department(institution, department, name):
    """Rename a department, enforcing per-institution case-insensitive uniqueness."""
    from django.db import IntegrityError
    from authentication.models import Department

    name = (name or '').strip()
    if not name:
        raise DepartmentError('Department name is required.')

    clash = (
        Department.objects
        .filter(institution=institution, name__iexact=name)
        .exclude(pk=department.pk)
        .exists()
    )
    if clash:
        raise DepartmentError('A department with this name already exists.', http_status=422)

    department.name = name
    try:
        department.save(update_fields=['name', 'updated_at'])
    except IntegrityError:
        raise DepartmentError('A department with this name already exists.', http_status=422)
    return department


def set_department_active(department, active):
    """Activate or soft-deactivate a department. Deactivation hides it from the
    dropdown but keeps assigned experts pointing at it (history preserved)."""
    if department.is_active == active:
        return department
    department.is_active = active
    department.save(update_fields=['is_active', 'updated_at'])
    return department


def resolve_expert_department(institution, department_id):
    """
    Resolve a department id to one of *institution*'s departments for assignment
    to an expert. Returns the Department, or None when department_id is falsy.

    Raises ExpertError(422) when the id is not an active department of this
    institution — mirrors the cross-institution roster-assignment rule.
    """
    if not department_id:
        return None

    from authentication.models import Department
    from authentication.services.expert_service import ExpertError

    try:
        return Department.objects.get(
            pk=department_id, institution=institution, is_active=True,
        )
    except Department.DoesNotExist:
        raise ExpertError('Invalid department for your institution.', http_status=422)

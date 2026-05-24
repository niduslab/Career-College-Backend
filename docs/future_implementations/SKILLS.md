# Suggested Claude Code Skills for Career College Backend

> **Purpose:** Custom Claude Code skills that automate repetitive development workflows in this project. Each skill is a reusable prompt that understands the project's conventions (CLAUDE.md, file structure, boilerplate patterns) and scaffolds correct code on command.
>
> **Date:** 2026-05-23

---

## Table of Contents

1. [Scaffold New Content Type](#1-scaffold-new-content-type)
2. [Scaffold CRUD View](#2-scaffold-crud-view)
3. [Scaffold Learner Endpoint](#3-scaffold-learner-endpoint)
4. [Scaffold Celery Task](#4-scaffold-celery-task)
5. [Scaffold Service Function](#5-scaffold-service-function)
6. [Scaffold Test Suite](#6-scaffold-test-suite)
7. [Scaffold Serializer Pair](#7-scaffold-serializer-pair)
8. [Scaffold Management Command](#8-scaffold-management-command)
9. [Add New Permission Class](#9-add-new-permission-class)
10. [Scaffold New App](#10-scaffold-new-app)
11. [Scaffold State Machine](#11-scaffold-state-machine)
12. [Learner Security Audit](#12-learner-security-audit)
13. [API Endpoint Documentation Generator](#13-api-endpoint-documentation-generator)
14. [Scaffold Signal Handler](#14-scaffold-signal-handler)
15. [Migration Safety Check](#15-migration-safety-check)

---

## 1. Scaffold New Content Type

**Trigger phrases:** "new content type", "add content type", "scaffold content", "create a new curriculum item"

**Why this matters:** Adding a new content type (like Lecture, Quiz, Assignment, CodingExercise) is the most complex workflow in this codebase — it touches ~10 files across 4 layers. Missing any one step (especially the `SectionContent` `GenericRelation` or the 3-layer re-export chain) causes runtime failures.

**What the skill does:**

Given a content type name (e.g., `LabExercise`), the skill generates:

| File | What gets created |
|------|-------------------|
| `courses/all_models/<domain>_models.py` | New model inheriting `TimestampedModel`, with `GenericRelation` to `SectionContent`, `Meta` with `db_table`, `constraints`, `indexes`, `ordering`, inner enum classes, `clean()` validation |
| `courses/all_models/__init__.py` | Wildcard re-export entry |
| `courses/models.py` | Re-export entry |
| `courses/all_serializers/<domain>_serializers.py` | `FooSerializer` (read) + `FooCreateUpdateSerializer` (write) |
| `courses/all_serializers/learner_serializers.py` | `_LearnerFooSerializer` — explicitly omits sensitive fields |
| `courses/all_serializers/__init__.py` | Re-export entries |
| `courses/serializers.py` | Re-export entries |
| `courses/all_views/<domain>_views.py` | `FooListCreateAPIView` + `FooDetailAPIView` with full boilerplate (permissions, `guard_editable`, try/except, response envelope) |
| `courses/all_views/__init__.py` | Re-export entries |
| `courses/views.py` | Re-export entries |
| `courses/urls.py` | URL pattern entries |
| `courses/services/<domain>_service.py` | `create_foo()`, `update_foo()`, `delete_foo()` with `@transaction.atomic`, `select_for_update` |
| `courses/services/__init__.py` | Re-export entries |
| `SectionContent.ItemType` | New choice added to the enum |
| `courses/admin.py` | Model registration |
| `courses/all_tests/test_<domain>.py` | Test base class + CRUD test methods |

**Conventions the skill enforces:**
- `APIView` only (never generic views or viewsets)
- Standard response envelope (`{'success': True, 'message': ..., 'data': ...}`)
- `guard_editable(course)` before any mutation
- Numeric ID endpoints → 404 on no-access (never 403)
- Serializers handle shape only, business logic in services
- Learner serializer omits sensitive fields by absence, not conditional removal
- `GenericRelation` on the model for cascade delete through `SectionContent`

---

## 2. Scaffold CRUD View

**Trigger phrases:** "new view", "scaffold view", "add endpoint", "create API view"

**Why this matters:** Every view in this project follows an identical pattern — `APIView` subclass, permission stack, `_get_owned_*` helper, standard response envelope, `guard_editable` for mutations, specific try/except structure. Writing this from scratch is tedious and easy to get subtly wrong.

**What the skill does:**

Given a model name and CRUD operations needed, generates a view file in `all_views/` with:

```python
class FooDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]

    def _get_owned_foo(self, request, foo_id):
        try:
            foo = Foo.objects.select_related('section__course').get(
                pk=foo_id, section__course__instructors=request.user
            )
        except Foo.DoesNotExist:
            return None, None
        return foo, foo.section.course

    def get(self, request, foo_id):
        foo, course = self._get_owned_foo(request, foo_id)
        if not foo:
            return Response(
                {'success': False, 'message': 'Foo not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': FooSerializer(foo).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, foo_id):
        foo, course = self._get_owned_foo(request, foo_id)
        if not foo:
            return Response(...)
        guard_editable(course)
        serializer = FooCreateUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            updated = update_foo(foo, serializer.validated_data)
        except Exception as e:
            logger.error(f"Foo update failed: {e}")
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'message': 'Foo updated.', 'data': FooSerializer(updated).data},
            status=status.HTTP_200_OK,
        )
```

Also auto-updates:
- `all_views/__init__.py`
- `views.py`
- `urls.py`

---

## 3. Scaffold Learner Endpoint

**Trigger phrases:** "learner endpoint", "learner view", "consumption endpoint", "student-facing API"

**Why this matters:** Learner endpoints have a distinct pattern from instructor endpoints — different permission classes, access via `resolve_course_access()` or `get_consumption_*()` service helpers, and critically, they must never expose instructor-only fields. Getting the access pattern wrong leaks data or breaks the 403-vs-404 policy.

**What the skill does:**

Given a content type, generates:

| Component | Pattern |
|-----------|---------|
| **View** | `permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser \| IsInstructorUser]`, uses `get_consumption_foo(user, foo_id)`, raises `Foo.DoesNotExist` → 404 |
| **Serializer** | `_LearnerFooSerializer` — plain `serializers.Serializer` (not ModelSerializer), explicitly declares only safe fields, uses `SerializerMethodField` for computed values |
| **Service function** | `get_consumption_foo(user, foo_id)` following the `get_consumption_lecture` / `get_quiz_for_consumption` pattern — fetch + verify access in one call |
| **Submit view** (if applicable) | `IsLearnerUser`-gated POST, 403 for instructors, 404 for unenrolled, atomic service call |

**Security checks the skill enforces:**
- Lists all sensitive fields from the instructor serializer and confirms each is absent from the learner serializer
- Uses 404 (not 403) for numeric ID endpoints
- POST endpoints are `IsLearnerUser` only (instructors get 403 — preview must not pollute data)

---

## 4. Scaffold Celery Task

**Trigger phrases:** "celery task", "background task", "async task", "new task"

**Why this matters:** Celery tasks in this project follow a very specific pattern — `@shared_task` with `bind=True`, `autoretry_for`, `retry_backoff`, `retry_jitter`, `max_retries=3`, terminal status guard, status transitions, and `transaction.on_commit` for side effects.

**What the skill does:**

Given a task name and purpose, generates:

```python
@shared_task(
    bind=True,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def <verb>_<model>_task(self, <model>_id: int):
    """Docstring explaining what the task does."""
    from courses.models import Model

    try:
        instance = Model.objects.select_related(...).get(pk=<model>_id)
    except Model.DoesNotExist:
        logger.error(f"Model {<model>_id} not found, aborting.")
        return

    # Terminal guard
    if instance.status in Model.TERMINAL_STATUSES:
        logger.info(f"Model {<model>_id} already terminal, skipping.")
        return

    instance.status = Model.Status.PROCESSING
    instance.save(update_fields=['status', 'updated_at'])

    try:
        with transaction.atomic():
            # Core logic here
            instance.status = Model.Status.COMPLETED
            instance.save(update_fields=['status', 'updated_at'])

    except Exception as exc:
        logger.error(f"Task failed for Model {<model>_id}: {exc}")
        if self.request.retries >= self.max_retries:
            instance.status = Model.Status.FAILED
            instance.error_message = str(exc)[:500]
            instance.save(update_fields=['status', 'error_message', 'updated_at'])
        raise
```

Also suggests:
- Celery queue routing entry in `settings.py`
- `transaction.on_commit` dispatch pattern from the calling code

---

## 5. Scaffold Service Function

**Trigger phrases:** "service function", "business logic", "new service", "add to services"

**Why this matters:** All business logic lives in `courses/services/` (never in views or serializers). Service functions follow a consistent pattern — `@transaction.atomic`, `select_for_update` for parent locking, ownership fetch, and raising `ValidationError` or custom exceptions.

**What the skill does:**

Given a function name and description, generates:

```python
@transaction.atomic
def create_foo(parent_id: int, user, validated_data: dict) -> Foo:
    """
    Create a Foo under the given parent.

    Locks the parent row to prevent concurrent position conflicts.
    Raises Foo.DoesNotExist if parent not found or not owned by user.
    """
    parent = Parent.objects.select_for_update().get(
        pk=parent_id, course__instructors=user
    )

    foo = Foo.objects.create(
        parent=parent,
        **validated_data,
    )

    # Create SectionContent slot (if this is a curriculum item)
    create_section_content_for_object(parent, foo)

    return foo
```

Also updates:
- `courses/services/__init__.py` re-exports

---

## 6. Scaffold Test Suite

**Trigger phrases:** "write tests", "scaffold tests", "test file", "add tests for"

**Why this matters:** Every test file in `courses/all_tests/` duplicates the same instructor + course + section scaffold. The test patterns are consistent — `APITestCase` subclass, `setUpTestData` with helper methods, `force_authenticate`, and assertions on the standard response envelope.

**What the skill does:**

Given a feature/model name, generates a test file with:

```python
class FooTestBase(APITestCase):
    """Shared setup for Foo-related tests."""

    @classmethod
    def setUpTestData(cls):
        cls.instructor = cls._make_verified_instructor('instructor@test.com')
        cls.other_instructor = cls._make_verified_instructor('other@test.com')
        cls.learner = cls._make_learner('learner@test.com')
        cls.course = NidusCourse.objects.create(
            title='Test Course', slug='test-course', status='draft',
        )
        cls.course.instructors.add(cls.instructor)
        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section 1', position=1,
        )

    @staticmethod
    def _make_verified_instructor(email):
        user = User.objects.create_user(
            email=email, password='testpass123', user_type='instructor',
        )
        user.is_email_verified = True
        user.save()
        IdentityVerification.objects.create(
            instructor=user.instructor_profile, status='approved',
        )
        user.instructor_profile.is_verified = True
        user.instructor_profile.save()
        return user

    @staticmethod
    def _make_learner(email):
        user = User.objects.create_user(
            email=email, password='testpass123', user_type='learner',
        )
        user.is_email_verified = True
        user.save()
        return user

    def auth(self, user):
        self.client.force_authenticate(user=user)


class FooCreateTests(FooTestBase):
    """Test POST /api/v1/courses/.../foo/"""

    def test_create_foo_success(self):
        self.auth(self.instructor)
        response = self.client.post(self.url, data={...}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['success'])

    def test_create_foo_unauthenticated(self):
        response = self.client.post(self.url, data={...}, format='json')
        self.assertEqual(response.status_code, 401)

    def test_create_foo_wrong_instructor(self):
        self.auth(self.other_instructor)
        response = self.client.post(self.url, data={...}, format='json')
        self.assertEqual(response.status_code, 404)  # numeric ID → 404

    def test_create_foo_non_draft_course(self):
        self.course.status = 'published'
        self.course.save()
        self.auth(self.instructor)
        response = self.client.post(self.url, data={...}, format='json')
        self.assertEqual(response.status_code, 403)  # guard_editable
```

**Test categories always generated:**
- Happy path (authenticated owner, valid data)
- Unauthenticated (401)
- Wrong instructor / no access (404 for numeric ID, 403 for slug)
- Non-draft course (guard_editable → 403)
- Invalid data (400 with validation errors)
- Learner access (403 for instructor-only endpoints)

---

## 7. Scaffold Serializer Pair

**Trigger phrases:** "new serializer", "scaffold serializer", "add serializer for"

**Why this matters:** This project always creates serializers in pairs — a read serializer (`FooSerializer`) and a write serializer (`FooCreateUpdateSerializer`). The read serializer may include computed fields, nested data, and method fields. The write serializer handles validation only. Business logic never goes in serializers.

**What the skill does:**

Given a model, generates:

```python
class FooSerializer(serializers.Serializer):
    """Read-only representation of Foo."""
    id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)
    # ... all model fields as read-only
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class FooCreateUpdateSerializer(serializers.Serializer):
    """Validation for creating/updating Foo. No business logic."""
    title = serializers.CharField(max_length=255)
    # ... writable fields only, with validators
```

Also updates:
- `all_serializers/__init__.py`
- `serializers.py`

---

## 8. Scaffold Management Command

**Trigger phrases:** "management command", "django command", "custom command", "data repair"

**Why this matters:** Existing commands (`populate_section_content`, `reindex_section_content_positions`, `seed_course_categories`) follow a consistent pattern with `--dry-run` support, `transaction.atomic`, queryset iteration, and progress reporting via `self.stdout`.

**What the skill does:**

```python
class Command(BaseCommand):
    help = 'Description of what this command does'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without applying them',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be saved'))

        queryset = Model.objects.filter(...).iterator()
        updated = 0

        for obj in queryset:
            # Logic here
            if not dry_run:
                obj.save(update_fields=[...])
            updated += 1

        verb = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(f'{verb} {updated} records'))

        if dry_run:
            transaction.set_rollback(True)
```

---

## 9. Add New Permission Class

**Trigger phrases:** "new permission", "permission class", "add permission"

**Why this matters:** All permission classes must live in `core/permissions.py` (never in individual apps). This is a documented convention in CLAUDE.md that is easy to forget.

**What the skill does:**

Given a permission name and logic description, generates:

```python
# In core/permissions.py

class IsFooAllowed(permissions.BasePermission):
    """
    Description of what this permission checks.
    """
    message = 'You do not have permission to perform this action.'

    def has_permission(self, request, view):
        # Request-level check
        return ...

    def has_object_permission(self, request, view, obj):
        # Object-level check
        return ...
```

**Enforces:**
- File location: always `core/permissions.py`
- Never in app-specific files
- Custom `message` attribute for clear error responses

---

## 10. Scaffold New App

**Trigger phrases:** "new app", "create app", "add django app"

**Why this matters:** Every app in this project follows the `all_views/`, `all_serializers/`, `all_models/` directory convention with thin re-export files. A plain `django-admin startapp` gives you a flat structure that doesn't match the project pattern.

**What the skill does:**

Given an app name, creates the full directory structure:

```
<app_name>/
├── all_models/
│   └── __init__.py
├── all_serializers/
│   └── __init__.py
├── all_views/
│   └── __init__.py
├── all_tests/
│   └── __init__.py
├── services/
│   └── __init__.py
├── models.py          (thin re-export from all_models/)
├── serializers.py     (thin re-export from all_serializers/)
├── views.py           (thin re-export from all_views/)
├── urls.py            (with app_name set)
├── apps.py
├── admin.py
├── signals.py
└── __init__.py
```

Also updates:
- `settings.py` → `INSTALLED_APPS`
- Root `urls.py` → new path include

---

## 11. Scaffold State Machine

**Trigger phrases:** "state machine", "status transitions", "workflow states", "add status flow"

**Why this matters:** The project has two existing state machines — `NidusCourse.transition_to()` for course status and `IdentityVerification` for instructor verification. Both follow the pattern of a `transition_to()` method on the model with explicit valid-transition maps and `ValidationError` on invalid transitions.

**What the skill does:**

Given states and transitions, generates:

```python
class Foo(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ACTIVE = 'active', 'Active'
        ARCHIVED = 'archived', 'Archived'

    VALID_TRANSITIONS = {
        Status.DRAFT: [Status.ACTIVE],
        Status.ACTIVE: [Status.ARCHIVED],
        Status.ARCHIVED: [Status.DRAFT],
    }

    TERMINAL_STATUSES = {Status.ARCHIVED}

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    def transition_to(self, new_status, **kwargs):
        """
        Transition to a new status. Raises ValidationError if the
        transition is not allowed.
        """
        allowed = self.VALID_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise ValidationError(
                f"Cannot transition from '{self.status}' to '{new_status}'."
            )
        self.status = new_status
        self.save(update_fields=['status', 'updated_at'])
```

Also generates:
- Corresponding view actions (e.g., `submit/`, `approve/`, `reject/`) following the course status views pattern
- `ValidationError` handling in views (message_dict → 400, plain string → 422)

---

## 12. Learner Security Audit

**Trigger phrases:** "security audit", "learner field audit", "check for leaks", "sensitive field check"

**Why this matters:** The CLAUDE.md documents a specific list of fields that must remain instructor-only. Every time a new field is added to an instructor serializer, the developer must verify it doesn't appear in any learner-facing response. This is the most security-critical convention in the project.

**What the skill does:**

Scans all serializers and reports:

1. **Instructor serializers** — lists every field declared
2. **Learner serializers** — lists every field declared
3. **Delta** — fields present in instructor serializers but absent from learner serializers (expected)
4. **Warnings** — fields present in learner serializers that match known sensitive field names (`is_correct`, `solution_code`, `model_answer`, `rubric`, `is_hidden`)
5. **Missing learner serializers** — instructor serializers with no corresponding learner counterpart

Output: a table showing the audit results and any violations.

---

## 13. API Endpoint Documentation Generator

**Trigger phrases:** "document endpoints", "API docs", "endpoint list", "generate API reference"

**Why this matters:** The project has no automated API documentation (no Swagger/OpenAPI). As the number of endpoints grows, a manually maintained reference keeps the frontend team aligned.

**What the skill does:**

Scans `urls.py` across all apps and generates a markdown document with:

| Method | URL | View | Permissions | Request Body | Response Shape |
|--------|-----|------|-------------|--------------|----------------|
| GET | `/api/v1/courses/catalog/` | `CatalogListView` | `AllowAny` | — | Paginated list |
| POST | `/api/v1/courses/<pk>/sections/` | `SectionListCreateView` | `IsVerifiedInstructor` | `{title, position}` | `{success, data}` |

Grouped by app and audience (public, learner, instructor, admin).

---

## 14. Scaffold Signal Handler

**Trigger phrases:** "new signal", "post_save signal", "add signal handler"

**Why this matters:** The project uses signals in two apps — `authentication/signals.py` (auto-create profiles on user creation) and `courses/signals.py` (recalculate progress on `WatchProgress` save). Both follow Django's `@receiver` pattern with `post_save` and are registered in `apps.py` → `ready()`.

**What the skill does:**

Given a model and trigger condition, generates:

```python
# In <app>/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=Foo)
def handle_foo_saved(sender, instance, created, **kwargs):
    """
    Triggered after Foo is saved.
    """
    if created:
        # Handle creation
        pass
```

Also verifies:
- `apps.py` has `def ready(self): import <app>.signals`
- Signal doesn't duplicate existing handlers

---

## 15. Migration Safety Check

**Trigger phrases:** "migration check", "check migration", "migration safety", "safe to migrate"

**Why this matters:** This project uses PostgreSQL and may have production data. Certain migration operations (adding a NOT NULL column without a default, renaming a column, removing a field) can cause downtime or data loss.

**What the skill does:**

Given a pending migration file, analyzes it for:

| Check | Risk | Recommendation |
|-------|------|----------------|
| `AddField` with `null=False` and no `default` | **High** — fails on existing rows | Add with `null=True` first, backfill, then alter |
| `RemoveField` | **Medium** — data loss | Verify the field is no longer read anywhere |
| `RenameField` | **Medium** — breaks queries referencing old name | Use `db_column` to keep DB name stable |
| `AlterField` changing type | **High** — may require full table rewrite | Check if the cast is implicit in Postgres |
| `RunSQL` with no `reverse_sql` | **Low** — irreversible migration | Add reverse SQL or document why |
| `DeleteModel` | **High** — data loss | Confirm no ForeignKey references remain |

Output: a safety report with risk level and recommended approach for each operation.

---

## Priority Ranking

Based on how often each workflow occurs and how much boilerplate it eliminates:

| Priority | Skill | Pain Saved | Frequency |
|----------|-------|------------|-----------|
| 1 | Scaffold New Content Type | Very high (10+ files) | Low but critical |
| 2 | Scaffold CRUD View | High (4 files per view) | High |
| 3 | Scaffold Test Suite | High (tedious setup) | High |
| 4 | Scaffold Learner Endpoint | High (security-critical) | Medium |
| 5 | Learner Security Audit | Medium (prevents data leaks) | On every PR |
| 6 | Scaffold Service Function | Medium (3 files) | High |
| 7 | Scaffold Serializer Pair | Medium (3 files) | High |
| 8 | Scaffold Celery Task | Medium (specific pattern) | Low |
| 9 | Migration Safety Check | Medium (prevents incidents) | On every migration |
| 10 | API Endpoint Documentation | Medium (alignment tool) | Periodic |
| 11 | Scaffold Management Command | Low (simple pattern) | Low |
| 12 | Add New Permission Class | Low (single file) | Low |
| 13 | Scaffold State Machine | Low (rare addition) | Very low |
| 14 | Scaffold Signal Handler | Low (single file) | Low |
| 15 | Scaffold New App | Low (one-time per app) | Very low |

---

## How to Create These Skills

Each skill would be a directory under your Claude Code skills path containing a `SKILL.md` file. For example:

```
skills/
├── scaffold-content-type/
│   └── SKILL.md
├── scaffold-crud-view/
│   └── SKILL.md
├── scaffold-learner-endpoint/
│   └── SKILL.md
├── scaffold-test-suite/
│   └── SKILL.md
└── learner-security-audit/
    └── SKILL.md
```

Each `SKILL.md` contains the full prompt with:
- The project conventions (from CLAUDE.md)
- The exact boilerplate patterns to follow
- Which files to create/modify
- The re-export chain to update
- Validation checks to run after generation

To create any of these skills, use the `skill-creator` skill with a description of the desired workflow.

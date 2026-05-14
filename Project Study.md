# Django & DRF Deep Dive — Interview Study Note
### Based on: Career College Backend (Course Marketplace Platform)

> This note walks through every major Django and Django REST Framework concept applied in this project.  
> Read it before a backend/Django/DRF interview to get both theory and real implementation examples.

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Django Models](#2-django-models)
3. [Custom User Model](#3-custom-user-model)
4. [Model Relationships](#4-model-relationships)
5. [Model Meta Class](#5-model-meta-class)
6. [Model Validation — clean() and save()](#6-model-validation--clean-and-save)
7. [Abstract Models](#7-abstract-models)
8. [TextChoices and Status Fields](#8-textchoices-and-status-fields)
9. [State Machine Pattern on Models](#9-state-machine-pattern-on-models)
10. [Soft Delete Pattern](#10-soft-delete-pattern)
11. [Custom Manager](#11-custom-manager)
12. [Django Signals](#12-django-signals)
13. [ContentTypes Framework and GenericForeignKey](#13-contenttypes-framework-and-genericforeignkey)
14. [Django REST Framework — APIView](#14-django-rest-framework--apiview)
15. [Serializers](#15-serializers)
16. [Context Passing in Serializers](#16-context-passing-in-serializers)
17. [Permission Classes](#17-permission-classes)
18. [Pagination](#18-pagination)
19. [Parser Classes](#19-parser-classes)
20. [Response Envelope Pattern](#20-response-envelope-pattern)
21. [The N+1 Problem](#21-the-n1-problem)
22. [Django ORM Optimization](#22-django-orm-optimization)
23. [Database Transactions — transaction.atomic](#23-database-transactions--transactionatomic)
24. [Row-level Locking — select_for_update](#24-row-level-locking--select_for_update)
25. [Database Indexes and Constraints](#25-database-indexes-and-constraints)
26. [Django Celery — Async Task Queue](#26-django-celery--async-task-queue)
27. [JWT Authentication — SimpleJWT](#27-jwt-authentication--simplejwt)
28. [OAuth2 — Google Authorization Code Flow](#28-oauth2--google-authorization-code-flow)
29. [Service Layer Pattern](#29-service-layer-pattern)
30. [Selector Pattern](#30-selector-pattern)
31. [File Uploads and Media Handling](#31-file-uploads-and-media-handling)
32. [Django Middleware](#32-django-middleware)
33. [Django Management Commands](#33-django-management-commands)
34. [Error Handling Patterns](#34-error-handling-patterns)
35. [Settings and Environment Variables](#35-settings-and-environment-variables)
36. [Migrations](#36-migrations)
37. [Slug Fields and Auto-generation](#37-slug-fields-and-auto-generation)
38. [Denormalization Pattern](#38-denormalization-pattern)
39. [Video Transcoding Pipeline](#39-video-transcoding-pipeline)
40. [Quick-fire Interview Q&A Cheatsheet](#40-quick-fire-interview-qa-cheatsheet)

---

## 1. Project Overview

**What it is:** A Coursera-like course marketplace backend built with Django 5.2 + Django REST Framework.

**Core actors:**
- `learner` — browses and enrolls in published courses
- `instructor` — creates, authors, and publishes courses (must pass identity verification first)
- `partner_institution` — an organizational entity that can co-brand courses
- `admin` — reviews, approves/rejects courses and verifications

**Major features:**
- Email-based auth with OTP verification
- JWT tokens (access + refresh, rotation, blacklist)
- Google OAuth2 (authorization-code flow)
- Identity verification state machine (draft → submitted → under_review → approved/rejected)
- Course authoring: sections, lectures (video/article), quizzes, coding exercises, assignments
- Course status state machine (draft → under_review → published/rejected → archived)
- Async video transcoding to HLS via FFmpeg + Celery
- Learner enrollment with soft unenroll

---

## 2. Django Models

### What are Models?
A Django model is a Python class that maps directly to a database table. Each class attribute becomes a table column. Django's ORM (Object-Relational Mapper) handles all SQL generation — you never write `CREATE TABLE` or `INSERT INTO` manually.

```python
class CourseCategory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

### Why are they used?
- **Single source of truth** for your data schema — change the Python class, run `makemigrations`, and the DB follows.
- Provide a rich query API: `CourseCategory.objects.filter(is_active=True).order_by('name')`
- Enable model-level validation via `clean()`, `save()`, and field validators.

### Common Field Types used in this project

| Field | Purpose |
|-------|---------|
| `CharField(max_length=N)` | Short text. Maps to `VARCHAR(N)` |
| `TextField()` | Unbounded text. Maps to `TEXT` |
| `BooleanField()` | True/False. Maps to `BOOLEAN` |
| `IntegerField()` / `PositiveIntegerField()` | Integer columns |
| `DecimalField(max_digits, decimal_places)` | Money/prices. Maps to `NUMERIC` |
| `DateTimeField(auto_now_add=True)` | Timestamp set once on creation |
| `DateTimeField(auto_now=True)` | Timestamp updated on every save |
| `SlugField()` | URL-safe string (letters, numbers, hyphens) |
| `ImageField(upload_to=...)` | File path for images |
| `JSONField()` | Stores JSON natively (PostgreSQL `jsonb`) |
| `EmailField()` | Validated email string |
| `ForeignKey()` | Many-to-one relationship |
| `ManyToManyField()` | Many-to-many relationship |

**`auto_now_add` vs `auto_now`:**
- `auto_now_add=True` → set ONCE when the row is created, never changed again
- `auto_now=True` → updated to the current timestamp on EVERY `.save()` call

---

## 3. Custom User Model

### What is it?
Django ships with a built-in `User` model. For most real projects you replace it with your own model that extends `AbstractUser` or `AbstractBaseUser`. This project extends `AbstractUser`.

```python
class User(AbstractUser):
    username = None                          # removes the default username field
    email = models.EmailField(unique=True)   # email becomes the login identifier
    full_name = models.CharField(max_length=255)
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES)
    is_email_verified = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)   # soft-delete flag

    USERNAME_FIELD = 'email'   # tells Django to use email for authentication
    REQUIRED_FIELDS = []       # fields prompted in createsuperuser (besides USERNAME_FIELD)
```

### Why extend AbstractUser?
`AbstractUser` gives you: `password`, `is_staff`, `is_active`, `is_superuser`, `last_login`, and all the permission machinery. You keep what you need and add your own fields.

### Registering the custom user model
In `settings.py`:
```python
AUTH_USER_MODEL = 'authentication.User'
```
This must be set **before the first migration**. Changing it after migrations exist is extremely painful.

### Why remove `username`?
This app identifies users by email, not username. Setting `username = None` and `USERNAME_FIELD = 'email'` removes the username requirement entirely.

### OTP fields on the User model
```python
otp_code = models.CharField(max_length=6, blank=True, null=True)
otp_created_at = models.DateTimeField(blank=True, null=True)
otp_purpose = models.CharField(max_length=20, choices=OTP_PURPOSE_CHOICES)
otp_verified = models.BooleanField(default=False)
```
The OTP is stored on the User row itself (rather than a separate table) to keep the verification flow simple. `otp_purpose` distinguishes registration OTPs from password-reset OTPs.

---

## 4. Model Relationships

### ForeignKey (Many-to-One)
Many rows in the child table point to ONE row in the parent table.

```python
# Many lectures belong to ONE section
section = models.ForeignKey(
    CourseSection,
    on_delete=models.CASCADE,
    related_name='lectures',
)
```

**`on_delete` options:**
- `CASCADE` — delete child rows when the parent is deleted *(most common)*
- `SET_NULL` — set the FK to NULL when parent is deleted (`null=True` required)
- `PROTECT` — raise an error if you try to delete a parent that has children
- `SET_DEFAULT` — set FK to the field's default value
- `DO_NOTHING` — do nothing (dangerous — can break referential integrity)

**`related_name`** — the name used to do a reverse lookup:
```python
section.lectures.all()   # because related_name='lectures'
```

### ManyToManyField
Both sides can have many records on the other side. Django creates a hidden junction table automatically.

```python
# One course can have many instructors; one instructor can teach many courses
instructors = models.ManyToManyField(
    settings.AUTH_USER_MODEL,
    related_name='instructed_nidus_courses',
    blank=True,
)
```

Query it with:
```python
course.instructors.all()
user.instructed_nidus_courses.all()
```

### Using `settings.AUTH_USER_MODEL` instead of `User`
Always reference the user model via `settings.AUTH_USER_MODEL` in FKs (or `get_user_model()` at runtime). This avoids circular import issues and respects the custom user model setting.

### `related_name` clashes
When the same model appears in FKs from multiple apps, you must set unique `related_name` values to avoid clashes with Django's auto-generated reverse accessors. This project sets explicit names like `'authentication_user_set'` for the M2M group/permission overrides.

---

## 5. Model Meta Class

The inner `Meta` class configures database-level behaviour for the model.

```python
class Meta:
    db_table = 'nidus_courses'           # custom table name (default would be 'courses_nidusCourse')
    verbose_name = 'Nidus Course'        # singular human-readable name (used in admin)
    verbose_name_plural = 'Nidus Courses'
    ordering = ['-created_at']           # default queryset ordering
    indexes = [
        models.Index(fields=['status', '-created_at'], name='idx_ncourse_status_date'),
    ]
    constraints = [
        models.UniqueConstraint(
            fields=['user', 'course'],
            name='uniq_enrollment_user_course',
        ),
        models.CheckConstraint(
            check=models.Q(progress_percent__lte=100),
            name='chk_enrollment_progress_percent_lte_100',
        ),
    ]
```

**`abstract = True`** — the model is not created as a table; it only serves as a base class (see [Section 7](#7-abstract-models)).

**`ordering`** — default sort for every `.all()` / `.filter()` call unless you call `.order_by()` explicitly.

---

## 6. Model Validation — clean() and save()

### `clean()`
Called by Django's form validation and by `.full_clean()`. Use it for cross-field or business-rule validation.

```python
def clean(self):
    super().clean()
    if self.created_by and self.created_by.user_type != 'instructor':
        raise ValidationError({'created_by': 'Only instructors can create courses.'})
```

The dict form `{'field_name': 'message'}` produces field-specific errors. A plain string produces a non-field error.

**Important:** `clean()` is NOT called automatically by `.save()` in Django. You must call `.full_clean()` explicitly, or rely on serializer validation (DRF serializers call `.validate()` themselves).

### `save()`
Override to add pre-save logic:

```python
def save(self, *args, **kwargs):
    if not self.slug:                        # auto-generate slug on first save
        self.slug = slugify(self.title)
    self.is_published = (self.status == 'published')  # keep denormalized flag in sync
    super().save(*args, **kwargs)            # always call super() at the end
```

**`update_fields`** — a performance optimization. Pass a list of field names to only update those columns:
```python
video_asset.save(update_fields=['status', 'updated_at'])
# generates: UPDATE video_assets SET status=?, updated_at=? WHERE id=?
# instead of updating all columns
```

---

## 7. Abstract Models

An abstract model is a base class that provides reusable fields to child models but is **never created as a database table itself**.

```python
class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True   # <-- key line
```

Any model that inherits from `TimestampedModel` automatically gets `created_at` and `updated_at` columns without repeating the field definitions. In this project, `NidusCourse`, `Lecture`, `CourseSection`, `CodingExercise`, `Enrollment`, and many more all extend `TimestampedModel`.

---

## 8. TextChoices and Status Fields

`models.TextChoices` is an enum that generates human-readable `(value, label)` pairs for a `CharField`.

```python
class CourseStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    UNDER_REVIEW = 'under_review', 'Under Review'
    PUBLISHED = 'published', 'Published'
    REJECTED = 'rejected', 'Rejected'
    ARCHIVED = 'archived', 'Archived'

status = models.CharField(
    max_length=20,
    choices=CourseStatus.choices,
    default=CourseStatus.DRAFT,
)
```

Access in code: `NidusCourse.CourseStatus.PUBLISHED` → `'published'`  
Access labels: `course.get_status_display()` → `'Published'`

Nesting it inside the model (`class CourseStatus`) keeps the definition co-located with the model that owns it and avoids polluting the module namespace.

---

## 9. State Machine Pattern on Models

A **state machine** limits which transitions are legal between status values and enforces business rules at each transition.

```python
VALID_TRANSITIONS = {
    'draft':        ('under_review',),
    'under_review': ('published', 'rejected'),
    'rejected':     ('draft',),
    'published':    ('archived',),
    'archived':     ('draft',),
}

def transition_to(self, new_status, reviewer=None, rejection_reason=''):
    allowed = self.VALID_TRANSITIONS.get(self.status, ())
    if new_status not in allowed:
        raise ValidationError(f'Cannot transition from "{self.status}" to "{new_status}".')

    if new_status == 'under_review':
        self._validate_course_completeness()   # run extra checks before submission

    if new_status == 'rejected' and not rejection_reason.strip():
        raise ValidationError({'rejection_reason': 'A reason is required when rejecting.'})

    self.status = new_status
    self.save()
```

**Why a state machine?**
- Makes illegal transitions impossible.
- Centralises all business logic in ONE method. Views just call `course.transition_to(...)`.
- Easy to test: each valid and invalid transition is an individual test case.

**Rule:** Never set `course.status = 'published'` directly anywhere outside `transition_to()`. This would bypass all the guards.

---

## 10. Soft Delete Pattern

Instead of permanently deleting a row (`DELETE FROM ...`), a soft-delete marks it as deleted using a boolean flag.

```python
is_deleted = models.BooleanField(default=False)
deleted_at = models.DateTimeField(null=True, blank=True)
```

**Why soft delete?**
- Audit trail — you can see that the record existed and when it was deleted.
- Recovery — you can un-delete without restoring from backups.
- Referential integrity — foreign keys pointing to the record do not break.

**Custom Manager** (see next section) hides soft-deleted rows from normal queries:
```python
def get_queryset(self):
    return super().get_queryset().filter(is_deleted=False)
```

---

## 11. Custom Manager

A Manager is the interface through which database query operations are provided to Django models. The default manager is `objects`. You can override it or add new ones.

```python
class CustomUserManager(BaseUserManager):
    def get_queryset(self):
        # Default queryset silently excludes soft-deleted users
        return super().get_queryset().filter(is_deleted=False)

    def all_with_deleted(self):
        # Explicit escape hatch when you need everything
        return super().get_queryset()

    def deleted_only(self):
        return super().get_queryset().filter(is_deleted=True)

    def create_user(self, email, password=None, **extra_fields):
        # Custom logic for user creation
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
```

Attach it to the model:
```python
objects = CustomUserManager()
```

Now `User.objects.all()` automatically excludes soft-deleted users. To bypass, call `User.objects.all_with_deleted()`.

---

## 12. Django Signals

Signals are Django's implementation of the Observer pattern. They allow decoupled components to get notified when specific things happen in other parts of the application.

### Common built-in signals
- `post_save` — fired after a model's `.save()` completes
- `pre_save` — fired before a model's `.save()` runs
- `post_delete` — fired after a model is deleted
- `m2m_changed` — fired when a ManyToMany field is changed

### How signals work in this project

When a new `User` is saved, a signal automatically creates the correct profile:

```python
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if not created:   # only run on new rows, not updates
        return

    if instance.user_type == 'learner':
        LearnerProfile.objects.get_or_create(user=instance)
    elif instance.user_type == 'instructor':
        InstructorProfile.objects.get_or_create(user=instance)
    elif instance.user_type == 'partner_institution':
        PartnerInstitutionProfile.objects.get_or_create(
            user=instance,
            defaults={'institution_name': instance.full_name},
        )
```

**Key signal handler arguments:**
- `sender` — the model class that sent the signal
- `instance` — the actual model instance
- `created` — `True` if a new row was inserted, `False` if an existing row was updated
- `**kwargs` — absorbs any extra arguments Django may pass

### Registering signals
Either place the signal in `signals.py` and import it in the app's `AppConfig.ready()`:
```python
class AuthenticationConfig(AppConfig):
    def ready(self):
        import authentication.signals   # importing registers the receiver
```

### Signals: when to use vs. when to avoid
**Use them:** for truly decoupled side effects (e.g. creating profiles, sending notifications, audit logging).  
**Avoid them:** for core business logic — signals are hard to trace and test. If you need the profile creation to be transactional with the user creation, use a service function instead.

---

## 13. ContentTypes Framework and GenericForeignKey

### What is the ContentTypes Framework?
Django's `contenttypes` app maintains a registry of every installed model. Each model class gets a row in the `django_content_type` table, identified by `(app_label, model)`.

### GenericForeignKey (GFK)
A GFK is a special field that can point to a row in ANY model. It uses two real database columns:
- `content_type` (FK to `django_content_type`) — identifies the target model
- `object_id` (integer) — the primary key of the target row

```python
class SectionContent(models.Model):
    section = models.ForeignKey(CourseSection, on_delete=models.CASCADE, related_name='contents')

    # Denormalized type tag for fast filtering
    item_type = models.CharField(max_length=20, choices=ItemType.choices, db_index=True)

    # Standard GFK trio
    content_type = models.ForeignKey(DjContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')  # virtual field

    position = models.PositiveIntegerField(default=1, db_index=True)
```

**Why GFK here?**  
A `SectionContent` slot can hold a `Lecture`, `Quiz`, `CodingExercise`, or any future content type without adding a new FK column every time a new type is added. The `SectionContent` model owns the curriculum ordering for all content types.

### GenericRelation (reverse of GFK)
The content model (e.g. `Lecture`) declares a `GenericRelation` so that deleting the lecture automatically cascade-deletes its `SectionContent` slot:

```python
class Lecture(TimestampedModel):
    section_content = GenericRelation(
        SectionContent,
        content_type_field='content_type',
        object_id_field='object_id',
        related_query_name='lecture',
    )
```

Without `GenericRelation`, deleting a `Lecture` would leave orphaned `SectionContent` rows. The `related_query_name` allows reverse filtering:
```python
SectionContent.objects.filter(lecture__section=section)
```

### ContentType lookup
```python
from django.contrib.contenttypes.models import ContentType as DjContentType
ct = DjContentType.objects.get_for_model(Lecture)
# Returns the ContentType row for the Lecture model
```

---

## 14. Django REST Framework — APIView

### What is APIView?
`APIView` is the base class for all class-based views in DRF. It wraps Django's `View` class and adds:
- Content negotiation (JSON, XML, etc.)
- Authentication handling
- Permission checking
- Throttling
- Exception handling and error responses
- Proper `OPTIONS` support for CORS

### How to define an APIView
Define a class that inherits from `APIView` and implement HTTP method handlers as instance methods:

```python
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

class CourseCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]

    def post(self, request):
        # request.data  — parsed request body (dict)
        # request.user  — authenticated user (set by authentication class)
        # request.query_params  — GET query parameters
        serializer = NidusCourseCreateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)
        course = serializer.save()
        return Response({'success': True, 'data': NidusCourseSerializer(course).data}, status=201)
```

### HTTP method handlers
| Method | Handler | Use case |
|--------|---------|----------|
| GET | `def get(self, request, ...)` | Retrieve data |
| POST | `def post(self, request, ...)` | Create a resource |
| PUT | `def put(self, request, ...)` | Full update (replace) |
| PATCH | `def patch(self, request, ...)` | Partial update |
| DELETE | `def delete(self, request, ...)` | Delete a resource |

If you define `get` but not `post`, a `POST /endpoint/` will return `405 Method Not Allowed` automatically.

### `request` in DRF vs. Django
DRF wraps Django's `HttpRequest` in its own `Request` object:
- `request.data` — parsed body (supports JSON, form data, multipart). Use this instead of `request.POST`.
- `request.query_params` — same as `request.GET` (aliased for clarity)
- `request.user` — authenticated user, set by the authentication class

### Why this project uses only `APIView` (no ViewSets or generic views)
The project rule is explicit: use `APIView` with manual method definitions. This gives total control over the response format, permission composition, and logic flow — no "magic" that could break the standard response envelope.

---

## 15. Serializers

### What is a Serializer?
A serializer converts complex Python objects (model instances) to JSON-compatible Python primitives (dicts, lists) — this is called **serialization**.  
It also validates and converts incoming data (JSON → validated Python objects) — this is called **deserialization**.

### ModelSerializer
`ModelSerializer` auto-generates fields from the model definition. It saves you from writing boilerplate field declarations.

```python
class NidusCourseSerializer(serializers.ModelSerializer):
    # Override automatic fields with nested serializers
    created_by = InstructorBriefSerializer(read_only=True)
    instructors = InstructorBriefSerializer(read_only=True, many=True)
    category = CourseCategoryBriefSerializer(read_only=True)

    class Meta:
        model = NidusCourse
        fields = ['id', 'title', 'slug', 'description', 'status', 'created_by', ...]
        read_only_fields = fields   # this serializer is read-only (output only)
```

### Nested Serializers
Embed one serializer inside another to represent related objects:

```python
class InstructorBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'full_name', 'email']

class NidusCourseSerializer(serializers.ModelSerializer):
    created_by = InstructorBriefSerializer(read_only=True)   # nested
```

Without this, `created_by` would return just the raw integer FK value.

### read_only vs write_only fields
- `read_only=True` — field appears in output (serialization) but is ignored on input
- `write_only=True` — field accepted on input but excluded from output (used for `password` fields)

```python
password = serializers.CharField(write_only=True, min_length=8)
```

### Field-level validation — `validate_<fieldname>`
```python
def validate_title(self, value):
    title = value.strip()
    if len(title) < 5:
        raise serializers.ValidationError('Title must be at least 5 characters long.')
    return title   # always return the (possibly modified) value
```

### Object-level validation — `validate(self, attrs)`
Called after all individual field validators. Use it for cross-field validation:
```python
def validate(self, attrs):
    if attrs.get('end_date') < attrs.get('start_date'):
        raise serializers.ValidationError('end_date must be after start_date.')
    return attrs
```

### `create()` and `update()` in Serializers
When `.save()` is called:
- If no instance was passed → calls `create(validated_data)`
- If an instance was passed → calls `update(instance, validated_data)`

This project uses `create()` to handle M2M relations and nested objects within a transaction:

```python
def create(self, validated_data):
    with transaction.atomic():
        learning_objectives = validated_data.pop('learning_objectives', [])
        # ... pop other nested data
        course = NidusCourse.objects.create(**validated_data)
        course.instructors.add(request.user)
        self._replace_items(CourseLearningObjective, course, learning_objectives)
        return course
```

### PrimaryKeyRelatedField
Accepts a PK integer on input and returns the related object:
```python
instructors = serializers.PrimaryKeyRelatedField(
    many=True,
    queryset=User.objects.filter(user_type='instructor', is_deleted=False),
    required=False,
)
```
On input: `{"instructors": [1, 2, 3]}` → resolved to `[User(1), User(2), User(3)]`

### Serializer vs. plain dict validation
Use serializers for any endpoint that takes user input. Serializers give you:
- Declarative field types and constraints
- Automatic error messages with field names
- A standardized `errors` dict you can return directly in the response

---

## 16. Context Passing in Serializers

Sometimes a serializer needs access to the current request (e.g. to know the logged-in user) but the request isn't part of the model data being validated.

Pass a `context` dict when instantiating the serializer:
```python
# In the view:
serializer = NidusCourseCreateUpdateSerializer(
    data=request.data,
    context={'request': request},   # pass request as context
)
```

Access it inside the serializer:
```python
def create(self, validated_data):
    request = self.context['request']
    course = NidusCourse.objects.create(created_by=request.user, **validated_data)
    course.instructors.add(request.user)   # auto-add the creator as instructor
    return course
```

**When is context needed?**
- Adding the current user as the creator/owner
- Generating absolute URLs (`request.build_absolute_uri(...)`)
- Conditional field inclusion based on user role (e.g., hide `solution_code` from learners)

---

## 17. Permission Classes

### What are Permission Classes?
DRF permission classes decide whether a request should be allowed or denied. They run AFTER authentication (identifying the user) but BEFORE the view handler.

### Creating a custom permission
```python
from rest_framework.permissions import BasePermission

class IsVerifiedInstructor(BasePermission):
    message = 'Only verified instructors can perform this action.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or user.user_type != 'instructor':
            return False
        # DB check: does the instructor have an approved verification?
        return InstructorProfile.objects.filter(user_id=user.id, is_verified=True).exists()
```

### `has_permission` vs `has_object_permission`
- `has_permission(request, view)` — runs for EVERY request to the view. Use for broad access control (is the user an instructor? is email verified?).
- `has_object_permission(request, view, obj)` — runs only when you call `self.check_object_permissions(request, obj)` explicitly (or when using generic views). Use for ownership checks.

```python
class IsCourseInstructor(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.instructors.filter(pk=request.user.pk).exists()
```

### Stacking permissions
```python
permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]
```
All must return `True` for the request to proceed. They are evaluated in order; the first failure short-circuits the rest.

### Built-in permission classes
- `AllowAny` — no restriction (public endpoints like catalog, login)
- `IsAuthenticated` — user must be logged in
- `IsAdminUser` — user must have `is_staff=True`
- `IsAuthenticatedOrReadOnly` — authenticated for writes, anyone for reads

### Where permissions live in this project
**All permission classes must be in `core/permissions.py`** — never inside individual apps. This prevents duplication and ensures a single definition when the same permission guards multiple apps.

---

## 18. Pagination

### What is Pagination?
Instead of returning thousands of records in one response, pagination splits results into pages and returns them one page at a time.

### DRF's PageNumberPagination

```python
# core/pagination.py
from rest_framework.pagination import PageNumberPagination

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10                        # default records per page
    page_size_query_param = 'page_size'   # allow client to override: ?page_size=25
    max_page_size = 100                   # maximum allowed page size
```

### Using pagination in a view
```python
def get(self, request):
    queryset = get_instructor_courses(request.user)
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(queryset, request)   # slices the queryset
    serializer = NidusCourseSerializer(page, many=True)     # serializes the page
    paginated_response = paginator.get_paginated_response(serializer.data)  # builds response
    paginated_response.data = {'success': True, 'data': paginated_response.data}
    return paginated_response
```

### Response shape
```json
{
  "success": true,
  "data": {
    "count": 42,
    "next": "http://localhost:8000/api/v1/courses/?page=3",
    "previous": "http://localhost:8000/api/v1/courses/?page=1",
    "results": [...]
  }
}
```

**`count`** — total number of records (total, not just this page)  
**`next`** / **`previous`** — full URLs for the adjacent pages (null if at boundary)

---

## 19. Parser Classes

Parsers tell DRF how to interpret the request body. By default, DRF includes `JSONParser` and `FormParser`.

```python
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser

class ContentUploadView(APIView):
    parser_classes = [JSONParser, FormParser, MultiPartParser]
```

- `JSONParser` — parses `Content-Type: application/json`
- `FormParser` — parses `Content-Type: application/x-www-form-urlencoded`
- `MultiPartParser` — parses `Content-Type: multipart/form-data` (required for file uploads)

This project adds `MultiPartParser` to views that accept file uploads (thumbnails, video uploads, identity documents).

---

## 20. Response Envelope Pattern

All responses in this project follow a consistent JSON envelope. This makes the frontend's life easier — it always knows exactly what keys to expect.

```json
// Success
{"success": true, "message": "Course created.", "data": {...}}

// Validation error
{"success": false, "message": "Validation failed.", "errors": {"title": ["Too short."]}}

// Not found
{"success": false, "message": "Course not found."}

// Business rule violation
{"success": false, "message": "Course is already published."}

// Server error
{"success": false, "message": "An unexpected error occurred. Please try again."}
```

**Why this matters for interviews:**  
Consistent response envelopes are a sign of mature API design. Without one, different endpoints return different shapes, which breaks frontend clients and makes error handling inconsistent.

---

## 21. The N+1 Problem

### What is the N+1 problem?
It occurs when you load N objects and then, for each object, issue an additional SQL query to load a related object. Result: 1 query to get the list, then N queries for the related data = N+1 total.

### Classic example
```python
# BAD: N+1
courses = NidusCourse.objects.all()   # 1 query
for course in courses:
    print(course.created_by.full_name)   # 1 query PER course → N+1 total
```

If you have 100 courses, this generates 101 queries instead of 2.

### How Django generates N+1
Django's ORM is **lazy** — related objects are loaded on first access. When you do `course.created_by`, Django runs `SELECT * FROM users WHERE id = ?`. This happens inside the loop, generating one query per iteration.

### Common N+1 scenarios in this project
1. Listing courses and accessing `course.created_by.full_name` in the serializer
2. Listing courses and iterating over `course.instructors.all()` in the serializer
3. Loading sections and then accessing `section.course.title` for each section
4. Rendering SectionContent and accessing `content.content_object` for each item

### Solution: `select_related` and `prefetch_related`

#### `select_related` — for ForeignKey / OneToOneField (SQL JOIN)
Fetches the related object in the **same query** using a SQL JOIN. Use for single-valued relationships.

```python
# GOOD: 1 query (with JOIN)
courses = NidusCourse.objects.select_related('created_by', 'category').all()
for course in courses:
    print(course.created_by.full_name)   # no extra query — already loaded
```

#### `prefetch_related` — for ManyToMany / reverse FK (separate optimized query)
Fetches all related objects in a **separate query** and stores them in memory. Use for multi-valued relationships.

```python
# GOOD: 2 queries total
courses = NidusCourse.objects.prefetch_related('instructors', 'partner_institutions').all()
for course in courses:
    for instructor in course.instructors.all():   # no extra query — already cached
        print(instructor.full_name)
```

#### Real example from this project (selectors.py)
```python
def get_course_base_queryset():
    return NidusCourse.objects.select_related('created_by', 'category').prefetch_related(
        'instructors',
        'partner_institutions',
        'learning_objectives',
        'prerequisites',
        'audiences',
    )
```

This one queryset setup prevents N+1 across all list/detail views that use it.

#### Deeper prefetch (chained relationships)
```python
video_asset = VideoAsset.objects.select_related('lecture__section__course').get(pk=id)
# One query that JOINs video_assets → lectures → sections → courses
```

### `select_for_update` note
This is NOT for N+1 prevention. It is a row lock (see Section 24).

### When N+1 is hard to spot
It's easy to miss N+1 when it's inside a serializer's `to_representation()` or a nested field that iterates a reverse relationship. Always check which related fields your serializer accesses and make sure the queryset fetches them up front.

---

## 22. Django ORM Optimization

### Aggregations
```python
from django.db.models import Max

result = SectionContent.objects.filter(section=section).aggregate(Max('position'))
max_pos = result['position__max'] or 0
```

`aggregate()` returns a dict. Returns a single row of computed values for the entire queryset. Uses SQL: `SELECT MAX(position) FROM section_contents WHERE section_id = ?`.

### `values_list`
Returns flat lists or tuples instead of model instances — much faster when you don't need the full object:

```python
ids = section_qs.filter(
    position__gte=target_position,
    position__lt=current_position,
).values_list('id', flat=True)
# Returns: [1, 2, 5, 10, ...] — plain list of integers
```

### `F()` expressions — refer to a column in a query
`F()` represents a database column value without pulling it to Python. Allows atomic DB-level updates:

```python
from django.db.models import F

# Shift positions up: UPDATE ... SET position = position + 1 WHERE ...
SectionContent.objects.filter(pk__in=impacted_ids).update(position=F('position') + 1)
# This is ONE query and avoids race conditions
```

Without `F()` you would load each row, add 1, and save individually — generating N queries.

### `update()` — bulk update
Runs a single SQL UPDATE for the entire queryset:

```python
SectionContent.objects.filter(pk=section_content.pk).update(position=temp_position)
# One query. Does NOT call .save() and does NOT trigger signals.
```

Use `update()` for performance-critical updates. Beware: signals and custom `save()` logic are bypassed.

### `bulk_create` — batch insert
```python
CourseLearningObjective.objects.bulk_create(new_objects)
# Inserts all objects in a single INSERT INTO ... VALUES (...), (...), (...)
```

### `get_or_create`
Returns `(instance, created_bool)`:
```python
profile, created = LearnerProfile.objects.get_or_create(user=instance)
# Runs: SELECT ... WHERE user_id=?; if not found, INSERT ...
```

### `Case` / `When` — conditional expressions
```python
from django.db.models import Case, IntegerField, When

SectionContent.objects.filter(pk__in=ids).update(
    position=Case(
        *[When(pk=pk, then=new_pos) for pk, new_pos in zip(ids, new_positions)],
        output_field=IntegerField(),
    )
)
# One UPDATE with conditional logic: UPDATE ... SET position = CASE WHEN id=1 THEN 3 ...
```

### `distinct()`
Removes duplicate rows from a queryset (needed when JOINs cause row multiplication):
```python
NidusCourse.objects.filter(instructors=instructor).distinct()
```

---

## 23. Database Transactions — `transaction.atomic`

### What is a transaction?
A database transaction is a unit of work that either **fully completes** or **fully rolls back**. It follows ACID properties — Atomicity, Consistency, Isolation, Durability.

### Why use `transaction.atomic`?
When multiple database operations must succeed or fail together. If one step fails, all changes are rolled back, leaving the database in a consistent state.

### Using `transaction.atomic` as a context manager
```python
with transaction.atomic():
    video_asset.status = VideoAsset.Status.READY
    video_asset.save(update_fields=['status', 'updated_at'])

    lecture.stream_master_playlist = master_playlist
    lecture.save(update_fields=['stream_master_playlist', 'updated_at'])

    job.status = VideoProcessingJob.Status.COMPLETED
    job.save(update_fields=['status', 'completed_at', 'updated_at'])
# If any .save() raises an exception, ALL three changes are rolled back
```

### Using as a decorator
```python
@transaction.atomic
def update_assignment(assignment_id, user, validated_data):
    assignment = _get_owned_assignment(assignment_id, user)
    # ... multiple DB operations ...
    assignment.save()
    return assignment
```

### Savepoints
Nested `atomic()` blocks create database savepoints, allowing partial rollbacks:
```python
with transaction.atomic():         # outer transaction
    do_something()
    with transaction.atomic():     # savepoint
        do_risky_thing()           # if this fails, only this block rolls back
    do_more_things()               # this still runs
```

### When to use `transaction.atomic`?
- Creating a parent record plus related child records
- Updating multiple records that must be consistent
- Any "write multiple tables" scenario where partial completion would corrupt data

---

## 24. Row-level Locking — `select_for_update`

### What is a race condition?
Two simultaneous requests read the same value, compute a new value based on it, and both write — one overwrites the other's work.

**Example without locking:**
- Request A reads `max_position = 5`
- Request B reads `max_position = 5`  
- Request A inserts at position 6
- Request B inserts at position 6 → **violates the unique constraint!**

### `select_for_update()` — row locking
Places a `SELECT ... FOR UPDATE` lock on the selected rows. Other transactions trying to read those rows with `SELECT FOR UPDATE` will **block** until the lock is released.

```python
locked_assignment = (
    Assignment.objects
    .select_for_update()          # acquires a row lock
    .filter(pk=assignment_id, section__course__instructors=user)
    .first()
)
# ... compute next_position based on locked state ...
AssignmentQuestion.objects.create(assignment_id=assignment_id, position=next_position, ...)
# lock is released when the surrounding transaction.atomic() block ends
```

Must be inside a `transaction.atomic()` block to be meaningful.

---

## 25. Database Indexes and Constraints

### Indexes
An index is a data structure (usually a B-tree) that speeds up lookups on specific columns. Without an index, every query requires a full table scan.

```python
class Meta:
    indexes = [
        # Composite index: queries that filter by status AND sort by created_at are fast
        models.Index(fields=['status', '-created_at'], name='idx_ncourse_status_date'),
        # Single-column index via field-level db_index=True:
        # email = models.EmailField(unique=True, db_index=True)
    ]
```

**When to add an index:**
- Columns used in `WHERE` clauses frequently
- Columns used in `ORDER BY`
- FK columns (Django adds these automatically)
- Columns used in JOINs

**`unique=True` on a field** implicitly creates a unique index.

### `UniqueConstraint`
Enforces that a combination of columns is unique:
```python
models.UniqueConstraint(
    fields=['user', 'course'],
    name='uniq_enrollment_user_course',
)
# Prevents a learner from enrolling in the same course twice
```

### `CheckConstraint`
Enforces a custom SQL condition:
```python
models.CheckConstraint(
    check=models.Q(progress_percent__lte=100),
    name='chk_enrollment_progress_percent_lte_100',
)
# The database itself rejects any row where progress_percent > 100
```

**Why define constraints at the DB level?**  
Application-level validation can be bypassed (direct DB manipulation, bulk operations). DB constraints are the last line of defense.

---

## 26. Django Celery — Async Task Queue

### What is Celery?
Celery is an asynchronous task queue. Instead of making the HTTP request wait for a slow operation (like transcoding a video), you hand the work off to Celery and respond immediately.

### Components
```
Request → Django View → Celery (sends task message to Redis) → Response (immediate)

In the background:
Redis (message broker) → Celery Worker (separate process) → Executes the task
```

- **Broker** — the message queue that stores task messages until a worker picks them up. This project uses **Redis**.
- **Worker** — a separate long-running process that monitors the broker queue and executes tasks.
- **Result backend** — stores task return values and status. Also Redis here.

---

### Redis — The Broker Behind Celery

#### What is Redis?
Redis (Remote Dictionary Server) is an **in-memory data store**. All data lives in RAM, making reads and writes orders-of-magnitude faster than a relational database like PostgreSQL, which writes to disk.

Redis is not just a cache — it supports rich data structures:

| Data Structure | Redis Type | Use case |
|----------------|-----------|---------|
| Key-Value | Strings | Caching, counters, tokens |
| List | Lists | Task queues (Celery uses this) |
| Hash | Hashes | Session/object storage |
| Set | Sets | Unique membership checks |
| Sorted Set | ZSets | Leaderboards, priority queues |
| Pub/Sub | Channels | Real-time messaging |

#### How Redis works internally
1. **Single-threaded event loop** — Redis processes commands one at a time with zero lock contention. This is why it is extremely fast and predictable.
2. **In-memory first** — all data is kept in RAM. Optional persistence (RDB snapshots, AOF logs) can flush data to disk.
3. **Non-blocking I/O** — it handles thousands of concurrent connections without creating a thread per connection.

A typical Redis data flow:
```
Client LPUSH myqueue "task_message"   ← producer pushes to the left of a list
Worker BRPOP myqueue 0                ← consumer blocks on the right end, waiting
```
`BRPOP` blocks (waits) until a message arrives — no polling needed. This is exactly how Celery's worker listens for new tasks.

#### Why Redis (not PostgreSQL) as the Celery broker?

| Concern | PostgreSQL as broker | Redis as broker |
|---------|---------------------|----------------|
| Speed | Slow — disk I/O for every enqueue/dequeue | Fast — in-memory, microsecond latency |
| Designed for queues | No — relational tables need polling | Yes — `BLPOP`/`BRPOP` are purpose-built for queues |
| Scalability | Lock contention under heavy write load | Single-threaded, no lock contention |
| Overhead | Full ACID machinery even for ephemeral messages | Lightweight, optional persistence |

Task messages are **short-lived** — they exist only until a worker picks them up. Paying the full cost of PostgreSQL's ACID guarantees for these ephemeral messages is wasteful. Redis is purpose-built for this workload.

#### Redis as the Result Backend
After a Celery task finishes, its return value and status (`SUCCESS`, `FAILURE`, `RETRY`) are stored so the caller can poll or retrieve results:

```python
result = transcode_video_asset_task.delay(video_asset.pk, job.pk)
result.id        # the task UUID
result.status    # 'PENDING', 'STARTED', 'SUCCESS', 'FAILURE', 'RETRY'
result.get()     # blocks until the task finishes and returns the return value
```

Redis stores these as key-value pairs with a TTL (time-to-live). This project uses the same Redis instance for both the broker and result backend:

```python
# settings.py
CELERY_BROKER_URL = 'redis://127.0.0.1:6379/0'     # database 0 for the queue
CELERY_RESULT_BACKEND = CELERY_BROKER_URL            # same Redis instance
```

The URL format: `redis://<host>:<port>/<db_number>`  
Redis supports 16 logical databases (0–15) within one instance. Using different numbers isolates concerns.

#### Redis connection in this project
```
redis://127.0.0.1:6379/0
         ↑           ↑ ↑
         host       port db-number
```

In production this would point to a managed Redis instance (AWS ElastiCache, Redis Cloud, etc.) with TLS: `rediss://...` (note the extra `s`).

#### Redis vs RabbitMQ (common interview comparison)

| Feature | Redis | RabbitMQ |
|---------|-------|---------|
| Primary purpose | Data store that supports queuing | Dedicated message broker |
| Protocol | Custom (RESP) | AMQP |
| Persistence | Optional (in-memory first) | Messages persisted by default |
| Task routing | Basic | Advanced (exchanges, bindings) |
| Management UI | Redis Commander / RedisInsight | Built-in management plugin |
| Learning curve | Low | Higher |
| Best for | Simple queues, caching, rate limiting | Complex routing, enterprise messaging |

For a course marketplace with straightforward async tasks (video transcoding, email sending), Redis is the right choice — simple setup, fast, and doubles as a cache.

#### Key Redis concepts for the interview

**Persistence modes:**
- **RDB (snapshotting)** — saves a point-in-time snapshot every N seconds or after M writes. Fast to restore, may lose recent data.
- **AOF (Append-Only File)** — logs every write command. Slower but can recover to the last second before a crash.
- Both can be combined.

**Eviction policies** (when memory is full):
- `noeviction` — refuse new writes (safe but risky)
- `allkeys-lru` — evict least-recently-used keys (good for caching)
- `volatile-lru` — only evict keys that have a TTL set

**TTL (Time to Live):**
```
SET token "abc123" EX 3600    # expires in 3600 seconds
```
Celery result entries are stored with a configurable TTL (`CELERY_RESULT_EXPIRES`, default 24 hours). Expired results are automatically purged, keeping memory usage bounded.

---

### Setting up Celery with Django

```python
# career_college_backend/celery.py
import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'career_college_backend.settings')

app = Celery('career_college_backend')
# Load config from Django settings, using CELERY_ prefix
app.config_from_object('django.conf:settings', namespace='CELERY')
# Auto-discover tasks.py in all installed apps
app.autodiscover_tasks()
```

```python
# settings.py
CELERY_BROKER_URL = 'redis://127.0.0.1:6379/0'
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
```

```python
# career_college_backend/__init__.py
from .celery import app as celery_app
__all__ = ('celery_app',)
```

### Defining a task

```python
from celery import shared_task

@shared_task(
    bind=True,               # gives access to self (the task instance)
    autoretry_for=(Exception,),  # automatically retry on any exception
    retry_backoff=True,      # wait longer between each retry (exponential backoff)
    retry_jitter=True,       # add randomness to backoff to prevent thundering herd
    max_retries=3,           # try 3 times before giving up
)
def transcode_video_asset_task(self, video_asset_id: int, job_id: int):
    # self.request.retries  — current retry count
    # self.retry(exc=exc)   — manually trigger a retry
    ...
```

**`bind=True`** — makes the task a "bound" task. `self` is the task instance, giving access to retry logic, task ID, request info, etc.

**`shared_task`** vs `@app.task` — `shared_task` doesn't require a reference to the specific Celery app object, making it more portable across a multi-app project.

### Calling (dispatching) a task

```python
# Dispatch the task asynchronously — returns immediately
transcode_video_asset_task.delay(video_asset.pk, job.pk)

# Alternative with keyword args
transcode_video_asset_task.apply_async(args=[video_asset.pk, job.pk], countdown=5)
```

`.delay()` is a shorthand for `.apply_async()`. The task message goes to Redis; a worker picks it up and runs `transcode_video_asset_task(video_asset_id, job_id)` in a separate process.

### How it's used in this project: Video Transcoding Pipeline

1. Instructor uploads a raw video file → `VideoAsset` created with `status='uploading'`
2. View dispatches: `transcode_video_asset_task.delay(video_asset.pk, job.pk)`
3. View returns `201 Created` immediately (no waiting for transcoding)
4. Celery worker picks up the task:
   - Sets `VideoAsset.status = 'processing'`
   - Runs FFmpeg to produce 5 HLS renditions (240p, 360p, 480p, 720p, 1080p)
   - On success: sets `status = 'ready'`, stores playlist paths
   - On failure: sets `status = 'failed'`, saves error message
   - Auto-retries up to 3 times with backoff on exception

### Starting the Celery worker
```bash
celery -A career_college_backend worker -l info
```

### Why Celery for video transcoding?
FFmpeg transcoding can take minutes. An HTTP response must come back in seconds. Without async processing, the client connection would time out and the user experience would be broken.

### Retry with exponential backoff
```
Attempt 1: immediate
Attempt 2: wait ~2 seconds
Attempt 3: wait ~4 seconds
(jitter adds randomness so all retrying workers don't hit the DB simultaneously)
```

---

## 27. JWT Authentication — SimpleJWT

### What is JWT?
JSON Web Token — a signed, stateless token that proves identity. Structure: `header.payload.signature` (all base64-encoded).

The server signs the token with a secret key. The client sends it back in the `Authorization: Bearer <token>` header. The server verifies the signature without hitting the database.

### Token types in this project
- **Access token** — short-lived (12 hours). Used for API calls. Small, included in every request.
- **Refresh token** — longer-lived (7 days). Used ONLY to get a new access token when the current one expires. Stored securely (ideally in an HttpOnly cookie).

### SimpleJWT Configuration
```python
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=12),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,      # issue a NEW refresh token on every refresh
    'BLACKLIST_AFTER_ROTATION': True,    # add the old refresh token to a blacklist
    'AUTH_HEADER_TYPES': ('Bearer',),
}
```

### Token rotation + blacklisting
When a client refreshes:
1. Old refresh token → blacklisted (can never be used again)
2. New refresh token → issued

This prevents refresh token theft: if an attacker steals a refresh token and uses it, the legitimate user's next refresh attempt will find their token blacklisted → they must log in again.

The `rest_framework_simplejwt.token_blacklist` app stores the blacklist in the database.

### Generating tokens
```python
from rest_framework_simplejwt.tokens import RefreshToken

refresh = RefreshToken.for_user(user)
access_token = str(refresh.access_token)
refresh_token = str(refresh)
```

### Blacklisting on logout
```python
class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def save(self):
        token = RefreshToken(self.validated_data['refresh'])
        token.blacklist()   # adds to the blacklist table
```

### DRF Authentication class
```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
}
```
This class reads `Authorization: Bearer <token>` from each request header, validates the JWT signature, and sets `request.user`.

### HttpOnly Cookie flow (Google OAuth)
For the Google OAuth flow, tokens are stored in HttpOnly cookies (not accessible to JavaScript):
```python
response.set_cookie(
    key='access_token',
    value=access_token,
    httponly=True,        # JS cannot read this
    secure=True,          # only sent over HTTPS
    samesite='Lax',
)
```
This protects against XSS attacks — even if an attacker injects JavaScript, they cannot steal the token.

---

## 28. OAuth2 — Google Authorization Code Flow

### What is OAuth2?
OAuth2 is an authorization framework that lets users grant third-party applications access to their accounts without sharing their passwords.

### Authorization Code Flow (used in this project)

```
1. User clicks "Sign in with Google"
2. Backend generates an authorization URL + random 'state' parameter
3. User is redirected to Google's consent screen
4. Google redirects back with a one-time authorization code
5. Backend exchanges the code for Google access + ID tokens (server-to-server)
6. Backend fetches user profile from Google
7. Backend provisions or finds the user in the database
8. Backend issues its own JWT tokens and returns them
```

**Why store `state` in the session?**  
`state` is a random value generated per request. When Google redirects back, you verify the returned `state` matches what you stored. This prevents CSRF attacks on the OAuth callback.

### Code structure
```python
class GoogleAuthRedirectView(APIView):
    def get(self, request):
        url, state = build_authorization_url()
        request.session['google_oauth_state'] = state   # CSRF protection
        return HttpResponseRedirect(url)

class GoogleAuthCallbackView(APIView):
    def get(self, request):
        # Google redirects here with ?code=...&state=...
        code = request.query_params.get('code')
        # Forward code to frontend for final exchange, OR exchange directly here
```

### User provisioning
The `get_or_create_google_user()` function handles:
- Does a user with this email already exist? Log them in.
- Does a user exist but registered with a different method? Raise `GoogleOAuthAccountConflictError`.
- No user exists? Create one from the Google profile data.
- User is blocked? Raise `GoogleOAuthBlockedUserError`.

---

## 29. Service Layer Pattern

### What is the Service Layer?
A layer of pure Python functions that contain **business logic**. They sit between views (which handle HTTP) and models (which handle DB schema). Serializers handle input validation only.

```
Request → View (HTTP concerns) → Service (business logic) → Model (data persistence)
```

### Why use a service layer?
- **Testability** — service functions can be unit tested without HTTP
- **Reusability** — the same logic can be called from a view, a management command, a Celery task, etc.
- **Single Responsibility** — views stay thin; business rules have one home

### Example from this project
```python
# courses/services/section_service.py

@transaction.atomic
def reorder_section_content(section_content: SectionContent, new_position: int) -> SectionContent:
    """Pure business logic: reorder a content item within its section."""
    section = section_content.section
    section_qs = SectionContent.objects.select_for_update().filter(section=section)
    max_position = section_qs.aggregate(Max('position'))['position__max'] or 0
    target_position = min(new_position, max_position)
    # ... shift logic using F() expressions ...
    return section_content
```

The view just calls:
```python
reorder_section_content(section_content, new_position)
```

All the reordering complexity (locking, position shifting, conflict avoidance) is encapsulated in the service function.

### Assignment service pattern
```python
# Service receives validated_data (already clean), performs the DB work
def add_question(assignment_id, user, validated_data) -> AssignmentQuestion:
    locked_assignment = Assignment.objects.select_for_update()...first()
    next_position = AssignmentQuestion.objects.filter(...).aggregate(Max('position'))['...'] + 1
    return AssignmentQuestion.objects.create(assignment_id=assignment_id, position=next_position, **validated_data)
```

---

## 30. Selector Pattern

A **selector** is a module of query-building functions that return querysets. They centralise database read logic, preventing the same queryset setup from being duplicated across multiple views.

```python
# courses/selectors.py

def get_course_base_queryset():
    """Base queryset for courses — always include the commonly needed relations."""
    return NidusCourse.objects.select_related('created_by', 'category').prefetch_related(
        'instructors', 'partner_institutions', 'learning_objectives',
        'prerequisites', 'audiences',
    )

def get_instructor_courses(instructor):
    return get_course_base_queryset().filter(instructors=instructor).distinct().order_by('-created_at')
```

Multiple views import `get_instructor_courses()`. If you ever need to change what "instructor courses" means (e.g., add a filter for active courses only), you change it in ONE place.

---

## 31. File Uploads and Media Handling

### ImageField and FileField
```python
thumbnail = models.ImageField(upload_to=course_thumbnail_upload_path, blank=True, null=True)
```

`upload_to` can be a string path or a **callable** that receives the instance and filename:

```python
def course_thumbnail_upload_path(instance, filename):
    base_name, ext = os.path.splitext(filename)
    slug = slugify(base_name) or 'thumbnail'
    unique_suffix = uuid.uuid4().hex[:10]
    return f"courses/thumbnails/{slug}_{unique_suffix}{ext.lower()}"
```

Using UUID suffixes prevents filename collisions and makes paths unpredictable (minor security benefit).

### Settings for media
```python
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
```

`MEDIA_ROOT` — where Django saves files on disk.  
`MEDIA_URL` — the URL prefix that serves the files in development.

### Serving media in development
Add to `urls.py`:
```python
from django.conf import settings
from django.conf.urls.static import static

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

In production, a web server (Nginx, S3) serves media files directly — never Django.

---

## 32. Django Middleware

### What is Middleware?
Middleware is a hook into Django's request/response processing. Each middleware component is a callable that wraps the view. It processes the request before it reaches the view and the response before it is returned to the client.

```
Request → Middleware 1 → Middleware 2 → View → Middleware 2 → Middleware 1 → Response
```

### Middleware order in `settings.py`
```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',   # security headers
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',       # CSRF protection
    'django.contrib.auth.middleware.AuthenticationMiddleware',  # sets request.user
    'allauth.account.middleware.AccountMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
```

Order matters. `SessionMiddleware` must come before `AuthenticationMiddleware` because auth reads from the session.

### Writing custom middleware
```python
class RequestLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response   # next middleware or view

    def __call__(self, request):
        # Code here runs BEFORE the view
        response = self.get_response(request)   # call the view
        # Code here runs AFTER the view
        return response
```

---

## 33. Django Management Commands

Custom management commands extend `manage.py` with project-specific operations.

### Structure
```
courses/management/commands/
    populate_section_content.py
    reindex_section_content_positions.py
```

```python
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Populate SectionContent for existing lectures'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        # ... logic ...
        self.stdout.write(self.style.SUCCESS('Done.'))
```

Run with:
```bash
python manage.py populate_section_content --dry-run
```

**Use cases in this project:**
- `populate_section_content` — data migration helper to backfill `SectionContent` rows for pre-existing lectures
- `reindex_section_content_positions` — repair tool to fix position gaps/conflicts

---

## 34. Error Handling Patterns

### ValidationError from state machines
Django's `ValidationError` can carry a `message_dict` (field-level errors) or plain messages (non-field errors). Handle them differently:

```python
try:
    course.transition_to('under_review')
except ValidationError as e:
    if hasattr(e, 'message_dict'):
        # Field-level errors → 400 with errors dict
        return Response(
            {'success': False, 'message': 'Action failed.', 'errors': e.message_dict},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Plain string → state machine / business rule violation → 422
    return Response(
        {'success': False, 'message': e.messages[0]},  # use messages[0], NOT str(e.message)
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )
```

**Why `e.messages[0]` instead of `str(e.message)`?**  
`e.message` can be a list when multiple messages are attached. `str(list)` gives you `"['message1', 'message2']"` — ugly, leaked internals. `e.messages` is always a list; `e.messages[0]` is the clean first string.

### HTTP status codes cheat sheet

| Code | Meaning | When to use |
|------|---------|------------|
| 200 | OK | Successful GET, PATCH, DELETE |
| 201 | Created | Successful POST (resource created) |
| 400 | Bad Request | Validation failure, malformed input |
| 401 | Unauthorized | Not authenticated |
| 403 | Forbidden | Authenticated but not authorized |
| 404 | Not Found | Resource doesn't exist |
| 409 | Conflict | Duplicate resource (e.g. already enrolled) |
| 422 | Unprocessable Entity | Business rule violation (valid input, illegal operation) |
| 500 | Internal Server Error | Unexpected server-side failure |

### `get_object_or_404`
A shortcut that calls `.get(...)` and raises `Http404` if not found:
```python
course = get_object_or_404(NidusCourse, pk=pk, instructors=request.user)
# If not found, returns 404 automatically
```

DRF converts `Http404` into a 404 JSON response via its exception handler.

### Try-except scope discipline
Only wrap genuinely risky operations (DB writes, external calls, token generation):
```python
try:
    course = serializer.save()   # DB write can fail (IntegrityError, DB down)
except IntegrityError:
    return Response({'success': False, 'message': 'Duplicate data.'}, status=400)
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    return Response({'success': False, 'message': 'An unexpected error occurred.'}, status=500)
```

**Never** catch `Exception` around your serializer validation (`.is_valid()` cannot raise unexpected exceptions by design).

---

## 35. Settings and Environment Variables

### Never hardcode secrets
All sensitive values come from environment variables via a `.env` file:

```python
import os
from dotenv import load_dotenv

load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.getenv('SECRET_KEY', 'unsafe-dev-secret-key')
DEBUG = env_bool('DEBUG', default=True)
```

### Custom env helpers
```python
def env_bool(name, default=False):
    """Read a boolean from an env var. Accepts '1', 'true', 'yes', 'on'."""
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "t", "yes", "y", "on"}

def env_list(name, default=""):
    """Read a comma-separated list from an env var."""
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]
```

### `settings.AUTH_USER_MODEL`
Use the string reference `settings.AUTH_USER_MODEL` in FK definitions (not the imported class) to avoid circular import issues. The actual class is resolved lazily.

---

## 36. Migrations

### What are migrations?
Migration files are Python scripts that describe database schema changes. They form an ordered history of every change to your data model.

```bash
python manage.py makemigrations   # generates migration files from model changes
python manage.py migrate          # applies pending migrations to the database
python manage.py showmigrations   # shows which migrations have been applied
```

### How they work
`makemigrations` compares the current model state to the last applied migration state and generates a new migration file. `migrate` applies the `up` direction; the `--fake` flag marks migrations as applied without running them.

### Migration dependencies
Each migration file declares which migration it depends on. Django builds a dependency graph and applies them in the correct order.

### Data migrations
Beyond schema changes, you can write migrations that also modify data:
```python
def populate_data(apps, schema_editor):
    MyModel = apps.get_model('myapp', 'MyModel')
    MyModel.objects.filter(...).update(...)

class Migration(migrations.Migration):
    operations = [
        migrations.RunPython(populate_data, migrations.RunPython.noop),
    ]
```

**Note:** The management commands in this project (`populate_section_content`) are manual data repair scripts run by operators — not data migrations — because they may need `--dry-run` and interactive feedback.

---

## 37. Slug Fields and Auto-generation

A slug is a URL-friendly string: lowercase letters, numbers, and hyphens. Slugs appear in URLs instead of numeric IDs (`/courses/python-for-beginners/` vs `/courses/42/`).

```python
slug = models.SlugField(max_length=280, unique=True, db_index=True)
```

### Auto-generating unique slugs

```python
def save(self, *args, **kwargs):
    if not self.slug:                          # only generate on first save
        base_slug = slugify(self.title) or 'course'
        candidate = base_slug
        suffix = 1
        while NidusCourse.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base_slug}-{suffix}"
            suffix += 1
        self.slug = candidate
    super().save(*args, **kwargs)
```

This loop tries `python-basics`, then `python-basics-1`, `python-basics-2`, etc. until a unique slug is found. The `.exclude(pk=self.pk)` is important: on updates, the model should not conflict with its own current slug.

---

## 38. Denormalization Pattern

**Denormalization** stores derived/computed data in an extra column to make queries faster, at the cost of needing to keep that column in sync.

### Example: `is_published` flag
```python
is_published = models.BooleanField(
    default=False,
    db_index=True,
    help_text='Denormalized flag for fast published-course queries.',
)
```

`is_published` is `True` only when `status == 'published'`. It's technically redundant (you could compute it from `status`), but it allows a fast single-column index lookup:

```python
# Fast: single index on is_published
NidusCourse.objects.filter(is_published=True)

# Slower: must read the status column (still indexed, but less direct)
NidusCourse.objects.filter(status='published')
```

The `save()` method keeps them in sync:
```python
self.is_published = self.status == self.CourseStatus.PUBLISHED
```

**Important:** The SAME information is now in two places. You MUST update both atomically. The discipline is to only change `status` via `transition_to()`, which calls `save()`, which syncs `is_published`.

---

## 39. Video Transcoding Pipeline

### Full flow
```
1. Instructor POSTs video file to /courses/{id}/lectures/{id}/video-upload/
2. View creates VideoAsset(status='uploading') and VideoProcessingJob
3. View calls: transcode_video_asset_task.delay(video_asset.pk, job.pk)
4. View responds 202 Accepted immediately

5. Celery worker picks up the task:
   - Sets VideoAsset.status = 'processing'
   - Calls FFmpeg to produce HLS (HTTP Live Streaming) output:
     - 240p, 360p, 480p, 720p, 1080p variants
     - One master playlist (m3u8) that references all variants
   - Writes output to: media/courses/{slug}/lectures/{id}/hls/{video_asset_id}/

6. On success:
   - VideoAsset.status = 'ready'
   - Lecture.stream_master_playlist = path to master.m3u8
   - VideoProcessingJob.status = 'completed'

7. On failure:
   - VideoAsset.status = 'failed'
   - Lecture.transcoding_error = error message
   - Task retries up to 3 times with backoff
```

### HLS (HTTP Live Streaming)
HLS breaks a video into small segments (usually 6–10 seconds each) and provides an `.m3u8` playlist file. Video players download the playlist to know which segments to fetch. The adaptive bitrate feature means the player automatically switches to a lower/higher quality variant based on network speed.

---

## 40. Quick-fire Interview Q&A Cheatsheet

**Q: What is the difference between `select_related` and `prefetch_related`?**  
A: `select_related` works for ForeignKey/OneToOne — does a SQL JOIN, one query. `prefetch_related` works for ManyToMany/reverse FK — does separate queries and caches results in Python. Use `select_related` for "to-one" relations, `prefetch_related` for "to-many".

**Q: What is the N+1 problem?**  
A: Fetching N objects then querying the DB for each object's related data, resulting in N+1 queries. Fixed with `select_related`/`prefetch_related`.

**Q: What does `transaction.atomic` do?**  
A: Wraps database operations in a transaction. If any operation inside fails, ALL changes are rolled back. Ensures data consistency.

**Q: What is `select_for_update`?**  
A: Places a row-level lock on selected rows. Other transactions trying to read/lock those rows block until the current transaction ends. Prevents race conditions on concurrent writes.

**Q: What is the difference between `clean()` and `save()`?**  
A: `clean()` is for validation — it raises `ValidationError` but doesn't save. `save()` persists to the DB. Django does NOT call `clean()` automatically before `save()`; you must call `full_clean()` explicitly or rely on serializer validation.

**Q: When would you use signals vs a service layer?**  
A: Signals for truly decoupled, optional side effects (profile creation, notifications). Service layer for core business logic that must be part of the main operation — services are easier to test, debug, and ensure transactional integrity.

**Q: What is `update_fields` in `.save()`?**  
A: Limits the SQL UPDATE to only the specified columns: `obj.save(update_fields=['status'])`. More efficient — only those columns are written.

**Q: What is a GenericForeignKey?**  
A: A virtual field that allows a model to relate to any other model. Uses two real columns: `content_type` (which model) and `object_id` (which row). Used in this project for `SectionContent` to hold lectures, quizzes, or coding exercises in one unified ordering table.

**Q: How does JWT token rotation work?**  
A: When the client uses the refresh token to get a new access token, the old refresh token is blacklisted and a new one is issued. This limits the window of exposure if a refresh token is stolen.

**Q: What is a state machine and why use it on a model?**  
A: A state machine restricts which status transitions are legal and enforces business rules at each transition. Centralises transition logic so views just call `transition_to()` — impossible transitions are rejected at the model level.

**Q: What is `F()` expression?**  
A: Represents a database column in a query without loading its value to Python. `F('position') + 1` generates SQL `position + 1`, enabling atomic, race-condition-free increments in a single UPDATE query.

**Q: Why use `@shared_task` instead of `@app.task` in Celery?**  
A: `shared_task` creates the task without requiring a direct reference to the Celery app instance. This is the recommended pattern in Django projects because it avoids circular imports between the app module and task modules.

**Q: What is soft delete and why is it preferred?**  
A: Soft delete marks records as deleted with a boolean flag instead of removing them from the DB. Benefits: audit trail, recovery without backup restore, no broken foreign key references.

**Q: What is the purpose of `BasePermission.has_permission` vs `has_object_permission`?**  
A: `has_permission` runs for every request to a view (broad access gate). `has_object_permission` runs when checking a specific object (ownership check). In DRF, `has_object_permission` is only called if `has_permission` returns `True` first.

**Q: What is `AllowAny` permission class?**  
A: It returns `True` for every request regardless of authentication status. Used for public endpoints like the course catalog or registration.

**Q: What is the Service Layer pattern?**  
A: A layer of business logic functions separate from views and models. Views handle HTTP concerns, services handle business rules, models handle data schema. Makes business logic testable in isolation and reusable across multiple entry points (views, tasks, management commands).

**Q: How does Django's ORM prevent SQL injection?**  
A: The ORM uses parameterized queries. All values passed to filter/create/update are treated as data, never interpreted as SQL. You would have to use `RawSQL` or `.raw()` explicitly to bypass this protection.

**Q: What is `bulk_create` and when should you use it?**  
A: Inserts multiple model instances in a single SQL INSERT statement. Use when creating many rows at once (e.g., replacing all learning objectives on a course update). Much faster than calling `.create()` in a loop. Note: signals are not fired and `save()` is not called for bulk operations.

**Q: What does `related_name` do in a ForeignKey?**  
A: Sets the name of the reverse relation from the related model back to this model. `section = ForeignKey(Course, related_name='sections')` means you can do `course.sections.all()` — without it, you'd have to use the auto-generated `course.lecture_set.all()`.

**Q: What is the difference between `DELETE` and soft delete in Django?**  
A: `obj.delete()` removes the row from the database permanently. Soft delete sets `is_deleted=True` on the object and saves it. The custom manager then hides it from normal queries with `.filter(is_deleted=False)`.

**Q: What is a Custom Manager and why override `get_queryset`?**  
A: A Manager is the interface for all DB queries on a model. Overriding `get_queryset()` lets you change the "default" queryset — e.g., automatically excluding soft-deleted rows — so that all code using `.objects.all()` gets clean results without needing to remember to add `.filter(is_deleted=False)` everywhere.

---

*Last updated: May 2026 — based on career_college_backend codebase study*

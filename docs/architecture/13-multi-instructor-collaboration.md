# 13) Multi-Instructor Collaboration & Owner Protection

## Overview

A single `NidusCourse` can be co-authored by multiple verified instructors. One instructor is the **owner**; the rest are **co-instructors**. Ownership is permanent for the course's lifetime (unless explicitly transferred — see Future Extensions).

---

## Data Model

```
NidusCourse
  ├── created_by          (FK → User)                    ← owner; set once at creation, immutable via API
  ├── instructors         (M2M → User)                   ← all who can read/edit content; owner is always a member (instructor courses only)
  └── partner_institution (FK → PartnerInstitutionProfile, nullable)  ← set automatically when a partner institution creates the course
```

`created_by` is set to `request.user` inside `NidusCourseCreateUpdateSerializer.create()` and is not exposed in the writable serializer fields (`read_only_fields = ['created_by']`), so it cannot be changed via the API.

`partner_institution` is set automatically at course creation when the creator is a `partner_institution` user. It is never writable via the API — not even by the owner.

---

## What Each Role Can Do

| Action | Owner (`created_by`) | Co-instructor | Admin |
|--------|---------------------|---------------|-------|
| Edit title, description, price, etc. | Yes | Yes | Via admin panel |
| Add / remove sections | Yes | Yes | Via admin panel |
| Add / remove lectures, quizzes, assignments, coding exercises | Yes | Yes | Via admin panel |
| Upload videos | Yes | Yes | Via admin panel |
| **Add / remove instructors** | **Yes** | **No** | Via admin panel |
| Submit for review | Yes | Yes | N/A |
| Rework after rejection | Yes | Yes | N/A |
| Archive course | Yes | Yes | Yes |
| Restore from archive | Yes | Yes | Yes |
| Delete course | N/A (no delete endpoint) | N/A | Via admin panel |

`partner_institution` is set at course creation by the system — it is never writable via the API. Only an admin can change it via the Django admin panel.

---

## Enforcement Points

### 1. Serializer — `NidusCourseCreateUpdateSerializer.update()`

**File:** `courses/all_serializers/course_serializers.py`

This is the primary enforcement point. A co-instructor PATCHing the course will have their `instructors` and `partner_institutions` fields **silently ignored**.

```python
if instructors is not None:
    if request_user == instance.created_by:
        if request_user not in instructors:
            instructors.append(request_user)
        instance.instructors.set(instructors)
    # co-instructors: silently ignore — roster is owner-only

if partner_institutions is not None:
    if request_user == instance.created_by:
        instance.partner_institutions.set(partner_institutions)
    # co-instructors: silently ignore
```

**Why silent ignore, not 403?** PATCH accepts many fields at once. A co-instructor updating the course title might pass the current instructor list in the same payload (typical frontend behaviour). A 403 would block the legitimate title update. Silently ignoring the restricted fields lets content edits succeed while keeping the roster frozen.

### 2. Utility guard — `guard_owner()` in `courses/utils.py`

A reusable guard for any future endpoint that must be owner-only (e.g., a course delete endpoint).

```python
def guard_owner(course, user):
    """Return a 403 Response if user is not the course owner, else None."""
    if course.created_by != user:
        return Response(
            {'success': False, 'message': 'Only the course owner can perform this action.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None
```

Usage pattern (follows the same convention as `guard_editable`):

```python
def delete(self, request, pk):
    course = self._get_course(request, pk)
    if err := guard_owner(course, request.user):
        return err
    course.delete()
    return Response({'success': True, 'message': 'Course deleted.'})
```

### 3. Owner cannot be removed from the instructor list

The `update()` method re-appends the owner to the list if they are absent:

```python
if request_user not in instructors:
    instructors.append(request_user)
```

Because only the owner can call `instructors.set(...)`, this rule is enforced whenever the roster is actually written.

### 4. `created_by` is immutable

`created_by` is in `read_only_fields` on `NidusCourseCreateUpdateSerializer.Meta` — the field is present on the model but not writable through the API. Any attempt to pass it in a PATCH body is silently ignored by DRF.

---

## How Instructors Are Added Today

The owner passes instructor PKs in the request body at creation or via PATCH:

```json
POST /api/v1/courses/create/
{
    "title": "Machine Learning Basics",
    "instructors": [5, 12, 23]
}
```

Or adds them later:

```json
PATCH /api/v1/courses/{pk}/
{
    "instructors": [5, 12, 23, 31]
}
```

The owner must know the co-instructor's user ID. There is no invitation flow yet.

---

## Request Flow: Co-instructor PATCH with Roster Field

```
PATCH /api/v1/courses/{pk}/
  instructor A (co-instructor) sends:
    { "title": "New Title", "instructors": [A_id] }

CourseDetailView._get_course(request, pk)
  → filters NidusCourse where pk=pk AND instructors=A  ✓ (A is in M2M)

guard_editable(course)  → None (course is draft)

NidusCourseCreateUpdateSerializer.update()
  → title = "New Title"  ← applied
  → instructors != None BUT request_user (A) != instance.created_by (owner)
    → silently skipped
  → partner_institutions == None  → skipped

Response: 200 OK, title updated, roster unchanged
```

---

## Future Extensions

| Feature | Description |
|---------|-------------|
| **Invitation flow** | `CourseInstructorInvite` model: owner sends invite by email, instructor accepts via endpoint, then added to M2M. Replaces direct-ID approach. |
| **Granular roles** | Per-instructor roles (`owner`, `editor`, `viewer`) via a through-model on the M2M. Different roles gate different actions. |
| **Transfer ownership** | Allow owner to transfer `created_by` to another instructor. Requires both parties to confirm. |
| **Activity log** | Track who changed what: "Instructor B edited Section 3". Useful for accountability. |

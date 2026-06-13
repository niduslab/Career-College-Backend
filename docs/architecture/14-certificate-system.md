# 14 — Certificate System

## Overview

When a learner completes a course (progress reaches 100%), the platform automatically issues a `Certificate` and sends a congratulations email. The certificate has a public UUID-based share URL so learners can post it on LinkedIn, share with employers, or download a PDF copy.

---

## Trigger: How Completion Is Detected

`recalculate_progress()` in `courses/services/enrollment_service.py` is the single function that recomputes `Enrollment.progress_percent`. It is called:

- via `WatchProgress` post-save signal (lecture completion)
- directly at the end of `submit_quiz_attempt()` (quiz completion)
- via `transaction.on_commit` at the end of `grade_assignment_submission_task` when status is `passed`
- via `transaction.on_commit` at the end of `evaluate_coding_submission_task` when status is `passed`

When `recalculate_progress` transitions progress to ≥ 100% **for the first time** (i.e. `enrollment.completed_at` was `None` before the call), it:

1. Sets `enrollment.completed_at = timezone.now()`
2. Sets a local `newly_completed = True` flag
3. Saves the enrollment row
4. Calls `transaction.on_commit(lambda: _issue_certificate_and_notify(enrollment.pk))`

The `transaction.on_commit` wrapper guarantees the certificate is never issued for a transaction that later rolls back. Whether `recalculate_progress` is called inside an active `transaction.atomic()` block or directly (outside any transaction), Django's `on_commit` semantics handle both cases correctly:

- **Inside atomic block** → callback fires after the outermost block commits
- **Outside any transaction** → callback fires immediately

---

## Certificate Issuance: `issue_certificate()`

`courses/services/certificate_service.py`

```python
def issue_certificate(enrollment: Enrollment) -> Certificate:
    certificate, created = Certificate.objects.get_or_create(
        enrollment=enrollment,
        defaults={
            'learner_name': enrollment.user.full_name,
            'course_title': enrollment.course.title,
            'issued_at': enrollment.completed_at or timezone.now(),
        },
    )
```

**Idempotent by design.** `get_or_create` on the `OneToOneField` means:

- First call → creates and returns a new row, logs `Certificate issued: uid=... user=... course=...`
- Subsequent calls (retry, double-dispatch, Celery redelivery) → returns the existing row unchanged

The `learner_name` and `course_title` fields are **snapshots frozen at issue time**. If a learner later changes their display name, or an instructor edits the course title, the certificate remains an accurate historical record.

---

## Data Model

```
course_certificates
├── id                 BIGINT PK (auto)
├── enrollment_id      FK → enrollments (OneToOne, UNIQUE, CASCADE)
├── certificate_uid    UUID4 (unique, indexed)   ← public identifier
├── learner_name       VARCHAR(200)              ← snapshot
├── course_title       VARCHAR(200)              ← snapshot
├── issued_at          TIMESTAMPTZ
├── created_at         TIMESTAMPTZ (auto)
└── updated_at         TIMESTAMPTZ (auto)
```

`certificate_uid` is UUID4 — non-guessable, cannot be enumerated sequentially. This is intentional: anyone who knows a UUID can view or download the certificate (like a GitHub Gist share link), but they cannot discover other users' certificates by incrementing an integer.

The DB-level `UniqueConstraint` on `enrollment_id` (from `OneToOneField`) provides a belt-and-braces guard against race conditions independent of the application layer.

---

## Email Notification

`_issue_certificate_and_notify()` in `enrollment_service.py` calls:

```python
send_certificate_email_task.delay(certificate.pk)
```

`send_certificate_email_task` is a Celery `@shared_task` with `acks_late=True`, `autoretry_for=(Exception,)`, `max_retries=3` — the same pattern used by `send_instructor_invite_email_task`.

The email (template: `templates/emails/certificate.html`) contains:
- Learner name and completed course title
- Issue date
- **View Certificate** button → `{FRONTEND_URL}/certificates/{uuid}`
- **Download PDF** button → `/api/v1/courses/certificates/{uuid}/download/`
- Certificate UUID in plain text (for copy-paste)

---

## API Endpoints

### `GET /api/v1/courses/my-courses/<slug>/certificate/`

Authenticated learner fetches their own certificate for a course.

| Condition | Response |
|-----------|----------|
| Course slug not found | 404 |
| Enrolled but course not completed | 404 |
| Not enrolled | **403** (slug → 403 per platform policy) |
| Success | 200 + `{certificate_uid, learner_name, course_title, issued_at}` |

Permissions: `IsAuthenticated`, `IsEmailVerified`, `IsLearnerUser`

### `GET /api/v1/courses/certificates/<uuid>/verify/`

Public (no auth). Returns certificate metadata for the share/verify page.

| Condition | Response |
|-----------|----------|
| UUID not found | 404 |
| Found | 200 + `{certificate_uid, learner_name, course_title, issued_at, is_valid: true}` |

`is_valid` is always `true` if the row exists — there is no revocation mechanism.

### `GET /api/v1/courses/certificates/<uuid>/download/`

Public (no auth). Returns a PDF file (`application/pdf`, `Content-Disposition: attachment`).

| Condition | Response |
|-----------|----------|
| UUID not found | 404 JSON |
| PDF generation error | 500 JSON |
| Success | 200 PDF bytes |

Anyone who holds the certificate UUID can download the PDF. This is intentional — it mirrors how platforms like Coursera and Udemy handle public certificate sharing.

---

## PDF Generation

`courses/certificate_pdf.py` uses **reportlab** (pure Python, no system dependencies) to render a landscape A4 PDF on-the-fly. No PDF files are stored on disk. Every download request generates fresh bytes from the immutable database record.

Layout:
- Navy double-border frame with gold accent bars (top and bottom)
- Platform name header
- "CERTIFICATE OF COMPLETION" title with gold divider rule
- Learner name with gold underline (font auto-shrinks for long names)
- Course title (font auto-shrinks for long titles)
- Issue date
- Certificate UUID footer

Because PDFs are generated on demand from immutable snapshots, the design can be updated at any time and all existing certificates automatically get the new layout on next download.

---

## Certificate Revocation

Not implemented. Once issued, a certificate is always valid. `is_valid` on the verify endpoint is always `true` if the row exists.

If revocation is needed in the future, add an `is_revoked = BooleanField(default=False)` field and filter it in `get_certificate_by_uid()` and `get_certificate_for_learner()`.

---

## Progress Regression

If course content is added after a learner completes the course, `recalculate_progress` may drop `progress_percent` below 100% and clear `enrollment.completed_at`. The **certificate is not revoked** — the `Certificate` row remains. When the learner completes the new content and reaches 100% again, `issue_certificate` is called via `on_commit` and `get_or_create` returns the existing row (already issued, no duplicate).

---

## Sequence Diagram

```
Learner finishes last content item
        │
        ▼
recalculate_progress(enrollment)
   progress = 100, completed_at was None
        │
        ├── enrollment.completed_at = now()
        ├── enrollment.save()
        └── transaction.on_commit(→ _issue_certificate_and_notify)
                    │
                    ├── Certificate.get_or_create(enrollment=enrollment)
                    │       → certificate row created (idempotent)
                    └── send_certificate_email_task.delay(certificate.pk)
                                │
                                └── Celery worker: render email → send_mail()
                                        email contains:
                                          View: {FRONTEND_URL}/certificates/{uuid}
                                          Download: /api/v1/courses/certificates/{uuid}/download/
```

---

## Files

| File | Role |
|------|------|
| `courses/all_models/certificate_models.py` | `Certificate` model |
| `courses/services/certificate_service.py` | `issue_certificate`, `get_certificate_for_learner`, `get_certificate_by_uid` |
| `courses/services/enrollment_service.py` | Hook in `recalculate_progress` + `_issue_certificate_and_notify` |
| `courses/certificate_pdf.py` | reportlab PDF renderer |
| `courses/all_serializers/certificate_serializers.py` | `CertificateSerializer`, `PublicCertificateSerializer` |
| `courses/all_views/certificate_views.py` | `LearnerCertificateView`, `CertificateVerifyView`, `CertificateDownloadView` |
| `courses/tasks.py` | `send_certificate_email_task` |
| `courses/email_utils.py` | `send_certificate_email()` |
| `templates/emails/certificate.html` | Congratulations email template |
| `courses/migrations/0012_add_certificate.py` | DB migration |

# Certificate Backend — Where the Changes Go

Companion to `certificate-backend-requirements.md`. This document maps every requirement onto the
concrete files, models, and endpoints that need to change in this codebase.

---

## 1. Current State vs. Requirements

The existing certificate implementation is much thinner than the requirements assume.

`Certificate` (`courses/all_models/certificate_models.py`) currently has only five fields:
`enrollment` (OneToOne), `certificate_uid` (UUID4), `learner_name`, `course_title`, `issued_at`.

| Requirement | Current state |
|---|---|
| Human-readable Certificate ID (`CC-2026-NEXT-000123`) | **Missing** — only a raw UUID4 |
| Certificate status / revocation | **Missing** — `is_valid` is hardcoded `True` at `courses/all_serializers/certificate_serializers.py:26` |
| Instructor designation | **Hardcoded** `'Course Instructor'` at `courses/certificate_pdf.py:414` |
| Instructor signature image | **No model field**; `_draw_signature` exists but is commented out at `courses/certificate_pdf.py:399` |
| Authorized signatory (name/designation/signature) | **Does not exist anywhere in the project** |
| Issuing organization | **Hardcoded** `'Career College'` at `courses/certificate_pdf.py:423` |
| Signature snapshot at issuance | **Missing** |
| Course duration / learning hours on certificate | `NidusCourse.duration_minutes` exists but is unused by the PDF; no learning-hours field |
| Verification by Certificate ID | Only UUID lookup exists |
| Signature upload + validation | No signature fields; no file-size validator exists anywhere in the project |

Neither `InstructorProfile` nor `PartnerInstitutionProfile` has a signature field, and there is no
platform-settings singleton model in the project.

---

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Authorized signatory lives in **both** `PartnerInstitutionProfile` and a new platform singleton, with a fallback chain | Institution-owned courses sign with their own signatory; individual-instructor courses fall back to the platform default. Covers every course type. |
| 2 | Revocation **is** in scope — `status` field + admin revoke/restore endpoints | Requirements §7 lists status as required; it also fixes the currently-dishonest hardcoded `is_valid: True`. |
| 3 | New explicit `learning_hours` field on `NidusCourse` | `duration_minutes` is total video runtime — usually far lower than real course hours, and it reads wrong on a certificate. |
| 4 | Signature uploads **reuse** `PATCH /api/v1/auth/profile/me/` | That endpoint already handles multipart and is self-scoped to `request.user`, so requirements §13's authorization ask is met with no new permission class. |

---

## 3. New Model — `PlatformSettings` Singleton

**New file:** `admin_console/all_models/platform_settings_models.py`
(export from `admin_console/all_models/__init__.py` and `admin_console/models.py`)

`admin_console` is the right home — it is already a model app (`AdminSession`, `AdminActionLog`)
with admin-gated endpoints. `core/` has no `models.py` and is not an installed model app; do not
promote it just for this.

```python
class PlatformSettings(models.Model):
    """Singleton (pk=1): platform branding + the default authorized signatory."""
    organization_name = models.CharField(max_length=200, default='Career College')
    authorized_signatory_name = models.CharField(max_length=200, blank=True)
    authorized_signatory_designation = models.CharField(max_length=200, blank=True)
    authorized_signature = models.ImageField(
        upload_to=authorized_signature_path, blank=True, null=True,
        validators=[validate_image_file, validate_signature_size],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'platform_settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
```

**Endpoints** — must subclass `AdminConsoleAPIView` (per CLAUDE.md's *Admin Console* rule; never a
plain `APIView`):

- `GET  /api/v1/admin-console/platform-settings/` → current settings
- `PATCH /api/v1/admin-console/platform-settings/` → update (multipart for the signature)

**Files:** `admin_console/all_views/platform_settings_views.py`,
`admin_console/all_serializers/platform_settings_serializers.py`, route in `admin_console/urls.py`.
Write an `AdminActionLog` row on PATCH, matching the existing mutation pattern in
`admin_console/services/user_admin_service.py`.

---

## 4. Profile Signature Fields

### `authentication/models.py`

**`InstructorProfile`** (~L571–692) — add one field:

```python
signature = models.ImageField(
    upload_to=instructor_signature_path, blank=True, null=True,
    validators=[validate_image_file, validate_signature_size],
    help_text='Transparent PNG preferred. Snapshotted onto certificates at issuance.',
)
```

Designation reuses the **existing** `current_title` / `headline` fields — do not add a new one.

**`PartnerInstitutionProfile`** (~L695–797) — add three fields:

```python
authorized_signatory_name = models.CharField(max_length=200, blank=True, default='')
authorized_signatory_designation = models.CharField(max_length=200, blank=True, default='')
authorized_signature = models.ImageField(
    upload_to=institution_signature_path, blank=True, null=True,
    validators=[validate_image_file, validate_signature_size],
)
```

### `authentication/utils/upload_helpers.py`

Add four path helpers following the existing `_slugify_upload` pattern. The folder layout matches
requirements §11:

```python
def instructor_signature_path(instance, filename):
    return _slugify_upload(instance, filename, 'signatures/instructors')

def institution_signature_path(instance, filename):
    return _slugify_upload(instance, filename, 'signatures/authorized')

def authorized_signature_path(instance, filename):   # platform singleton
    return _slugify_upload(instance, filename, 'signatures/authorized')

def certificate_signature_path(instance, filename):  # frozen snapshot copies (§6)
    return _slugify_upload(instance, filename, 'certificates/signatures')
```

### `core/validators.py`

Add the project's **first** file-size validator (requirements §3 asks for size validation; none
exists today — only extension checks and a PDF magic-byte sniff):

```python
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024

def validate_signature_size(value):
    try:
        size = value.size
    except (AttributeError, OSError):
        return  # size unavailable in some contexts (e.g. during migrations)
    if size > MAX_SIGNATURE_BYTES:
        raise ValidationError('Signature image must be 2 MB or smaller.')
```

### `authentication/serializers.py`

Add the new fields to the instructor and partner-institution profile serializers.
`PATCH /api/v1/auth/profile/me/` (`authentication/all_views/profile_views.py:61`) then handles the
upload with no new endpoint and no new permission class.

---

## 5. `NidusCourse.learning_hours`

`courses/all_models/course_models.py`, beside `duration_minutes` (L198–201):

```python
learning_hours = models.PositiveIntegerField(
    default=0,
    help_text='Instructor-declared total learning hours, shown on the certificate.',
)
```

Writable on `NidusCourseCreateUpdateSerializer`; read on `NidusCourseSerializer`, the catalog
detail serializer, and the my-courses meta serializer. `duration_minutes` is unchanged — it remains
the catalog's video-runtime figure.

---

## 6. `Certificate` Model — ID, Status, Snapshots

`courses/all_models/certificate_models.py`. Every existing field stays, including `certificate_uid`
(the existing UUID routes remain for backwards compatibility — see §8).

```python
class Status(models.TextChoices):
    VALID = 'valid', 'Valid'
    REVOKED = 'revoked', 'Revoked'

certificate_id = models.CharField(max_length=40, unique=True, db_index=True)  # CC-2026-NEXT-000123
status = models.CharField(max_length=20, choices=Status.choices,
                          default=Status.VALID, db_index=True)
revoked_at = models.DateTimeField(null=True, blank=True)
revoked_reason = models.TextField(blank=True, default='')

# ── Snapshots frozen at issuance ──
completion_date = models.DateField(null=True, blank=True)
course_duration = models.CharField(max_length=100, blank=True, default='')   # e.g. "12 Weeks"
learning_hours = models.PositiveIntegerField(default=0)

instructor_name = models.CharField(max_length=200, blank=True, default='')
instructor_designation = models.CharField(max_length=200, blank=True, default='')
instructor_signature = models.ImageField(
    upload_to=certificate_signature_path, blank=True, null=True)

authorized_signatory_name = models.CharField(max_length=200, blank=True, default='')
authorized_signatory_designation = models.CharField(max_length=200, blank=True, default='')
authorized_signature = models.ImageField(
    upload_to=certificate_signature_path, blank=True, null=True)

issuer_name = models.CharField(max_length=200, blank=True, default='')
```

Add `Index(fields=['certificate_id'])` alongside the existing `idx_cert_uid`.

### Why the signature image files are *copied*, not referenced

Requirements §5 says a later signature change must not alter an already-issued certificate. A
ForeignKey — or a stored path string — points at a row or file that can be overwritten or deleted:
calling `ImageField.save()` on the instructor's profile replaces the object at that storage key, and
every certificate pointing at it silently changes.

Copying the bytes into `media/certificates/signatures/` at issuance is the only construction that
actually freezes the snapshot. `_slugify_upload` appends a uuid suffix, so copies never collide.

### Certificate ID generation

New private helper in `courses/services/certificate_service.py`:

```python
def _generate_certificate_id(course, year) -> str:
    """CC-<YYYY>-<SLUG-ABBREV>-<NNNNNN>, sequential within (year, abbrev)."""
```

- **Abbrev:** first alphanumeric token of `course.slug`, uppercased, non-alphanumerics stripped,
  truncated to 6 chars; falls back to `GEN` when empty.
- **Sequence:** `Certificate.objects.filter(certificate_id__startswith=prefix).count() + 1`,
  zero-padded to 6.
- **Race safety:** wrap the create in a retry loop (≈5 attempts) catching `IntegrityError` on the
  unique constraint and recomputing. Issuance is low-frequency (once per course completion) and
  already runs inside `transaction.on_commit`, so a dedicated Postgres sequence per prefix is not
  worth the DDL.
- **Do not** derive the ID from the pk — it must be permanent and the prefix must be stable.

Once written the ID is never regenerated: `issue_certificate` only sets it in the `get_or_create`
defaults.

---

## 7. Issuance — Snapshot + Eligibility

`courses/services/certificate_service.py` → `issue_certificate(enrollment)`.

### Eligibility guard (requirements §12)

Today the only caller is `_issue_certificate_and_notify` at
`courses/services/enrollment_service.py:594`, which already fires only on the first reach of 100%.
Add a defensive guard anyway so no future caller can bypass it:

```python
if enrollment.completed_at is None:
    raise CertificateError('Learner has not completed this course.', http_status=422)
```

Add `CertificateError(message, http_status)` to the service module, mirroring the existing
`ScheduleError` / `ReviewError` pattern.

### Snapshot resolution — `_resolve_signatories(course)`

- **Instructor:** first of `course.instructors.all()`, else `course.created_by`. Name from
  `user.full_name`; designation from `instructor_profile.current_title or headline or
  'Course Instructor'`; signature from `instructor_profile.signature`.
- **Authorized signatory (fallback chain):** if `course.partner_institution` is set **and** has a
  non-blank `authorized_signatory_name` → use the institution's three fields, with
  `issuer_name = institution.institution_name`. Otherwise → `PlatformSettings.load()`, with
  `issuer_name = settings.organization_name`.
- A blank or missing signature leaves the field null; the PDF and API simply omit it. **Never fail
  issuance because a signature is unset.**

### Copying the image bytes (storage-agnostic)

S3 is a supported backend, so this must never assume a local path — see CLAUDE.md's *Object
Storage* section, with `courses/transcoding.py` as the canonical example:

```python
from django.core.files.base import ContentFile

src.open('rb')
try:
    cert.instructor_signature.save(
        os.path.basename(src.name), ContentFile(src.read()), save=False)
finally:
    src.close()
```

Never call `.path()`. Wrap each copy in its own `try/except` → `logger.warning` and continue: a
failed signature copy must not lose the certificate.

Also snapshot `completion_date = enrollment.completed_at.date()`,
`learning_hours = course.learning_hours`, and `course_duration` — a human string derived from the
schedule when the enrollment has one (`f'{weeks} Weeks'` from `schedule.start_date`/`end_date`),
else blank.

### New service functions

- `get_certificate_by_public_id(identifier)` — accepts either a UUID or a `certificate_id` string.
  Try the UUID parse first, fall back to `certificate_id__iexact`. Same `select_related` chain as
  the existing `get_certificate_by_uid`.
- `revoke_certificate(certificate, *, actor, reason)` / `restore_certificate(certificate, *, actor)`
  — set status / `revoked_at` / `revoked_reason` under `select_for_update`, and write an
  `AdminActionLog` row in the same transaction. Raise `CertificateError(422)` on a double-revoke or
  on restoring an already-valid certificate.
- `build_verification_url(certificate)` — see §8.

---

## 8. API Surface

### Verification URL

Add to `career_college_backend/settings.py`:

```python
CERTIFICATE_VERIFY_PATH = env('CERTIFICATE_VERIFY_PATH', default='/verify/')
```

Build it in one place, `build_verification_url(certificate)` in
`courses/services/certificate_service.py`, used by both the serializers and the PDF:

```
f"{settings.FRONTEND_URL.rstrip('/')}{CERTIFICATE_VERIFY_PATH}{certificate.certificate_id}"
```

Requirements §7's "no localhost in production" is an **environment** concern — `FRONTEND_URL` must
be set to the production domain in prod. Document it in `.env.example` and the architecture doc
rather than adding a runtime assertion.

> Note: this absolute, frontend-facing `verification_url` (what the QR code encodes) is deliberately
> distinct from the existing **relative** `verify_url` API path in `LearnerCertificateListSerializer`.
> Keep both, named differently.

### Routes (`courses/urls.py`, alongside L183–184)

| Method / path | View | Permissions | Notes |
|---|---|---|---|
| `GET certificates/<uuid:certificate_uid>/verify/` | `CertificateVerifyView` (existing) | `AllowAny` | Unchanged route; now returns real status |
| `GET certificates/<uuid:certificate_uid>/download/` | `CertificateDownloadView` (existing) | `AllowAny` | Unchanged |
| `GET certificates/verify/<str:identifier>/` | `CertificatePublicVerifyView` (**new**) | `AllowAny` | Accepts UUID **or** `CC-2026-…` id → 404 if unknown |
| `POST certificates/<uuid:certificate_uid>/revoke/` | `CertificateRevokeView` (**new**) | `IsAuthenticated, IsEmailVerified, IsPlatformAdmin` | Body `{reason}` → 422 if already revoked |
| `POST certificates/<uuid:certificate_uid>/restore/` | `CertificateRestoreView` (**new**) | same | 422 if not revoked |

Place the new `<str:identifier>` route **after** the uuid routes so Django's resolver prefers the
typed converter. Per the project's 403-vs-404 rule, the public verify takes a public credential id →
**404** on unknown, never 403.

### Serializers (`courses/all_serializers/certificate_serializers.py`)

- **`PublicCertificateSerializer`** — replace the hardcoded `get_is_valid` with the real `status`,
  and reshape to requirements §9: `certificate_id`, `student`, `course` (name / duration /
  learning_hours), `completion_date`, `issue_date`, `instructor`, `authorized_signatory`, `issuer`,
  `verification_url`, `status`. Signature URLs stay **relative** (`.url`) per project convention —
  the frontend prepends the origin. Never expose the learner's email or any enrollment internals
  (requirements §13).
- **`CertificateSerializer`** and **`LearnerCertificateListSerializer`** — add `certificate_id`,
  `status`, `verification_url`.

---

## 9. PDF (`courses/certificate_pdf.py`)

- Read all signatory data from the **certificate snapshot fields**, not from `course.created_by`.
  This removes the L410–414 lookup and the L423 hardcoded org name.
- Replace the commented-out `_draw_signature` call at L399 with a real image draw. Add
  `_draw_signature_image(c, image_field, x, y, w, h)` that opens via `field.open('rb')` into
  `ImageReader(BytesIO(...))` with `mask='auto'` for PNG transparency. On any failure, log and fall
  back to the existing hand-drawn `_draw_signature` flourish so the PDF never breaks.
- Add the **right-hand authorized-signatory block** mirroring the existing left-hand instructor
  block (name / designation / issuer). It does not exist today.
- Add `Certificate ID`, `Course Duration`, and `Learning Hours` to the metadata area.
- The verify block (L450–456) uses `build_verification_url()`, showing the `certificate_id` rather
  than the raw UUID.
- **Keep** `_draw_signature` (the vector flourish) as the fallback path — do not delete it.

**QR code:** requirements §8 says the frontend renders it from `verification_url`. No backend QR
generation and no new dependency.

---

## 10. Migrations & Backfill

Five migrations:

1. `authentication/` — signature + signatory fields on the two profiles.
2. `courses/` — `NidusCourse.learning_hours`.
3. `courses/` — `Certificate` new fields, **all nullable / blank / defaulted**, plus indexes.
   `certificate_id` is added as `null=True` at this stage.
4. `courses/` — data migration backfilling `certificate_id` for existing rows (deterministic: order
   by `issued_at`, `id`, reusing the same prefix logic), then `AlterField` to
   `unique=True, null=False`.
5. `admin_console/` — `PlatformSettings`.

**Historical rows keep blank snapshot fields.** They were issued before signatories existed, and
back-filling them from today's profiles would fabricate a snapshot that was never true. Note this
explicitly in the architecture doc.

---

## 11. Analytics Impact — Do Not Skip

Unfiltered `Certificate` counts appear in seven places:

- `analytics/services/admin_analytics_service.py:133, 239, 315`
- `analytics/services/analytics_service.py:128, 384`
- `analytics/services/expert_performance_service.py:103`
- `analytics/services/instructor_students_service.py:174`

Revoking a certificate would silently leave it counted in every KPI.

**Decision — a deliberate split:**

- **Summary "certificates earned" counts** filter `status=Certificate.Status.VALID`. Apply at
  `analytics_service.py:128`, `admin_analytics_service.py:133`,
  `expert_performance_service.py:103`, and `instructor_students_service.py:174` (`has_certificate`).
- **Issuance trend series stay unfiltered** — the certificate genuinely was issued, and the trend is
  a historical record. Leave the two `build_time_series` calls alone.

Document this split in the architecture doc so nobody later "fixes" the apparent inconsistency.

---

## 12. Docs to Update (required by CLAUDE.md)

- **Rewrite `docs/architecture/14-certificate-system.md`.** It is already stale in three ways: it
  describes a nonexistent `send_certificate_email_task` / `courses/email_utils.py`, an old PDF
  layout, and a `courses/migrations/0012_add_certificate.py` that does not exist — plus a
  since-fixed claim that `completed_at` gets cleared. Add: the certificate ID format and its race
  safety, the snapshot rationale (why files are copied), the signatory fallback chain, the
  status/revocation model, the analytics counted-vs-trend split, and the
  `FRONTEND_URL`-must-not-be-localhost operational note.
- **New `docs/api-testing/postman-certificates.md`** — manual-test guide: upload a signature →
  complete a course → verify by ID → download the PDF → revoke → re-verify.
- **Update root `CLAUDE.md`** — the *Certificate System* section (new fields, endpoints, signatory
  fallback) and the *Admin Console* section (add `PlatformSettings`).
- **`.env.example`** — add `CERTIFICATE_VERIFY_PATH`, plus a note that `FRONTEND_URL` must be the
  production domain.

---

## 13. Files Touched

**New**

```
admin_console/all_models/platform_settings_models.py
admin_console/all_serializers/platform_settings_serializers.py
admin_console/all_views/platform_settings_views.py
courses/all_tests/test_certificate_issuance.py
courses/all_tests/test_certificate_verification.py
docs/api-testing/postman-certificates.md
```

**Modified**

```
courses/all_models/certificate_models.py
courses/all_models/course_models.py
courses/services/certificate_service.py
courses/all_serializers/certificate_serializers.py
courses/all_views/certificate_views.py
courses/urls.py
courses/certificate_pdf.py
authentication/models.py
authentication/serializers.py
authentication/utils/upload_helpers.py
core/validators.py
admin_console/urls.py, all_models/__init__.py, models.py
career_college_backend/settings.py
analytics/services/{analytics,admin_analytics,expert_performance,instructor_students}_service.py
docs/architecture/14-certificate-system.md
CLAUDE.md
.env.example
```

**Untouched:** `courses/services/enrollment_service.py` — the issuance trigger is already correct.

---

## 14. Verification

### Automated

```bash
python manage.py test courses.all_tests.test_certificate_issuance \
                      courses.all_tests.test_certificate_verification \
                      analytics
```

1. **Snapshot immutability** (the headline requirement) — issue a certificate, then change the
   instructor's `signature` and `current_title` and the institution's
   `authorized_signatory_name`; re-fetch → every certificate field is unchanged, and the copied
   signature file is still readable at its original key.
2. **ID uniqueness / permanence** — issue N certificates on one course → distinct sequential ids;
   call `issue_certificate` twice → the same id (the existing idempotency test must still pass).
3. **Signatory fallback** — institution-owned course → institution signatory; individual-instructor
   course → `PlatformSettings`; neither configured → blank fields, issuance still succeeds.
4. **Eligibility** — `issue_certificate` on an enrollment with `completed_at=None` →
   `CertificateError` 422.
5. **Verification** — `GET certificates/verify/<certificate_id>/` and `.../verify/<uuid>/` resolve
   the same row; unknown → 404; revoked → 200 with `status: revoked`.
6. **Revoke** — non-admin → 403; admin → 200; double-revoke → 422; restore → 200.
7. **Regression** — existing `courses/all_tests/test_my_certificates.py` and the analytics suites
   must still pass.

### Manual

Requires `python manage.py runserver` plus a Celery worker for the completion path.

1. `PATCH /api/v1/auth/profile/me/` (multipart) as an instructor with a transparent PNG → 200, with
   `signature` in the response. Retry with a 3 MB file → 400; with a `.txt` → 400.
2. `PATCH /api/v1/admin-console/platform-settings/` as an admin (session + `X-CSRFToken`) →
   signatory set.
3. Complete a course as a learner → `GET /api/v1/courses/my-certificates/` shows `certificate_id`
   and `verification_url`.
4. `GET /api/v1/courses/certificates/verify/CC-2026-XXX-000001/` **unauthenticated** → the full
   §9-shaped payload.
5. `GET .../download/` → open the PDF; confirm both signature images render, the designation and
   issuer are real (not hardcoded), and the verify line shows the certificate ID.
6. Change the instructor's signature, re-download → **the PDF is unchanged.**
7. Revoke → re-verify shows `revoked`; confirm the admin summary certificate count dropped while the
   trend series did not.

# 14 — Certificate System

How a learner earns a certificate, what gets frozen onto it, how anyone can verify
it, and how an admin revokes one.

---

## 1. Overview

A `Certificate` is issued automatically the first time a learner's progress in a
course reaches 100%. It carries:

- A **permanent, human-readable credential ID** — `CC-2026-NEXTJS-000123`
- A **UUID** (`certificate_uid`) for unguessable share/download links
- A **frozen snapshot** of everything printed on it: learner name, course title,
  completion date, duration, learning hours, and both signatories' names,
  designations and **signature images**
- A **status** (`valid` / `revoked`)

The defining property: **an issued certificate never changes.** If the instructor
uploads a new signature, renames their job title, or the organization swaps its
authorized signatory tomorrow, every previously issued certificate still shows
exactly what it showed on the day it was issued.

---

## 2. Issuance trigger

`recalculate_progress()` (`courses/services/enrollment_service.py`) detects the
first transition to 100% and schedules issuance:

```python
transaction.on_commit(lambda: _issue_certificate_and_notify(enrollment.pk))
```

`on_commit` is used because the certificate must only exist if the progress write
actually committed. `_issue_certificate_and_notify` re-fetches the enrollment,
calls `issue_certificate()`, and dispatches the `COURSE_COMPLETED` notification.
The whole callback is wrapped in a broad `try/except` → `logger.exception`:
bookkeeping must never turn a working lecture fetch into a 500.

**Completion is sticky.** `enrollment.completed_at` is never cleared once set, so
an instructor adding a lecture after the fact does not un-complete learners or
strand their certificates. (See `enrollment_service.py` and the CLAUDE.md note.)

---

## 3. `issue_certificate(enrollment)`

Located in `courses/services/certificate_service.py`. Three responsibilities:

### 3.1 Eligibility

```python
if enrollment.completed_at is None:
    raise CertificateError('Learner has not completed this course.', 422)
```

The only production caller already gates on completion, so this guard exists to
stop any *future* caller from minting a certificate the learner did not earn
(requirements §12). `CertificateError(message, http_status)` follows the same
shape as `ScheduleError` / `ReviewError`.

### 3.2 Idempotency

An existing certificate for the enrollment is returned unchanged — safe under
Celery redelivery, a double `on_commit`, or a retry. Snapshot fields are only
ever written on first creation.

### 3.3 Snapshotting

`_build_snapshot()` resolves every frozen value from live data at that moment:

| Field | Source |
|---|---|
| `learner_name` | `enrollment.user.full_name` |
| `course_title` | `course.title` |
| `completion_date` | `enrollment.completed_at.date()` |
| `learning_hours` | `course.learning_hours` |
| `course_duration` | Derived from the cohort schedule (`"12 Weeks"`); blank for self-paced |
| `instructor_name` | First of `course.instructors`, else `course.created_by` |
| `instructor_designation` | `InstructorProfile.current_title` → `headline` → `"Course Instructor"` |
| `instructor_signature` | **Copy** of `InstructorProfile.signature` |
| `authorized_signatory_*` | Fallback chain — see §5 |
| `issuer_name` | Institution name, else `PlatformSettings.organization_name` |

---

## 4. Why the signature images are **copied**, not referenced

This is the single most important design decision in the feature.

A ForeignKey — or even a stored path string — points at a row or file that can
change underneath it. `ImageField.save()` on the instructor's profile **replaces
the object at that storage key**, so every certificate pointing at it would
silently change. Requirement §5 says exactly the opposite must happen.

So at issuance the bytes are copied into `media/certificates/signatures/`:

```python
src.open('rb')
try:
    cert.instructor_signature.save(os.path.basename(src.name),
                                   ContentFile(src.read()), save=False)
finally:
    src.close()
```

Notes:

- **Storage-agnostic.** Reads through the `FieldFile`, never `.path()`, so it works
  on S3 exactly as on local disk (see CLAUDE.md *Object Storage*, and
  `courses/transcoding.py` as the canonical precedent).
- `_slugify_upload` appends a uuid suffix, so copies never collide.
- Each copy is wrapped in its own `try/except` → `logger.warning`. **A failed
  signature copy must never cost the learner their certificate** — the field is
  left blank and the certificate is still issued.

Regression test: `test_later_signature_change_does_not_alter_issued_certificate`.

---

## 5. Signatory resolution — the fallback chain

Two signatures appear on a certificate: the **course instructor** and the
**authorized signatory** (the organization's representative).

The authorized signatory is resolved by `_resolve_authorized_signatory(course)`:

```
Is the course institution-owned AND does that institution have a
signatory name configured?
  ├─ yes → use PartnerInstitutionProfile.authorized_signatory_*
  │        issuer = institution.institution_name
  └─ no  → use PlatformSettings.authorized_signatory_*
           issuer = institution name if institution-owned, else
                    PlatformSettings.organization_name
```

Configured in two places, both reusing existing endpoints:

| Who | Where | Endpoint |
|---|---|---|
| Instructor's own signature | `InstructorProfile.signature` | `PATCH /api/v1/auth/profile/me/` |
| Institution's signatory | `PartnerInstitutionProfile.authorized_signatory_*` | `PATCH /api/v1/auth/profile/me/` |
| Platform default signatory | `admin_console.PlatformSettings` | `PATCH /api/v1/admin-console/platform-settings/` |

Both profile surfaces are self-scoped to `request.user` and already handle
multipart, so no new upload endpoint and no new permission class was needed
(requirements §13 is satisfied by the existing gate).

**Nothing here is required.** With no signatory configured at all, issuance still
succeeds and the fields are simply blank — the PDF omits the block. Test:
`test_issuance_succeeds_with_no_signatory_configured`.

---

## 6. Certificate ID

Format: `CC-<YYYY>-<SLUG-ABBREV>-<NNNNNN>` — e.g. `CC-2026-NEXTJS-000123`.

- **Abbrev** — first alphanumeric token of the course slug, uppercased, truncated
  to 6 chars; `GEN` if the slug yields nothing usable.
- **Sequence** — count of existing IDs sharing that `(year, abbrev)` prefix, plus
  one, zero-padded to 6.

**Race safety.** The count is not atomic, so the *unique constraint* is the real
guard: `issue_certificate` retries up to 5 times on `IntegrityError`, recomputing
the sequence each pass, and returns the existing row if a concurrent worker won
the enrollment race. Issuance happens once per course completion, so contention
is rare enough that a dedicated Postgres sequence per prefix is not worth the DDL.

**The ID is permanent.** It is written only in the create path and never
regenerated. It is deliberately *not* derived from the pk — a pk is an
implementation detail, and the prefix has to stay stable.

---

## 7. Verification

### Endpoints

| Method / path | Auth | Notes |
|---|---|---|
| `GET certificates/verify/<identifier>/` | Public | Accepts the **certificate ID or the UUID** |
| `GET certificates/<uuid>/verify/` | Public | Original route, still supported |
| `GET certificates/<uuid>/download/` | Public | PDF |
| `POST certificates/<uuid>/revoke/` | `IsPlatformAdmin` | Body `{reason}` |
| `POST certificates/<uuid>/restore/` | `IsPlatformAdmin` | |
| `GET admin/certificates/` | `IsPlatformAdmin` | Browser: `?search=` (ID/learner/course, ≥2 chars), `?status=`, `?sort=` |

`admin/certificates/` is the **discovery surface** for revoke/restore, which
otherwise need a UUID an admin has no way to look up. `search_certificates()`
filters in SQL and falls back to newest-first on an unknown `sort` rather than
400ing — this is a browse screen, and an unordered queryset breaks pagination.

The `<str:identifier>` route is declared **after** the `<uuid:...>` routes so
Django's typed converter wins for a bare UUID.

A revoked certificate still returns **200**, with `status: "revoked"` — "this
credential exists but has been revoked" is precisely the answer a verifier needs.
An unknown identifier returns **404** with the same message either way, leaking
nothing.

The response never includes the learner's email or any enrollment internals
(requirements §13). Test: `test_verification_never_exposes_learner_email`.

### Verification URL

`build_verification_url(certificate)` is the single builder, used by the
serializers **and** the PDF so the printed URL, the API payload and the QR code
can never disagree:

```
FRONTEND_URL + CERTIFICATE_VERIFY_PATH + certificate_id
→ https://careercollege.com/verify/CC-2026-NEXTJS-000123
```

> **Operational requirement.** `FRONTEND_URL` must be the production domain in
> production. Requirement §7's "no localhost" is an environment concern, not a
> runtime check — a localhost value is printed verbatim onto every PDF issued
> while it is set. Flagged in `.env.example`.

**Two differently-named URL fields, deliberately:**
- `verify_url` — relative *API* path (`/api/v1/courses/certificates/<uuid>/verify/`)
- `verification_url` — absolute *frontend* URL, what the QR code encodes

### QR code

Rendered in **both** places, from the same `build_verification_url()` value:

- **PDF** — `_draw_qr()` in `certificate_pdf.py` (the `qrcode` library, pure
  Python, no network). The PDF is what gets printed, emailed and attached to a
  CV, so a printed certificate without a QR would force a verifier to retype a
  long URL by hand.
- **Web** — the frontend's `/verify/<id>` page draws its own with `qrcode.react`.

`_draw_qr` is best-effort: a QR failure logs a warning and the PDF still renders,
because the verification URL is printed as text beside it either way.

---

## 8. Revocation

`revoke_certificate` / `restore_certificate` take `select_for_update()` on the row
and write an append-only `AdminActionLog` entry in the same transaction (actions
`certificate_revoke` / `certificate_restore`, with the certificate ID in
`metadata` and the learner as `target_user`).

**Revocation changes only the verdict, never the record.** `status`, `revoked_at`
and `revoked_reason` are the only fields touched — the issued snapshot stays
intact, so the certificate remains an accurate account of what was awarded and
when. Double-revoke or restoring a valid certificate → **422**.

---

## 9. Analytics — the counted-vs-trend split

`Certificate` is aggregated in several analytics services. Revocation is handled
**inconsistently on purpose**:

| Where | Filtered to `valid`? | Why |
|---|---|---|
| Summary "certificates earned" counts (`analytics_service`, `admin_analytics_service`) | **Yes** | A revoked certificate is not an earned credential |
| Funnel `certified` stage | **Yes** | Same |
| Expert performance per-course counts | **Yes** | Revoked work is not an outcome |
| Instructor roster `has_certificate` | **Yes** | Reads as "no certificate" |
| **Issuance trend series** (`build_time_series`) | **No** | The trend is a historical record — the certificate genuinely *was* issued that month |

Do not "fix" this apparent inconsistency: the two questions are different.

---

## 10. PDF generation

`courses/certificate_pdf.py`, rendered on-the-fly by reportlab — nothing stored
on disk. Landscape A4, centred symmetric layout: double frame with corner
flourishes, wordmark and title stacked at the top, the award statement centred
over a faint seal watermark, then instructor signature · seal · authorized
signature, above a verification strip with the URL and a QR code.

**The palette mirrors the frontend's brand tokens** (`src/app/globals.css`) so
the PDF, the web verify page and the dashboard read as one system —
`_PRIMARY_*` are `--primary-*` and the greys are `--gray-*` by another name.
Change one and change the other. The older `_NAVY` / `_GOLD` names survive as
aliases onto brand purple so the drawing helpers still read naturally.

**The brand mark is bundled**, not uploaded: `courses/assets/career-college-logo.webp`,
read from the package directory rather than through `default_storage` (it is
application art, not user media). `_draw_wordmark` falls back to a drawn CC
monogram if the file is missing, so a packaging mistake never breaks a PDF.

Every signatory value is read from the **certificate snapshot**, never from the
live course or profile rows. That is what makes re-downloading an old certificate
reproduce the original.

- `_draw_signature_image()` opens the stored copy through the `FieldFile`, scales
  it to fit, and draws with `mask='auto'` so PNG transparency is honoured.
- **When there is no stored signature, nothing is drawn.** An earlier version
  fell back to a hand-drawn vector flourish; that was removed deliberately,
  because an invented squiggle above a real person's name reads as *that
  person's signature*, which it is not. Blank space above the rule is the honest
  state and matches an unsigned paper certificate. Do not reintroduce it.
- **The learner name is set in script** (Great Vibes, bundled, SIL OFL) and
  title-cased — script faces are drawn for mixed case and ALL CAPS runs
  together. Great Vibes is Latin-only, so `_is_latin()` routes a name outside
  that range back to `_UNICODE_BOLD` (VeraBd: Latin Extended, Greek, Cyrillic)
  in caps rather than rendering tofu. `_title_case()` leaves already-mixed
  tokens ("McDonald", "O'Brien") alone.
- **There is no background watermark.** The tinted disc and sunburst that used
  to sit behind the body added texture but no meaning and fought the name.
- Two signature columns: instructor (left) and authorized signatory (right, drawn
  only when a name is set).
- The metadata block prints Course Duration, Learning Hours, Completion Date and
  Certificate ID, each omitted when empty.

---

## 11. Data model

```
Certificate
├── enrollment            OneToOne → Enrollment (CASCADE)
├── certificate_uid       UUID4, unique, indexed      ← share/download links
├── certificate_id        CharField, unique, indexed  ← printed credential ID
├── status                valid | revoked
├── revoked_at / revoked_reason
├── learner_name          ┐
├── course_title          │
├── issued_at             │
├── completion_date       │
├── course_duration       ├─ frozen snapshot
├── learning_hours        │
├── instructor_*          │  (name, designation, signature file copy)
├── authorized_*          │  (name, designation, signature file copy)
└── issuer_name           ┘
```

`db_table = 'course_certificates'`. Indexes on both public identifiers.

### Migrations

| Migration | Contents |
|---|---|
| `authentication/0002` | `InstructorProfile.signature`, `PartnerInstitutionProfile.authorized_signatory_*` |
| `courses/0003` | All `Certificate` snapshot fields + `NidusCourse.learning_hours` + indexes |
| `courses/0004` | Data migration backfilling `certificate_id` on pre-existing rows |
| `admin_console/0003` | `PlatformSettings` + new `AdminActionLog.Action` choices |

**Historical certificates keep blank snapshot fields.** They were issued before
signatories existed; filling them from today's profiles would fabricate a
snapshot that was never true. Only `certificate_id` is backfilled (deterministic,
ordered by `issued_at, id`).

---

## 12. Notifications

Issuance dispatches `COURSE_COMPLETED` through the generic notification
dispatcher (`notifications/services/builders.py` → `_course_completed`), with
`certificate_uid` in the payload. Email template:
`notifications/templates/notifications/emails/course_completed.html`.

There is **no** dedicated certificate email task or `courses/email_utils.py` —
earlier revisions of this document described one that does not exist.

---

## 13. Files

| File | Role |
|---|---|
| `courses/all_models/certificate_models.py` | Model + `Status` choices |
| `courses/services/certificate_service.py` | Issuance, ID generation, snapshotting, lookups, revocation |
| `courses/all_serializers/certificate_serializers.py` | Learner + public payloads |
| `courses/all_views/certificate_views.py` | 7 endpoints |
| `courses/certificate_pdf.py` | reportlab renderer |
| `admin_console/all_models/platform_settings_models.py` | Platform singleton |
| `admin_console/all_views/platform_settings_views.py` | Admin settings endpoint |
| `authentication/utils/upload_helpers.py` | Signature upload paths |
| `core/validators.py` | `validate_signature_size` (2 MB cap) |
| `courses/all_tests/test_certificate_issuance.py` | Snapshot immutability, ID, fallback, eligibility |
| `courses/all_tests/test_certificate_verification.py` | Public verify, revoke/restore, PDF |
| `courses/all_tests/test_my_certificates.py` | Learner list endpoint |

Manual walkthrough: `docs/api-testing/postman-certificates.md`.

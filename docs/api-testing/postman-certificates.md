# Postman — Certificates

Manual walkthrough for the certificate feature: configure signatures → complete a
course → verify by ID → download the PDF → prove the snapshot is frozen → revoke.

Base URL: `http://localhost:8000/api/v1`

**Prerequisites**
- Server running (`python manage.py runserver`)
- A Celery worker (`celery -A career_college_backend worker -l info`) — needed for
  the notification email on completion; certificate issuance itself is synchronous
- Two small transparent PNG files to upload as signatures
- Accounts: one instructor, one learner, one admin

---

## 1. Instructor uploads their signature

The signature rides on the existing profile endpoint — there is no separate
upload endpoint.

```
PATCH /auth/profile/me/
Content-Type: multipart/form-data
Auth: instructor
```

| Field | Value |
|---|---|
| `signature` | `ada-signature.png` (file) |
| `current_title` | `Course Instructor` |

**Expect 200.** The response's `profile.signature` is a relative media path.
`current_title` becomes the designation printed on the certificate (it falls back
to `headline`, then to the literal `"Course Instructor"`).

### Validation checks

| Upload | Expect |
|---|---|
| A file over 2 MB | **400** — "Signature image must be 2 MB or smaller." |
| A `.txt` renamed to `.png` | **400** — extension validator |
| A `.pdf` | **400** |

---

## 2. Admin configures the platform authorized signatory

Used for every course that is **not** institution-owned.

```
PATCH /admin-console/platform-settings/
Content-Type: multipart/form-data
Auth: admin session cookie + X-CSRFToken
```

| Field | Value |
|---|---|
| `organization_name` | `Career College` |
| `authorized_signatory_name` | `John Doe` |
| `authorized_signatory_designation` | `Academic Director` |
| `authorized_signature` | `john-signature.png` (file) |

**Expect 200.** `GET` the same URL to read it back.

> A partner institution sets its own signatory through
> `PATCH /auth/profile/me/` instead (`authorized_signatory_name`,
> `authorized_signatory_designation`, `authorized_signature`). An
> institution-owned course prefers those; anything else falls back to the
> platform values above.

---

## 3. Set the course's learning hours

```
PATCH /courses/<course_id>/
Auth: instructor
```

```json
{ "learning_hours": 120 }
```

`learning_hours` is separate from `duration_minutes` (raw video runtime) — it is
the instructor-declared figure printed on the certificate.

---

## 4. Complete the course as a learner

Enroll, then finish every content item (lectures to 100%, quizzes submitted,
assignments passed). The certificate is issued automatically the first time
progress hits 100%.

```
GET /courses/my-certificates/
Auth: learner
```

**Expect 200**, one row:

```json
{
  "certificate_uid": "…",
  "certificate_id": "CC-2026-NEXTJS-000001",
  "status": "valid",
  "learner_name": "Grace Hopper",
  "course_title": "Next.js Development",
  "verification_url": "http://localhost:3000/verify/CC-2026-NEXTJS-000001",
  "download_url": "/api/v1/courses/certificates/…/download/",
  "verify_url": "/api/v1/courses/certificates/…/verify/"
}
```

Note the two URL fields: `verify_url` is the relative **API** path;
`verification_url` is the absolute **frontend** URL the QR code encodes.

---

## 5. Public verification

No auth header — this must work for a stranger holding only the printed ID.

```
GET /courses/certificates/verify/CC-2026-NEXTJS-000001/
```

**Expect 200:**

```json
{
  "success": true,
  "message": "Certificate is valid.",
  "data": {
    "certificate_id": "CC-2026-NEXTJS-000001",
    "status": "valid",
    "student":   { "name": "Grace Hopper" },
    "course":    { "name": "Next.js Development", "duration": "", "learning_hours": 120 },
    "completion_date": "2026-08-01",
    "issue_date": "2026-08-01T…",
    "instructor": {
      "name": "Ada Lovelace",
      "designation": "Course Instructor",
      "signature_url": "/media/certificates/signatures/ada-signature_a1b2c3d4.png"
    },
    "authorized_signatory": {
      "name": "John Doe",
      "designation": "Academic Director",
      "signature_url": "/media/certificates/signatures/john-signature_e5f6g7h8.png"
    },
    "issuer": { "name": "Career College" },
    "verification_url": "http://localhost:3000/verify/CC-2026-NEXTJS-000001"
  }
}
```

Checks:

| Request | Expect |
|---|---|
| Same endpoint with the **UUID** instead of the ID | 200, same row |
| `…/verify/CC-2026-NOPE-999999/` | **404** "Certificate not found." |
| Search the response for the learner's email | Not present |

Note the `signature_url` paths point at `certificates/signatures/` — these are the
frozen copies, not the instructor's profile file.

---

## 6. Download the PDF

```
GET /courses/certificates/<certificate_uid>/download/
```

**Expect 200**, `Content-Type: application/pdf`, filename containing the
certificate ID. Open it and confirm:

- Both signature images render (instructor left, authorized signatory right)
- The designation is the real one, not a hardcoded "Course Instructor"
- The issuer name is real, not a hardcoded "Career College"
- The metadata block shows Learning Hours, Completion Date and Certificate ID
- The verify line shows the **certificate ID**, not the raw UUID

---

## 7. The snapshot test — the headline requirement

This is the behaviour the whole feature exists for.

1. Note the current PDF and the `signature_url` values from step 5.
2. As the instructor, upload a **different** signature and change `current_title`:

```
PATCH /auth/profile/me/     → signature = ada-NEW.png, current_title = "Retired"
```

3. As the admin, change the platform signatory:

```
PATCH /admin-console/platform-settings/  → authorized_signatory_name = "Someone Else"
```

4. Re-run step 5 and step 6.

**Expect: nothing changed.** The verification payload still shows
`Ada Lovelace / Course Instructor / John Doe`, the `signature_url` values are the
same files, and the PDF is byte-for-byte the same certificate.

Any *newly* issued certificate from this point picks up the new values.

---

## 8. Revocation (admin only)

```
POST /courses/certificates/<certificate_uid>/revoke/
Auth: admin
```

```json
{ "reason": "Issued in error." }
```

| Request | Expect |
|---|---|
| Unauthenticated | **401** |
| As the learner | **403** |
| As admin | **200**, `status: "revoked"` |
| Same call again | **422** "Certificate is already revoked." |
| Unknown UUID | **404** |

Then re-verify publicly:

```
GET /courses/certificates/verify/CC-2026-NEXTJS-000001/
```

**Expect 200** (not 404) with `"status": "revoked"` and the message
"This certificate has been revoked." The issued snapshot is untouched — revocation
changes the verdict, never the record.

Check the audit trail:

```
GET /admin-console/audit/?action=certificate_revoke
```

Restore it:

```
POST /courses/certificates/<certificate_uid>/restore/   → 200, status back to "valid"
POST /courses/certificates/<certificate_uid>/restore/   → 422 "Certificate is not revoked."
```

---

## 9. Analytics cross-check

While a certificate is revoked:

| Endpoint | Expect |
|---|---|
| `GET /analytics/admin/summary/` | `certificates.total` **decreased** by one |
| `GET /analytics/admin/certificates/trend/` | series **unchanged** |

That split is deliberate — a revoked certificate is not an earned credential, but
it genuinely was issued that month. See `docs/architecture/14-certificate-system.md` §9.

---

## 10. Production checklist

- [ ] `FRONTEND_URL` is the real domain — **not** `localhost`. It is printed
      verbatim on every PDF and encoded in the QR code.
- [ ] `CERTIFICATE_VERIFY_PATH` matches the frontend's verification route.
- [ ] The frontend serves that route and renders the QR from `verification_url`.
- [ ] The platform authorized signatory is configured before the first real
      certificate is issued — certificates issued without one carry a blank
      signatory block permanently.

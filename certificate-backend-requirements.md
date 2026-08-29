# Certificate Backend Requirements

## 1. Objective

Update the Certificate backend to support professional, verifiable course certificates with instructor and authorized-signatory signatures.

The backend should provide all required certificate information through the API and preserve the exact signature/signatory information used when a certificate is issued.

---

## 2. Required Certificate Information

Each issued certificate should contain/provide:

- Student full name
- Course name
- Course completion status
- Date of completion
- Certificate issue date
- Unique Certificate ID / Credential ID
- Course duration
- Learning hours
- Instructor name
- Instructor designation
- Instructor signature
- Issuing organization name
- Authorized signatory name
- Authorized signatory designation
- Authorized signatory signature
- Certificate verification URL
- Certificate status

Example:

```text
Certificate ID: CC-2026-NEXT-000123
Student: MD. AL AMIN
Course: Next.js Development
Completion Date: August 01, 2026
Issue Date: August 01, 2026
Status: Valid
```

---

## 3. Instructor Signature

The backend should support an instructor signature image.

### Requirements

- Add a signature image field to the instructor profile.
- Admin/backend users should be able to upload or update the signature.
- Store the signature as an image file.
- Recommended format: transparent PNG.
- JPG may also be supported.
- Validate file type, file size, and image dimensions.

Example:

```text
Instructor:
AI Amin

Designation:
Course Instructor

Signature:
ai-amin-signature.png
```

---

## 4. Authorized Signatory

The system should support an organization-level authorized signatory.

Required fields:

```text
Name
Designation
Signature Image
```

Example:

```text
Name: John Doe
Designation: Academic Director
Signature: john-doe-signature.png
```

These values should be configurable from the backend/admin.

---

## 5. Certificate Signature Snapshot

### Important

When a certificate is issued, the signature and signatory information must be preserved as a snapshot.

Previously issued certificates must not automatically change if:

- Instructor changes their signature
- Instructor changes their name/designation
- Authorized signatory changes
- Authorized signatory changes their signature

The issued certificate should preserve:

```text
Instructor Name
Instructor Designation
Instructor Signature

Authorized Signatory Name
Authorized Signatory Designation
Authorized Signature
```

This ensures historical certificates remain consistent with the information used at the time of issuance.

---

## 6. Certificate ID

Every certificate must have a unique and permanent Certificate ID.

Example:

```text
CC-2026-NEXT-000123
```

Requirements:

- Must be unique.
- Must not change after issuance.
- Should be searchable/retrievable by the verification endpoint.
- Should be safe to expose publicly.

---

## 7. Certificate Verification

The certificate should have a public verification URL.

Example:

```text
https://careercollege.com/verify/CC-2026-NEXT-000123
```

Requirements:

- Verification should not require authentication.
- The Certificate ID should identify the certificate.
- The verification response should show the certificate's essential information.
- The verification endpoint should return the current certificate status.

Possible statuses:

```text
valid
revoked
expired
```

Only implement statuses that are actually required by the business rules.

### Important

Do not use `localhost` URLs in production certificate data.

Incorrect:

```text
http://localhost:3000/verify/...
```

Production:

```text
https://careercollege.com/verify/...
```

---

## 8. QR Code

The certificate should support a QR code that points to the public verification URL.

Example QR destination:

```text
https://careercollege.com/verify/CC-2026-NEXT-000123
```

The backend should provide the verification URL required by the frontend for QR generation.

The frontend can generate/render the QR code unless the backend specifically needs to generate the QR image.

---

## 9. Suggested Certificate API Response

The certificate API should return data similar to:

```json
{
  "certificate_id": "CC-2026-NEXT-000123",

  "student": {
    "name": "MD. AL AMIN"
  },

  "course": {
    "name": "Next.js Development",
    "duration": "12 Weeks",
    "learning_hours": 120
  },

  "completion_date": "2026-08-01",
  "issue_date": "2026-08-01",

  "instructor": {
    "name": "AI Amin",
    "designation": "Course Instructor",
    "signature_url": "https://careercollege.com/media/..."
  },

  "authorized_signatory": {
    "name": "John Doe",
    "designation": "Academic Director",
    "signature_url": "https://careercollege.com/media/..."
  },

  "issuer": {
    "name": "Career College"
  },

  "verification_url": "https://careercollege.com/verify/CC-2026-NEXT-000123",

  "status": "valid"
}
```

The exact response structure can be adapted to the existing backend architecture.

---

## 10. Database / Model Considerations

The implementation should fit the existing project architecture.

Possible certificate fields:

```text
certificate_id
student
course
completion_date
issue_date
status

instructor_name
instructor_designation
instructor_signature

authorized_signatory_name
authorized_signatory_designation
authorized_signature
```

If the project already has appropriate Instructor/Profile/Organization models, reuse them rather than creating duplicate data unnecessarily.

For issued certificates, preserve a snapshot of the signature and signatory information.

---

## 11. File Storage

Recommended structure:

```text
media/
├── signatures/
│   ├── instructors/
│   └── authorized/
└── certificates/
    └── signatures/
```

The exact storage structure can follow the existing project conventions.

If using cloud/object storage in production, follow the project's existing storage configuration.

---

## 12. Certificate Eligibility

A certificate should only be issued when the student satisfies the existing course completion requirements.

The backend should validate the student's completion status before creating/issuing a certificate.

Do not allow a certificate to be issued solely by calling the certificate creation endpoint without checking eligibility.

---

## 13. Security Considerations

- Certificate IDs must be unique.
- Verification endpoint should be publicly accessible but should expose only certificate information intended for public verification.
- Do not expose private student/account information.
- Signature upload endpoints should require appropriate admin/instructor authorization.
- Validate uploaded signature files.
- Previously issued certificate data should remain historically consistent.
- Certificate revocation, if supported, should update the verification status without modifying the original issued certificate information.

---

## 14. Frontend vs Backend Responsibility

### Backend

Backend is responsible for:

- Certificate eligibility
- Certificate creation
- Unique Certificate ID
- Completion/issue dates
- Instructor information
- Instructor signature
- Authorized signatory information
- Authorized signature
- Signature snapshot
- Certificate status
- Verification endpoint
- Verification URL
- API response

### Frontend

Frontend is responsible for:

- Certificate visual design
- Displaying student/course information
- Rendering signature images
- Displaying names/designations
- Rendering QR code
- Displaying Certificate ID
- Displaying verification information

---

## 15. Acceptance Criteria

The task is complete when:

- [ ] Instructor signature can be uploaded/configured.
- [ ] Authorized signatory can be configured.
- [ ] Certificate contains a unique Certificate ID.
- [ ] Certificate stores completion and issue dates.
- [ ] Certificate stores instructor signature information.
- [ ] Certificate stores authorized-signatory signature information.
- [ ] Issued certificates preserve signature snapshots.
- [ ] Changing a current instructor/signatory signature does not change old certificates.
- [ ] Certificate API returns all required information.
- [ ] Public verification endpoint works using Certificate ID.
- [ ] Verification URL does not use localhost in production.
- [ ] QR code can use the verification URL.
- [ ] Only eligible/completed students can receive certificates.
- [ ] Signature uploads are properly validated and authorized.

---

## 16. Example Final Certificate Structure

The frontend certificate can display the following:

```text
                         CAREER COLLEGE

                    CERTIFICATE OF COMPLETION

                       This is to certify that

                         MD. AL AMIN

              has successfully completed the

                       NEXT.JS DEVELOPMENT

             course offered by Career College.

------------------------------------------------------------

Course Duration:       12 Weeks
Learning Hours:        120 Hours
Completion Date:       August 01, 2026
Certificate ID:        CC-2026-NEXT-000123

------------------------------------------------------------

   [Instructor Signature]          [Authorized Signature]

          AI Amin                       John Doe
     Course Instructor             Academic Director
       Career College                Career College


                    [ QR CODE ]

              Scan QR Code
             Verify Certificate
```

> Note: The example values above are placeholders. Use the actual course duration, learning hours, authorized signatory, dates, and production domain from the system.

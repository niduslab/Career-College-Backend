# 09) Workflows And Architecture Why

This document explains major backend workflows in two parts:

- How it works (technical sequence)
- Why it is designed this way (architecture reason)

## 1) Authentication workflow

## How it works

1. User registers (`/api/v1/auth/register/`).
2. OTP is generated and sent.
3. User verifies OTP (`/api/v1/auth/otp/verify/`).
4. User logs in and receives auth session/token.
5. Protected endpoints require authentication and often `IsEmailVerified`.

## Why it is used

- OTP verification reduces fake account abuse.
- Email-verified gating improves trust for marketplace actions.
- Separation of register/verify/login keeps auth flow auditable and flexible.

## 2) Profile workflow

## How it works

1. `User` is created with `user_type`.
2. Matching profile model is managed (`LearnerProfile`, `InstructorProfile`, or `PartnerInstitutionProfile`).
3. Education/work history are attached separately.
4. Public profile listing/detail endpoints expose only intended public data.

## Why it is used

- Keeps auth identity (`User`) clean and stable.
- Allows profile fields to evolve per user type without bloating one table.
- Supports role-specific UI and filtering (learners vs instructors vs institutions).

## 3) Course creation workflow

## How it works

1. Verified instructor creates `NidusCourse`.
2. Adds learning objectives, prerequisites, and audience entries.
3. Adds sections with `position`.
4. Adds section contents (lecture/quiz items).

## Why it is used

- Mirrors real course-authoring UX in steps.
- Metadata tables are independent, so frontend can autosave parts safely.
- Instructor ownership checks are straightforward at each step.

## 4) Curriculum ordering workflow (`SectionContent`)

## How it works

1. Any curriculum item gets a `SectionContent` row.
2. `SectionContent.position` defines order within section.
3. Reorder API shifts impacted rows and updates target position atomically.
4. Reindex command repairs gaps if historical data drift exists.

## Why it is used

- Single ordering system for mixed content types.
- Avoids duplicated ordering logic in lecture/quiz models.
- Makes drag-and-drop reorder logic consistent and predictable.

## 5) Lecture + video processing workflow

## How it works

1. Lecture is created (`article` or `video`).
2. For video uploads, new `VideoAsset` is created and set active.
3. A `VideoProcessingJob` is queued.
4. Background task transcodes and writes streaming metadata/status.

## Why it is used

- Upload API stays fast; heavy processing is async.
- Historical video assets can be retained while only one stays active.
- Clear job statuses enable retry, monitoring, and support debugging.

## 6) Quiz workflow

## How it works

1. Quiz is created either:
   - via section contents (`item_type=quiz`), or
   - via direct quiz endpoint.
2. Section placement is represented by `SectionContent`.
3. Instructor adds questions then answers.
4. Validation ensures only one correct answer per question.

## Why it is used

- Supports both curriculum-first UI and direct resource APIs.
- Question/answer split keeps data normalized and extensible.
- DB + serializer constraints protect quiz quality.

## 7) Identity verification workflow

## How it works

1. Instructor creates draft verification request.
2. Completes required document fields and submits.
3. Admin reviews and transitions status (`approved/rejected/action_required`).
4. Approval marks instructor profile as verified.

## Why it is used

- State-machine transitions prevent invalid review jumps.
- Required-field checks enforce submission quality.
- Verification status powers permission checks for course authoring.

## 8) Why service/selector layering exists

## How it works

- Views handle HTTP and permissions.
- Serializers handle validation and persistence shape.
- Services handle domain operations (e.g., reorder, transcoding setup).
- Selectors centralize reusable query logic.

## Why it is used

- Keeps views small and readable.
- Improves testability and reuse of business logic.
- Reduces duplicate query/ordering code across endpoints.

# Backend Architecture Guide

This folder is the design reference for the backend — one document per subsystem, covering data
models, processes, end-to-end workflows, and the reasoning behind each design. Read top to bottom for
a full tour, or jump to the subsystem you're working on.

## Read in this order

1. `01-system-overview.md` — architecture diagram, project layout, request lifecycle, design patterns
2. `02-auth-and-accounts.md` — registration, OTP, JWT, OAuth flows
3. `03-profiles.md` — profile models, auto-creation signal, public/private endpoints
4. `04-courses-and-curriculum.md` — course models, SectionContent ordering, reorder algorithm
5. `05-lectures-and-video-pipeline.md` — video upload, FFmpeg transcoding, HLS pipeline, WatchProgress
6. `06-quizzes.md` — quiz authoring, attempt models, learner submission flow
7. `07-id-verification.md` — identity verification state machine, admin review
8. `08-core-infrastructure.md` — permissions, pagination, Celery tasks, JWT config, logging
9. `09-coding-exercises.md` — coding exercise authoring + Run/Submit execution + Docker sandbox
10. `10-assignments-crud.md` — assignment CRUD + async auto-grading + RubricGrader
11. `11-course-lifecycle.md` — course status state machine, completeness checks, admin review
12. `12-enrollment.md` — enrollment, progress calculation, learner consumption endpoints
13. `13-multi-instructor-collaboration.md` — owner vs co-instructor roles, roster protection, guard_owner utility
14. `14-certificate-system.md` — completion certificate issuance, the frozen signature/signatory snapshot, credential IDs, public verification, revocation, on-the-fly PDF generation
15. `15-review-rating-system.md` — review/rating model, vote atomicity, denormalized catalog fields, access policy
16. `16-notification-system.md` — notification dispatcher, event types, WebSocket delivery
17. `17-messaging-system.md` — learner ↔ instructor messaging, REST + WebSocket protocol, unread semantics
18. `18-partner-institutions.md` — institution verification, expert onboarding, departments, course creation + roster assignment
19. `19-webinars.md` — institution-owned webinars, presenter roles, publish state machine, catalog + registration
20. `20-analytics-dashboard.md` — partner-institution analytics: metrics, institution-scoping, query strategy, revenue/attendance caveats
21. `21-payments.md` — SSLCommerz hosted checkout: order state machine, validation trust model, callback topology
22. `22-scheduled-courses.md` — cohort schedules: schedule state machine, cohort enrollment, drip release, learner gates
23. `23-scheduled-course-lifecycle.md` — scheduled-course user journey paired with the backend flow at each step
24. `24-admin-console-auth.md` — admin console: session login, device/session tracking + remote logout, user management + audit log
25. `25-admin-capabilities.md` — cross-cutting map of **everything** a platform admin can do (console + course review + verification review + category mgmt + platform analytics), the two elevation mechanisms, and where each deep doc lives
32. `32-ai-course-outline-generator.md` — LLM-drafted course outlines: the three-repo topology, the outline-preview endpoint, the spend throttle, and what the caller does with the draft
33. `33-two-step-lecture-authoring.md` — creating a lesson vs. giving it content: the derived "awaiting content" state, the four places an empty lecture must not count, and creator-inclusive content ownership
34. `34-ai-article-lecture-generator.md` — LLM-drafted **article** lecture bodies: the second AI service, why the HTML is rendered server-side and escaped, the never-persist rule, and why video lectures are out of scope
35. `35-ai-quiz-question-generator.md` — LLM-drafted **quiz questions**: the third AI service, the single-correct-answer constraint enforced in three places, why Django assembles the grounding material, the review-and-accept step, and the transactional bulk-create endpoint

> Entries 26–31 exist as files but were never added to this index. See the
> directory listing for `26-discussion-qa.md`, `27-learner-dashboard.md`,
> `28-learning-paths.md`, `29-instructor-dashboard-analytics.md`,
> `30-instructor-students.md`, `31-instructor-revenue.md`.

## Scope

- API base: `/api/v1/`
- Main apps: `authentication`, `courses`, `id_verification`, `messaging`, `notifications`, `realtime`, `webinars`, `analytics`, `payments`, `admin_console`, `core`
- This guide explains:
  - Database model fields (key entities)
  - System processes (request/flow behavior)
  - Workflows (how features execute end-to-end and why that design is used)
  - File responsibilities (which file does what)

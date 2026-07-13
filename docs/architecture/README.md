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
14. `14-certificate-system.md` — completion certificate issuance, on-the-fly PDF generation, public share URLs
15. `15-review-rating-system.md` — review/rating model, vote atomicity, denormalized catalog fields, access policy
16. `16-notification-system.md` — notification dispatcher, event types, WebSocket delivery
17. `17-messaging-system.md` — learner ↔ instructor messaging, REST + WebSocket protocol, unread semantics
18. `18-partner-institutions.md` — institution verification, expert onboarding, departments, course creation + roster assignment
19. `19-webinars.md` — institution-owned webinars, presenter roles, publish state machine, catalog + registration
20. `20-analytics-dashboard.md` — partner-institution analytics: metrics, institution-scoping, query strategy, revenue/attendance caveats
21. `21-payments.md` — SSLCommerz hosted checkout: order state machine, validation trust model, callback topology
22. `22-scheduled-courses.md` — cohort schedules: schedule state machine, cohort enrollment, drip release, learner gates

## Scope

- API base: `/api/v1/`
- Main apps: `authentication`, `courses`, `id_verification`, `messaging`, `notifications`, `realtime`, `webinars`, `analytics`, `core`
- This guide explains:
  - Database model fields (key entities)
  - System processes (request/flow behavior)
  - Workflows (how features execute end-to-end and why that design is used)
  - File responsibilities (which file does what)

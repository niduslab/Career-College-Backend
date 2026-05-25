# Backend Onboarding Guide

This folder is a quick-start map for new backend developers.

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

## Cross-cutting design docs (outside this folder)

- [`../submission-flow.md`](../submission-flow.md) — Coding-exercise Run/Submit pipeline, Docker sandbox, per-language harness contract, redaction layers, failure modes.
- [`../comparison.md`](../comparison.md) — Comparison vs Udemy-style platform; rationale for the one-container-per-submission optimisation and the hardening gaps still on the table.

## Scope

- API base: `/api/v1/`
- Main apps: `authentication`, `courses`, `id_verification`, `core`
- This guide explains:
  - Database model fields (key entities)
  - System processes (request/flow behavior)
  - Workflows (how features execute end-to-end and why that design is used)
  - File responsibilities (which file does what)

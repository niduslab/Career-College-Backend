# Backend Onboarding Guide

This folder is a quick-start map for new backend developers.

## Read in this order

1. `01-system-overview.md`
2. `02-auth-and-accounts.md`
3. `03-profiles.md`
4. `04-courses-and-curriculum.md`
5. `05-lectures-and-video-pipeline.md`
6. `06-quizzes.md`
7. `07-id-verification.md`
8. `08-core-infrastructure.md`
9. `09-workflows-and-architecture-why.md`
10. `10-coding-exercises.md` (Part 1 authoring + Part 2 Run/Submit execution)
11. `11-assignments-crud.md`
12. `12-course-lifecycle.md`
13. `13-enrollment.md` (also covers Phase-1 + Phase-2 learner consumption endpoints)

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

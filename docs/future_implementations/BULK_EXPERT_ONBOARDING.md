# Bulk Expert Onboarding (CSV)

**Status:** Planned — not yet implemented.
**Depends on:** `authentication/services/expert_service.py::provision_expert` (shipped), the department service (shipped), Celery + the expert-credentials email task (shipped).
**SRS:** §7.2 partner-institution expert management.

---

## 1. Problem

`provision_expert()` onboards **one** expert per call (via `POST /api/v1/auth/partner/experts/`). Onboarding a cohort means N manual API calls. This feature adds a **batch** path: upload a CSV (or JSON array), provision every valid row, and return a **per-row result** — created vs skipped vs errored — so the institution sees exactly what happened without an all-or-nothing failure.

Reuses `provision_expert()` per row unchanged — this is orchestration around it, not a rewrite. Each provisioned expert still gets their credentials email asynchronously (that pipeline is already async and idempotent-on-commit).

---

## 2. Endpoint

```
POST /api/v1/auth/partner/experts/bulk/
```
**Auth:** `IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution` (same as single-expert onboarding). Scoped to `request.user.partner_institution_profile`.

**Body:** `multipart/form-data` with a `file` (CSV), **or** `application/json` `{"experts": [ {...}, ... ]}`. Support at least CSV — it's what a non-technical admin exports from a spreadsheet.

**CSV columns** (header row required): `full_name,email,department,headline,bio,specialization`
- `full_name`, `email` — required.
- `department` — matched by **name** (case-insensitive) against the institution's active departments; blank → none; unknown → row error (mirrors `resolve_expert_department`).
- `specialization` — optional, `;`-separated within the cell (CSV commas are column separators), parsed to a list.
- `headline`, `bio` — optional.

---

## 3. Response — per-row result (partial success)

```json
{
  "success": true,
  "message": "Processed 5 rows: 3 created, 1 skipped, 1 failed.",
  "data": {
    "created": 3, "skipped": 1, "failed": 1, "total": 5,
    "rows": [
      {"row": 1, "email": "a@x.com", "status": "created", "expert_id": 12},
      {"row": 2, "email": "b@x.com", "status": "failed", "error": "A user with this email already exists."},
      {"row": 3, "email": "c@x.com", "status": "created", "expert_id": 13},
      {"row": 4, "email": "", "status": "failed", "error": "Full name is required."},
      {"row": 5, "email": "a@x.com", "status": "skipped", "error": "Duplicate email within file."}
    ]
  }
}
```

Design points:
- **Per-row isolation:** each row provisions in its **own** `transaction.atomic()` block. One bad row must not roll back the good ones — so **no single wrapping transaction** over the whole file. This is the key difference from most bulk endpoints.
- `ExpertError` (from `provision_expert`) is caught per row → `status:"failed"` with `exc`'s message and `http_status` recorded (not surfaced as the HTTP status — the request itself is `200`/`207`).
- **In-file duplicate emails** → the second occurrence is `skipped` (don't even attempt; the DB uniqueness check would fail it anyway, but skipping is clearer and avoids a wasted query).
- HTTP status: `200` if all created, or **`207 Multi-Status`** if mixed. (If the project prefers to avoid 207, use `200` with the per-row breakdown — decide and document; 200 is simplest and consistent with the envelope.)

---

## 4. Sync vs async

- **Small files (≤ a cap, e.g. 200 rows): synchronous.** Provision inline, return the per-row report directly. Each credentials email is already enqueued async per row, so the request only does DB writes + validation — fast enough.
- **Large files: Celery.** If the cap is exceeded, either reject with `413`-style guidance (`422` "file exceeds N rows, split it") **or** dispatch a `bulk_provision_experts_task`, return `202 Accepted` + a task id, and expose a status endpoint. **v1 recommendation:** synchronous with a documented row cap (`MAX_BULK_ROWS = 200`); note the Celery path as a future extension. Keeps it "Med" scope.

The credentials emails already ride the existing async pipeline — the worker sends them; the endpoint never blocks on SMTP. **Without a running worker, no emails go out** (same caveat as all auth email — monitor queue depth).

---

## 5. Management command (parallel path)

Add `python manage.py bulk_onboard_experts <institution_id> <csv_path> [--dry-run]` mirroring the existing data-repair command pattern (`--dry-run`, `transaction`, `self.stdout` progress). Useful for ops onboarding a large cohort out-of-band without going through the API. Shares the same row-parsing + per-row provisioning helper as the endpoint — extract it once (`bulk_expert_service.py`), import from both.

`--dry-run` validates every row (email uniqueness, department resolution, required fields) and prints the would-be result **without** creating users or sending emails — wrap in `transaction.atomic()` + `set_rollback(True)`, like `populate_section_content`.

---

## 6. Files to change

1. **`authentication/services/bulk_expert_service.py`** — new.
   - `parse_expert_csv(file) -> list[dict]` (header validation, specialization split, per-row shape; raises a clear error on a malformed/oversized file).
   - `bulk_provision_experts(institution, rows) -> dict` — loops rows, per-row `try/except ExpertError`, in-file dedup, each row in its own atomic block, returns the counts + per-row list. Calls `provision_expert` unchanged.
2. **`authentication/all_views/partner_views.py`** — `InstitutionExpertBulkCreateView(APIView)`, `IsVerifiedPartnerInstitution`. Parse file/JSON → serializer/shape check → call service → envelope. `try/except` around parsing → 400 for a bad file; the per-row failures are data, not errors.
3. **`authentication/all_views/__init__.py`** + **`authentication/views.py`** — re-export the view.
4. **`authentication/urls.py`** — `partner/experts/bulk/` **before** `partner/experts/<int:expert_id>/` (literal-before-parameter ordering).
5. **`authentication/management/commands/bulk_onboard_experts.py`** — new command reusing the service.
6. **`authentication/serializers.py`** — optional `BulkExpertRowSerializer` for JSON-body validation (CSV parsing stays in the service).
7. **`authentication/tests/` (or `authentication` test module)** — new suite.
8. **Docs** — `CLAUDE.md` (Experts subsection), `docs/architecture/18-partner-institutions.md`, `docs/api-testing/postman-partner-institution.md`.

**No migration** — no new model; reuses `User` / `InstructorProfile`.

---

## 7. Edge cases & rules

- **Partial success is the whole point:** never wrap the file in one transaction. Good rows persist even when others fail.
- **In-file duplicate email → skip;** DB-existing email → fail (from `provision_expert`'s existing check, incl. soft-deleted users via `all_with_deleted()`).
- **Department by name, scoped to the institution:** unknown/foreign name → row error, not a silent none. Resolve names to ids once up front (one query for the institution's active departments) to avoid a per-row department lookup.
- **Email normalization:** lower/trim per row (same as `provision_expert`); dedup on the normalized value.
- **Malformed CSV / missing headers / wrong encoding** → whole-request `400` with a clear message (this is a real error, not per-row data).
- **Row cap** (`MAX_BULK_ROWS`): over the cap → `422` with guidance, or route to Celery (future). Prevents a giant upload from blocking a worker/request.
- **Credentials emails:** one per created expert, async, deliberately excluded from any bulk notification payload (no plaintext passwords persisted — same as single onboarding).
- **Idempotency:** re-uploading the same file re-skips/re-fails existing emails → no duplicate accounts.
- **403 vs 404:** endpoint takes no resource id → permission failures are 403.

---

## 8. Build order

1. `parse_expert_csv` + `bulk_provision_experts` service with per-row isolation + in-file dedup + department-name resolution.
2. Endpoint (CSV multipart) + per-row report + row cap.
3. JSON-array body support.
4. Management command (`--dry-run`).
5. (Later) Celery path for large files + a status endpoint.

## 9. Future extensions

- Async Celery processing + progress polling for large cohorts.
- Downloadable result CSV (append a `status`/`error` column to the uploaded file).
- Template CSV download endpoint so admins start from the right headers.
- Bulk **deactivate** (roster offboarding) via the same shape.

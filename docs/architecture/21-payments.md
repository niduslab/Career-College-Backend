# 21 — Payments (SSLCommerz)

Learner-pays-for-**course-or-webinar** via the SSLCommerz hosted checkout
(gwprocess v4), sandbox-first. A validated payment produces a `PAID`
enrollment (course) or an active `WebinarRegistration` (webinar). Institution
wallet / payout / refund automation and the analytics `revenue.enabled` flip
are **Phase 2** — see the end of this doc.

## Decisions

- **Currency:** BDT only (`Order.currency` default `'BDT'`; the sandbox is
  BDT-native). `NidusCourse.price` is treated as BDT.
- **Checkout model:** hosted redirect. The backend opens a gateway session and
  hands the frontend a `GatewayPageURL`; the browser completes payment on
  SSLCommerz's page and is redirected back.
- **Trust model:** redirect and IPN bodies are **never** trusted for payment
  state. The only path to `PAID` is `finalize_payment`, which re-queries the
  SSLCommerz Validation API with the `val_id` and verifies every field against
  our own order row.

## Data model (`payments/all_models/order_models.py`)

`Order` — one row per gateway session:

| Field | Meaning |
|---|---|
| `user` | `PROTECT` FK |
| `course`, `webinar` | nullable `PROTECT` FKs — **exactly one set** (`chk_order_exactly_one_target`); `order.item` / `order.item_type` resolve the target |
| `amount` | **snapshot of the target's `price` at checkout** — later price edits never affect validation |
| `currency` | `'BDT'` |
| `tran_id` | our unique gateway transaction id, `CC` + 24 hex (SSLCommerz max 30) |
| `status` | `initiated → processing → paid \| failed \| cancelled` |
| `val_id` | SSLCommerz validation id, recorded on success |
| `gateway_payload` | raw session + validation responses (audit); never serialized to clients |
| `paid_at` | set on finalize |

Constraints: partial uniques `(user, course) WHERE status='paid'` and
`(user, webinar) WHERE status='paid'` — at most one successful purchase per
(user, target) pair.
Re-checkout creates a **new** order and flips stale `initiated`/`processing`
rows to `cancelled` (audit preserved, one live session at a time).

**PAID is terminal.** `mark_order_failed` / `mark_order_cancelled` no-op on a
PAID row — a late fail redirect can never clobber a completed payment.

## Services

- `payments/services/sslcommerz_service.py` — pure gateway I/O.
  `initiate_session(order, user)` (POST `/gwprocess/v4/api.php`) and
  `validate_transaction(val_id)` (GET the validation API). Network failure →
  `PaymentError(503)`. Base URL switches sandbox/live via `SSLCOMMERZ_SANDBOX`.
- `payments/services/order_service.py` — the lifecycle:
  - `create_checkout(user, course=None, webinar=None)` — exactly one target.
    Course guards: published, `price > 0`, not actively enrolled, not already
    purchased. Webinar guards: published, `price > 0`, not started yet, not
    actively registered, capacity not full, not already purchased. Cancels
    stale pendings, creates the order, opens the session. The gateway network
    call runs **outside** the DB transaction so a hung gateway can't hold row
    locks.
  - `finalize_payment(tran_id, val_id)` — the single trusted path to PAID.
    Idempotent: short-circuits pre-network if already PAID, re-checks under
    `select_for_update` after validation. Verifies `status ∈ (VALID,
    VALIDATED)`, `tran_id`, `amount` (Decimal-exact), `currency`, `store_id`.
    Any mismatch → order FAILED (persisted **outside** the rolled-back
    transaction via the internal `_ValidationRejected` unwind) +
    `PaymentError(422)`. Success → PAID + access grant in the same
    transaction (`_grant_access`: course → `enroll_learner(...,
    enrollment_type=PAID, allow_unpublished=True)`; webinar →
    `register_for_webinar(..., via_payment=True)`) + `PAYMENT_SUCCESSFUL`
    notification on commit. The finalize lock uses
    `select_for_update(of=('self',))` — course/webinar are nullable FKs
    (LEFT JOIN) and Postgres can't `FOR UPDATE` the nullable side.
  - `mark_order_failed` / `mark_order_cancelled` — terminal transitions from
    the gateway callbacks; PAID never clobbered; FAILED dispatches
    `PAYMENT_FAILED`.
- `PaymentError(message, http_status)` in `payments/services/exceptions.py` —
  same shape as `WebinarError`; views return `exc.http_status` + envelope.

## Endpoints (`/api/v1/payments/`)

| Method | Path | View | Auth | Purpose |
|---|---|---|---|---|
| POST | `checkout/` | `PaymentCheckoutView` | learner (`IsEmailVerified`, `IsLearnerUser`) | body: exactly one of `{course_slug}` / `{webinar_slug}` → `{gateway_url, order_id, tran_id, item_type, amount, currency}` (201) |
| POST | `ipn/` | `PaymentIPNView` | none (`AllowAny`, no auth classes) | server-to-server notification; 200 on handled paths, 500 on transient crash so the gateway retries |
| GET/POST | `success/` | `PaymentSuccessView` | none | runs `finalize_payment`, 302 → `FRONTEND_URL + FRONTEND_PAYMENT_SUCCESS_PATH?tran_id=`; validation failure → fail path |
| GET/POST | `fail/` | `PaymentFailView` | none | marks failed, 302 → fail path |
| GET/POST | `cancel/` | `PaymentCancelView` | none | marks cancelled, 302 → cancel path |
| GET | `orders/` | `OrderListView` | learner | own orders, `?status=` filter, standard pagination envelope |
| GET | `orders/<int:pk>/` | `OrderDetailView` | learner | own order; cross-user → indistinguishable 404 (numeric-ID policy) |

Callback views set `authentication_classes = []` — the gateway posts without
our JWT. That is safe because the project is JWT-only (DRF `APIView`s are
CSRF-exempt without `SessionAuthentication`) and payment truth comes from the
validation API, not the request body.

The **success redirect is the primary finalize path** — a local sandbox has no
publicly reachable IPN URL. The IPN is the server-to-server safety net for
production; both funnel into the idempotent `finalize_payment`.

## Free-enroll / free-register gates

`CourseEnrollView` (`courses/all_views/enrollment_views.py`) rejects
`course.price > 0` with 422 **unless** the caller has a PAID order for that
course — in which case it enrolls with `enrollment_type=PAID` (covers
unenroll → re-enroll with no second charge). `enroll_learner` gained
keyword-only `enrollment_type` (default FREE — all existing callers unchanged)
and `allow_unpublished` (finalize-only); paid reactivation upgrades
`enrollment_type` but a free call never downgrades a paid one.

`register_for_webinar` (`webinars/services/registration_service.py`) mirrors
the same gate: `webinar.price > 0` without a PAID order → `WebinarError(422)`
pointing at checkout; with one, direct registration succeeds (cancel →
re-register, no second charge). The finalize path calls with
`via_payment=True`, which bypasses the price gate, the published check, and
the capacity check (money moved — an over-capacity paid registration is
honored and logged, never refused).

## Edge-case policy (authoritative)

| Case | Behavior |
|---|---|
| Double IPN | idempotent finalize → no-op, 200 |
| Success redirect before IPN | redirect finalizes; later IPN no-ops |
| `VALIDATED` on re-validation | accepted same as `VALID` |
| Course unpublished / webinar archived mid-payment | access still granted (`allow_unpublished` / `via_payment`) |
| Webinar fills up mid-payment | paid registration honored over capacity, `logger.warning` overshoot |
| Price changed after checkout | snapshot `order.amount` governs; old price honored |
| Concurrent double checkout | newest session wins; stale pendings cancelled; a cancelled-but-paid session still finalizes if no PAID order exists |
| True double payment | second order FAILED + `gateway_payload.requires_refund=True` + `logger.critical`; manual refund via merchant panel |
| Unenroll → re-enroll paid course (or cancel → re-register paid webinar) | free reactivation only with a PAID order; else 422 → checkout |
| Tampered amount/currency/store | order FAILED, 422, no access granted |
| Gateway down | 503; checkout order FAILED; IPN returns 500 so SSLCommerz retries |
| fail/cancel callback on PAID order | no-op (PAID terminal) |
| Checkout on a free course/webinar | 422 pointing at the enroll/register endpoint |
| Checkout on a webinar that already started | 422 |
| Learner pays, no callback ever arrives | reaper reconciles via gateway query → paid or (after 24h) failed |
| Unsigned / forged fail/cancel callback | ignored; order left for the reaper |
| Concurrent double-pay hits the unique constraint | `IntegrityError` caught → recorded as `requires_refund`, access still granted |
| Missing `store_id` in validation response | skipped in sandbox; **fails validation in production** |
| Gateway unreachable during IPN validation | IPN returns 503 → SSLCommerz retries |

## Notifications

`PAYMENT_SUCCESSFUL` / `PAYMENT_FAILED` (category `COURSE_ACTIVITY`), deduped
per `tran_id`, dispatched via `transaction.on_commit`, with email templates
`payment_successful.html` / `payment_failed.html`. Standard 4-edit wiring.

## Reliability & security hardening

- **Reconciliation reaper** (`payments/tasks.py` → `reap_stale_processing_orders_task`, Celery beat every 15 min). A learner can pay and never trigger a callback (tab closed post-payment, IPN unreachable, browser died mid-redirect), stranding the order in `processing` with money taken and no access. The reaper queries the gateway by `tran_id` (`query_transaction`, the merchant-transaction-query API): VALID → finalize; FAILED → mark failed; still-pending past `abandon_hours` (default 24h) → mark failed. Gateway-unreachable is swallowed so the next run retries. This is the safety net behind both callback paths — no order stays pending forever.
- **Callback signature verification** (`verify_callback_signature`). The success path re-hits the validation API (authoritative), but the fail/cancel callbacks trust the body's status — so those paths require a valid SSLCommerz `verify_sign`/`verify_key` MD5 hash before acting. An unsigned or forged fail/cancel callback is ignored (order left for the reaper), closing the griefing vector where anyone who learns a `tran_id` could force an in-flight order to failed/cancelled.
- **store_id fail-closed in production.** The validation-response `store_id` check is skipped only when `SSLCOMMERZ_SANDBOX=True` (sandbox EasyCheckout omits the field). With `SSLCOMMERZ_SANDBOX=False`, a missing `store_id` fails validation — a response can't dodge the store-ownership check by simply not carrying it.
- **Race-safe duplicate handling.** The duplicate-payment check (sibling PAID row) is backed by a `try/except IntegrityError` around the PAID save: if two sessions for the same `(user, target)` finalize concurrently and both pass the visibility check, the partial-unique constraint rejects the second, and that path re-locks and routes into the same `requires_refund` handling instead of surfacing a raw 500. Both the visible-duplicate and the race land in `_record_duplicate_payment`.
- **IPN transient vs permanent.** A 5xx-class `PaymentError` (gateway unreachable during validation) returns 503 from the IPN so SSLCommerz retries; a 4xx (validation rejected / unknown order) is acked 200 so it stops.

## Phase 2 (not built)

- Institution wallet + transaction ledger and payout requests (SRS §7.6.2/7.6.3).
- Automated refunds (the `requires_refund` flag is the manual hook today).
- Flip `analytics` `revenue.enabled=True`, computing gross from
  `Order(status='paid')`; per-course/per-expert revenue columns.
- Coupons / promotions / bundles.

Tests: `payments/all_tests/` (checkout guards, finalize verification +
idempotency + tamper rejection, callback behavior, order endpoints, webinar
checkout/finalize/gate in `test_webinar_payments.py`) with the gateway fully
mocked; paid-course gate cases in `courses/all_tests/test_enrollment.py`.
Manual walkthrough: `docs/api-testing/postman-payments.md`.

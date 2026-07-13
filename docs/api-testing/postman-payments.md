# Postman guide — Payments (SSLCommerz sandbox)

Manual walkthrough for the payment flow — **courses and webinars**.
Architecture: `docs/architecture/21-payments.md`.

## Environment variables

| Variable | Example | Notes |
|---|---|---|
| `base_url` | `http://localhost:8000/api/v1` | |
| `learner_token` | `Bearer eyJ...` | verified learner JWT (the buyer) |
| `learner2_token` | `Bearer eyJ...` | a second verified learner — isolation + gate checks |
| `instructor_token` | `Bearer eyJ...` | any instructor JWT — negative authz test |
| `institution_token` | `Bearer eyJ...` | verified partner institution — webinar seeding |
| `paid_course_slug` | `advanced-django` | a **published** course with `price > 0` |
| `free_course_slug` | `intro-python` | a published course with `price = 0` |
| `paid_scheduled_course_slug` | `cohort-bootcamp` | a **published, `delivery_mode=scheduled`** course with `price > 0` |
| `schedule_id` | `1` | a `CourseSchedule` of `paid_scheduled_course_slug` with `status=scheduled` and an open enrollment window |
| `paid_webinar_slug` | `scaling-django-live` | a **published, future-scheduled** webinar with `price > 0` |
| `free_webinar_slug` | `career-qna` | a published webinar with `price = 0` |

**Backend `.env` preconditions:** real sandbox credentials from
https://developer.sslcommerz.com (register a sandbox store) in
`SSLCOMMERZ_STORE_ID` / `SSLCOMMERZ_STORE_PASSWORD`, `SSLCOMMERZ_SANDBOX=True`,
`BACKEND_URL=http://localhost:8000`.

> **Local-testing note:** the IPN needs a publicly reachable URL, so on
> localhost it never fires. That's fine — the **success redirect** runs the
> same `finalize_payment`, so the flow completes end-to-end locally. In
> production the IPN is the safety net for a browser that dies mid-redirect.

### Seeding a paid webinar

A paid webinar needs the full authoring flow first (see
`postman-webinars.md` for details):

1. Institution creates it with a price:
   `POST {{base_url}}/webinars/create/` (`institution_token`) — include
   `"price": "15.00"`, a **future** `scheduled_at`, `duration_minutes`,
   `meeting_url`. Optionally `"max_capacity": 2` for the capacity checks.
2. Institution assigns a host expert: `POST {{base_url}}/webinars/<pk>/host/`.
3. The **host expert** publishes: `POST {{base_url}}/webinars/<pk>/publish/`.
4. Confirm it's live: `GET {{base_url}}/webinars/catalog/{{paid_webinar_slug}}/`
   shows the price. (`meeting_url` is absent from catalog — registrants only.)

---

## Group 1: Course checkout

### 1.1 Open a checkout session

```
POST {{base_url}}/payments/checkout/
Authorization: {{learner_token}}
Content-Type: application/json

{"course_slug": "{{paid_course_slug}}"}
```

**201:**

```json
{"success": true, "message": "Checkout session created.", "data": {
  "gateway_url": "https://sandbox.sslcommerz.com/gwprocess/v4/gw.php?Q=PAY&SESSIONKEY=...",
  "order_id": 1,
  "tran_id": "CC9F2A...",
  "item_type": "course",
  "schedule_id": null,
  "amount": "49.00",
  "currency": "BDT"
}}
```

**Negative checks:**

| Request | Expect |
|---|---|
| No auth | 401 |
| `instructor_token` | 403 (learner-only) |
| Empty body / neither slug | 400 with `errors` on both slug fields |
| **Both** `course_slug` and `webinar_slug` | 400 — exactly one target |
| `{"course_slug": "{{free_course_slug}}"}` | 422 "This course is free. Use the enroll endpoint instead." |
| Already actively enrolled | 422 "You are already enrolled in this course." |
| Already purchased (`paid` order exists) | 422 pointing at the enroll endpoint |
| Unknown / unpublished slug | 404 |
| `{"webinar_slug": "...", "schedule_id": 1}` | 400 "schedule_id is only valid for course checkout." |

**Re-checkout:** POST 1.1 twice without paying. Second response has a **new**
`tran_id`; the first order flips to `cancelled` (verify in 5.1). Only the
newest gateway session is live.

### 1.2 Pay on the gateway page

Open `gateway_url` in a browser. Sandbox test card: **4111 1111 1111 1111**,
any future expiry, any CVV, OTP `111111` (the sandbox accepts the displayed
OTP). Complete payment → browser lands on the backend `success/` callback →
302 to `{FRONTEND_URL}/payment/success?tran_id=...`.

### 1.3 Verify the outcome

1. **Order paid** — `GET {{base_url}}/payments/orders/` → the row for your
   `tran_id` has `"status": "paid"`, `paid_at` set, `item_type: "course"`.
2. **Enrollment granted** — `GET {{base_url}}/courses/my-courses/` → course
   present, enrollment `enrollment_type: "paid"`.
3. **Notification** — `GET {{base_url}}/notifications/` → a
   `payment.successful` row whose `data` carries `item_type`, `item_slug`,
   `tran_id`.

### 1.4 Free-enroll gate

```
POST {{base_url}}/courses/{{paid_course_slug}}/enroll/
Authorization: {{learner2_token}}   (has NOT paid)
```

**422** — "This is a paid course. Complete payment via the checkout endpoint
to enroll."

Then, as the **paying** learner:
`POST {{base_url}}/courses/{{paid_course_slug}}/unenroll/` →
`POST .../enroll/` again → **201**, enrollment reactivated with
`enrollment_type: "paid"`, and `GET /payments/orders/` still shows exactly
**one** order — no second charge.

---

## Group 1B: Cohort (scheduled course) checkout

Paying for a seat in a specific `CourseSchedule` cohort instead of self-paced
access. Same course, an extra `schedule_id` in the checkout body. See
`docs/api-testing/postman-schedules.md` for creating/activating a schedule
first — it must be `status: "scheduled"` with an open enrollment window
(`enrollment_opens_at <= now <= enrollment_closes_at`) before checkout will
accept it.

### 1B.1 Open a cohort checkout session

```
POST {{base_url}}/payments/checkout/
Authorization: {{learner_token}}
Content-Type: application/json

{"course_slug": "{{paid_scheduled_course_slug}}", "schedule_id": {{schedule_id}}}
```

**201:**

```json
{"success": true, "message": "Checkout session created.", "data": {
  "gateway_url": "https://sandbox.sslcommerz.com/gwprocess/v4/gw.php?Q=PAY&SESSIONKEY=...",
  "order_id": 3,
  "tran_id": "CC7B1D...",
  "item_type": "course",
  "schedule_id": 1,
  "amount": "49.99",
  "currency": "BDT"
}}
```

**Negative checks:**

| Request | Expect |
|---|---|
| `schedule_id` belonging to a **different** course | 404 "Schedule not found for this course." |
| Unknown `schedule_id` | 404 |
| Schedule `status` not `scheduled` (still `draft`, or already `ongoing`/`completed`/`archived`) | 422 "Enrollment for this cohort is not open." |
| Before `enrollment_opens_at` or after `enrollment_closes_at` | 422 "Enrollment for this cohort is not open." |
| Cohort at `max_seats` (active enrollments for that schedule) | 422 "This cohort is full." |
| Already actively enrolled **in that schedule** | 422 "You are already enrolled in this course." (a self-paced enrollment for the same course does **not** block a cohort checkout, and vice versa) |
| Already purchased that schedule (`paid` order exists for `(user, schedule)`) | 422 pointing at the enroll endpoint |

> **Checkout-time seat check is advisory only.** It runs without a row lock
> (the gateway round-trip can take a while). The authoritative,
> lock-protected seat check runs again at payment finalize
> (`enroll_learner`'s `_assert_schedule_enrollable`) — same pattern as the
> free cohort-enroll path in `postman-schedules.md`. A seat can still fill
> between checkout and completed payment; finalize is the source of truth.

### 1B.2 Pay and verify

Same as 1.2/1.3 — pay on the gateway page, then:

1. **Order paid** — `GET {{base_url}}/payments/orders/` → the row has
   `schedule_id` matching what you sent, `item_type: "course"`.
2. **Cohort enrollment granted** — `GET {{base_url}}/courses/my-courses/` →
   enrollment `enrollment_type: "paid"`, tied to the schedule (verify via
   admin or the schedule's roster endpoint in `postman-schedules.md`) — not
   a self-paced row.
3. Repeat 1.1 (no `schedule_id`) for the **same course** as the **same
   learner** → separate **self-paced** checkout succeeds independently;
   `GET /payments/orders/` shows **two** paid orders for the same course —
   one `schedule_id: null`, one with the cohort's id. Confirms the
   self-paced/cohort partial-unique split (`uniq_paid_order_user_course_selfpaced`
   / `uniq_paid_order_user_schedule`) doesn't collide.

---

## Group 2: Webinar checkout

Same flow, webinar target. The checkout body takes **exactly one** of
`course_slug` / `webinar_slug`.

### 2.1 Open a checkout session

```
POST {{base_url}}/payments/checkout/
Authorization: {{learner_token}}
Content-Type: application/json

{"webinar_slug": "{{paid_webinar_slug}}"}
```

**201:**

```json
{"success": true, "message": "Checkout session created.", "data": {
  "gateway_url": "https://sandbox.sslcommerz.com/gwprocess/v4/gw.php?Q=PAY&SESSIONKEY=...",
  "order_id": 2,
  "tran_id": "CC41BC...",
  "item_type": "webinar",
  "amount": "15.00",
  "currency": "BDT"
}}
```

**Negative checks:**

| Request | Expect |
|---|---|
| `{"webinar_slug": "{{free_webinar_slug}}"}` | 422 "This webinar is free. Use the register endpoint instead." |
| Already actively registered | 422 "You are already registered for this webinar." |
| Already purchased | 422 pointing at the register endpoint |
| Webinar already started (`scheduled_at` in the past) | 422 "This webinar has already started." |
| Capacity full (`max_capacity` reached by active registrations) | 422 "This webinar has reached its capacity." |
| Unknown / draft / archived slug | 404 |

> **Capacity race policy:** capacity is checked at **checkout**. A payment
> that lands after the webinar fills (two learners check out the last seat
> concurrently) is still honored — the registration is created over capacity
> and a `logger.warning` overshoot is recorded. Money moved → access granted,
> never refused.

### 2.2 Pay on the gateway page

Same as 1.2 — card `4111 1111 1111 1111`, complete payment, 302 to the
frontend success page with the webinar order's `tran_id`.

### 2.3 Verify the outcome

1. **Order paid** — `GET {{base_url}}/payments/orders/` → the row has
   `"status": "paid"`, `item_type: "webinar"`, `webinar_slug` /
   `webinar_title` set and `course_slug` / `course_title` **null**.
2. **Registration granted** — `GET {{base_url}}/webinars/my-webinars/` → the
   webinar appears. `GET {{base_url}}/webinars/my-webinars/{{paid_webinar_slug}}/`
   now reveals `meeting_url` (registrants only).
3. **Notification** — `payment.successful` row with `item_type: "webinar"`.

### 2.4 Register gate

```
POST {{base_url}}/webinars/{{paid_webinar_slug}}/register/
Authorization: {{learner2_token}}   (has NOT paid)
```

**422** — "This is a paid webinar. Complete payment via the checkout endpoint
to register." No registration row is created.

Free webinar sanity: `POST {{base_url}}/webinars/{{free_webinar_slug}}/register/`
→ **201** unchanged — the gate only bites when `price > 0`.

> There is currently **no cancel-registration endpoint**, so the
> "re-register free with a paid order" path (mirror of 1.4) can only be
> exercised by deactivating the `WebinarRegistration` row in Django admin,
> then re-POSTing register — expect **201** reactivation and still exactly
> one paid order.

---

## Group 3: Failure paths

### 3.1 Cancel at the gateway

Open a fresh checkout (course or webinar), click **Cancel** on the gateway
page → 302 to `/payment/cancel?tran_id=...`; the order shows
`"status": "cancelled"`. No enrollment/registration is created.

### 3.2 Failed card

Use the sandbox **fail** card (35xx series shown on the sandbox page) → 302 to
`/payment/fail?tran_id=...`; order `failed`; a `payment.failed` notification
exists (its body names the course/webinar and points back to its page).

### 3.3 PAID is terminal

After a successful payment, manually hit the fail callback with the same id:

```
POST {{base_url}}/payments/fail/
Content-Type: application/x-www-form-urlencoded

tran_id=<the PAID order's tran_id>&status=FAILED
```

**302** — and the order **stays `paid`**. A late/forged fail callback can
never clobber a completed payment.

### 3.4 Callback replay is idempotent

Re-POST the success callback (`tran_id` + `val_id` from a completed payment)
to `{{base_url}}/payments/success/` → 302 to the success page; still exactly
one enrollment/registration and one `paid` order. Same for a duplicate IPN.

### 3.5 Tamper resistance (code-level)

Amount/currency/store mismatches are covered by
`payments/all_tests/test_finalize.py` — the validation response is compared
field-by-field against the order snapshot; any mismatch fails the order and
never grants access. Not reproducible via Postman (the gateway signs real
sessions); listed here so the manual tester knows it's covered.

---

## Group 4: Order history

### 4.1 List own orders

```
GET {{base_url}}/payments/orders/
Authorization: {{learner_token}}
```

**200** — standard paginated envelope. Each row:

```json
{
  "id": 2,
  "item_type": "webinar",
  "course_title": null,
  "course_slug": null,
  "schedule_id": null,
  "webinar_title": "Scaling Django Live",
  "webinar_slug": "scaling-django-live",
  "amount": "15.00",
  "currency": "BDT",
  "status": "paid",
  "tran_id": "CC41BC...",
  "paid_at": "2026-07-05T10:12:00Z",
  "created_at": "2026-07-05T10:09:31Z"
}
```

`schedule_id` is non-null only on a cohort-seat course order (Group 1B);
null for self-paced course orders and always null for webinar orders.

**Checks:**
- Exactly one of the `course_*` / `webinar_*` pairs is non-null per row,
  matching `item_type`.
- `gateway_payload` and `val_id` are **never** present.
- `?status=paid` filters; `?status=garbage` → **400** listing valid values
  (`initiated, processing, paid, failed, cancelled`).
- `learner2_token` sees only their own rows — the buyer's orders never leak.

### 4.2 Order detail isolation

`GET {{base_url}}/payments/orders/<id>/` — own order → 200; **another
learner's order id → 404** (indistinguishable from missing — numeric-ID
policy). Unknown id → 404.

---

## Quick matrix — expected status by scenario

| # | Scenario | Endpoint | Expect |
|---|---|---|---|
| 1 | Checkout paid course, happy path | `POST payments/checkout/` | 201 + `gateway_url` |
| 2 | Checkout paid webinar, happy path | `POST payments/checkout/` | 201 + `item_type: webinar` |
| 3 | Checkout free course/webinar | `POST payments/checkout/` | 422 |
| 4 | Checkout with both slugs | `POST payments/checkout/` | 400 |
| 5 | Checkout while enrolled/registered | `POST payments/checkout/` | 422 |
| 6 | Checkout already-purchased target | `POST payments/checkout/` | 422 |
| 7 | Checkout started webinar | `POST payments/checkout/` | 422 |
| 8 | Checkout full webinar | `POST payments/checkout/` | 422 |
| 9 | Free-enroll paid course, no purchase | `POST courses/{slug}/enroll/` | 422 |
| 10 | Free-enroll paid course, has paid order | `POST courses/{slug}/enroll/` | 201 (`paid`) |
| 11 | Register paid webinar, no purchase | `POST webinars/{slug}/register/` | 422 |
| 12 | Register free webinar | `POST webinars/{slug}/register/` | 201 |
| 13 | Fail callback on PAID order | `POST payments/fail/` | 302, order stays `paid` |
| 14 | Replayed success callback | `POST payments/success/` | 302, no duplicate access |
| 15 | Cross-user order detail | `GET payments/orders/<id>/` | 404 |
| 16 | Invalid `?status=` filter | `GET payments/orders/` | 400 |
| 17 | Checkout course + `schedule_id`, cohort open | `POST payments/checkout/` | 201 + `schedule_id` set |
| 18 | Checkout webinar + `schedule_id` | `POST payments/checkout/` | 400 |
| 19 | Checkout `schedule_id` from a different course | `POST payments/checkout/` | 404 |
| 20 | Checkout cohort closed/not-yet-open/full | `POST payments/checkout/` | 422 |
| 21 | Same course, one self-paced + one cohort paid order | `GET payments/orders/` | both rows present, distinct `schedule_id` |

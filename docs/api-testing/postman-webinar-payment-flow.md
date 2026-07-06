# Postman guide — Webinar Payment Flow (End-to-End)

Full chain: **webinar creation → host assignment → publish → learner pays →
registration confirmed.** For payment-only edge cases (tamper, idempotency,
callback failures) see `postman-payments.md` Group 2/3 — this doc is the
happy-path chain plus the checks specific to each stage.

Related: `docs/architecture/21-payments.md`, `docs/architecture/19-webinars.md`,
`PAYMENT_WORKFLOW.md`.

## Environment variables

| Variable | Example | Notes |
|---|---|---|
| `base_url` | `http://localhost:8000/api/v1` | |
| `institution_token` | `Bearer eyJ...` | verified partner institution JWT |
| `host_expert_token` | `Bearer eyJ...` | active affiliated expert of that institution |
| `learner_token` | `Bearer eyJ...` | verified learner JWT (the buyer) |
| `learner2_token` | `Bearer eyJ...` | second learner — gate/isolation checks |
| `webinar_pk` | — | numeric id, captured from step 1's response |
| `webinar_slug` | — | slug, captured from step 1's response |

**Backend `.env` preconditions:** `SSLCOMMERZ_STORE_ID` /
`SSLCOMMERZ_STORE_PASSWORD` set to real sandbox credentials,
`SSLCOMMERZ_SANDBOX=True`, `BACKEND_URL=http://localhost:8000`.

> **Local IPN note:** localhost has no public IPN URL, so the IPN never
> fires locally — the **success redirect** does the real finalize work.
> That's expected; it's the production safety net, not the local path.

---

## Stage 1 — Institution creates the webinar

```
POST {{base_url}}/webinars/create/
Authorization: {{institution_token}}
Content-Type: application/json

{
  "title": "Scaling Django for High Traffic",
  "description": "Live session on Django performance at scale.",
  "scheduled_at": "2026-08-01T15:00:00Z",
  "timezone": "Asia/Dhaka",
  "duration_minutes": 60,
  "max_capacity": 2,
  "price": "15.00",
  "meeting_provider": "zoom",
  "meeting_url": "https://zoom.us/j/example"
}
```

**201** — status is `draft`. Capture `id` → `{{webinar_pk}}` and `slug` →
`{{webinar_slug}}`.

**Checks:**
- `scheduled_at` must be in the future — a past date should be rejected by the create serializer (if not caught here, it's caught at publish, see Stage 3).
- `price > 0` is what makes this a **paid** webinar — set `"price": "0.00"` on a second webinar later if you want a free-webinar control case.
- No auth / learner token / instructor token → 403 (institution-only).
- `max_capacity: 2` is deliberate — small enough to exercise the capacity-limit checks in Stage 4 without creating many test learners.

---

## Stage 2 — Institution assigns the host expert

A webinar cannot publish without a host — the host is the only actor who
may publish it.

```
POST {{base_url}}/webinars/{{webinar_pk}}/host/
Authorization: {{institution_token}}
Content-Type: application/json

{"expert_user_id": <host expert's numeric user id>}
```

**200** — `WebinarSerializer` response shows `host_expert` populated.

**Checks:**
- `expert_user_id` missing → 400.
- An expert **not** affiliated with this institution, or not `active` → rejected (`WebinarError`, 422) — try this with a foreign expert id to confirm cross-institution hosts are impossible.
- Institution not owning this webinar (wrong `institution_token`) → 404 (numeric pk policy).

---

## Stage 3 — Host publishes the webinar

```
POST {{base_url}}/webinars/{{webinar_pk}}/publish/
Authorization: {{host_expert_token}}
```

**200** — `status: "published"`, `is_published: true`, `published_at` set.

**Checks:**
- Publish attempted by the **institution** (not the host) → 404 (this endpoint is host-scoped, `host_expert=request.user`).
- Publish attempted before a host is assigned → 422 (completeness check fails).
- Publish with `scheduled_at` in the past, or missing `meeting_url`/`duration_minutes` → 422 with the specific missing field named.
- Confirm it's live: `GET {{base_url}}/webinars/catalog/{{webinar_slug}}/` (`AllowAny`) — price visible, **`meeting_url` absent** (registrants-only field, correctly hidden pre-purchase).

---

## Stage 4 — Learner opens checkout

```
POST {{base_url}}/payments/checkout/
Authorization: {{learner_token}}
Content-Type: application/json

{"webinar_slug": "{{webinar_slug}}"}
```

**201:**

```json
{"success": true, "message": "Checkout session created.", "data": {
  "gateway_url": "https://sandbox.sslcommerz.com/gwprocess/v4/gw.php?Q=PAY&SESSIONKEY=...",
  "order_id": 1,
  "tran_id": "CC...",
  "item_type": "webinar",
  "amount": "15.00",
  "currency": "BDT"
}}
```

**Checks before paying — do these now, they're cheap and easy to forget once you've moved on:**

| Attempt | Expect |
|---|---|
| Checkout the **draft** webinar (before Stage 3) | 404 — unpublished webinars aren't checkout targets |
| Checkout with `price: "0.00"` webinar | 422 "This webinar is free. Use the register endpoint instead." |
| Checkout after `scheduled_at` has passed | 422 "This webinar has already started." |
| `{{learner_token}}` checks out twice without paying | second call gets a **new** `tran_id`; first order flips to `cancelled` |
| Fill `max_capacity` (2) with active registrations first, then checkout a 3rd learner | 422 "This webinar has reached its capacity." |
| `instructor_token` / no auth | 403 / 401 |

---

## Stage 5 — Learner pays

Open `gateway_url` in a browser. Sandbox card **4111 1111 1111 1111**, any
future expiry, any CVV, OTP `111111`. Complete payment → browser lands on
the backend `success/` callback → 302 to
`{FRONTEND_URL}/payment/success?tran_id=...`.

**Checks:**
- Use the sandbox **fail** card instead → 302 to `/payment/fail`; order `failed`; no registration created; retry from Stage 4 with a fresh checkout.
- Click **Cancel** on the gateway page instead of paying → 302 to `/payment/cancel`; order `cancelled`.

---

## Stage 6 — Registration confirmed

```
GET {{base_url}}/payments/orders/
Authorization: {{learner_token}}
```

**200** — the row for your `tran_id`: `"status": "paid"`, `"item_type":
"webinar"`, `webinar_slug`/`webinar_title` set, `course_slug`/`course_title`
**null**, `paid_at` set. `gateway_payload`/`val_id` never present.

```
GET {{base_url}}/webinars/my-webinars/
Authorization: {{learner_token}}
```

**200** — the webinar appears in the learner's list.

```
GET {{base_url}}/webinars/my-webinars/{{webinar_slug}}/
Authorization: {{learner_token}}
```

**200** — `meeting_url` is now **present** (registrant-only field, unlocked
by the paid registration).

```
GET {{base_url}}/notifications/
Authorization: {{learner_token}}
```

**200** — a `payment.successful` notification row, `data.item_type ==
"webinar"`, `data.item_slug == "{{webinar_slug}}"`.

**Final gate check — the whole point of this chain:**

```
POST {{base_url}}/webinars/{{webinar_slug}}/register/
Authorization: {{learner2_token}}   (has NOT paid)
```

**422** — "This is a paid webinar. Complete payment via the checkout
endpoint to register." Confirms a learner cannot skip payment and register
directly.

---

## One-pass checklist (tick top to bottom for a full run)

- [ ] Institution creates webinar with `price > 0`, future `scheduled_at`, `max_capacity: 2` → `draft`
- [ ] Institution assigns host → `host_expert` set
- [ ] Foreign/inactive expert as host → rejected
- [ ] Non-host publish attempt → 404
- [ ] Publish without host → 422
- [ ] Host publishes → `published`, live in catalog, `meeting_url` hidden pre-purchase
- [ ] Checkout on draft webinar → 404
- [ ] Checkout on free webinar → 422
- [ ] Checkout on started webinar → 422
- [ ] Re-checkout cancels the stale pending order
- [ ] Fill capacity, checkout a 3rd learner → 422
- [ ] Learner pays with sandbox success card → 302 to success page
- [ ] Order shows `paid`, `item_type: webinar`, correct amount/currency
- [ ] `my-webinars` lists it; detail reveals `meeting_url`
- [ ] `payment.successful` notification exists with correct `item_type`/`item_slug`
- [ ] Unpaid learner hitting `/register/` directly on this webinar → 422
- [ ] Fail-card / cancel paths produce `failed`/`cancelled` orders, no registration

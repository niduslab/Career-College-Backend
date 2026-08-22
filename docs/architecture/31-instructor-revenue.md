# 31 — Instructor Revenue

## Problem

The frontend page at `/dashboard/instructor/revenue` is entirely mock: an
"Available Balance" and "Next payout" that imply a payout system, a 15%
"Platform Fees" commission split, bank-account-ending-in-4421 payout rows, an
`EarningsChart` fed six hardcoded months.

None of that exists in the backend. Per `CLAUDE.md`'s Payments section:

> Institution wallet/payout, refunds, and the analytics `revenue.enabled` flip
> are Phase 2 — not built.

This feature replaces the mock with what **is** real: gross revenue earned
from paid orders on the instructor's own courses. No payout, no balance, no
commission split, no bank account — because none of those have a backing
table, and inventing one here would contradict the "Phase 2, not built" note
everywhere else in the codebase.

## Scope: gross revenue only

This is **not** an un-faking of an existing stub the way admin revenue was —
`instructor_analytics_service._revenue_metrics()` already computes real
per-instructor gross via the dual-path course filter
(`Q(course__instructors=instructor) | Q(course__created_by=instructor)`, with
mandatory `.distinct()`). This feature extends that with a trend series, a
per-course breakdown, and a paginated order history — the summary endpoint
keeps owning the headline KPI numbers; nothing here duplicates it.

## What is real, and what we refuse to invent

| Concept | Real? | Source |
|---|---|---|
| Gross revenue (sum of paid amounts) | ✅ | `Order.amount` where `status='paid'` |
| Revenue by course | ✅ | Same, grouped by `course_id` |
| Revenue trend over time | ✅ | Same, bucketed by `created_at` |
| Paid-order history (date, amount, course, learner) | ✅ | `Order` rows |
| Currency | ✅ | `Order.currency` (always `'BDT'` today) |
| "Available Balance" | ❌ | No payout/ledger model. Nothing to subtract already-paid-out amounts from. |
| "Next payout" date | ❌ | No payout schedule exists. |
| "Platform Fees" / commission % | ❌ | No commission-split model. Any percentage shown would be invented. |
| Bank account / payout method | ❌ | No payout method model. |
| "Lifetime Earnings since 2023" | ❌ | Redundant with gross revenue and implies a founding date with no meaning. |
| Refund handling | — | `Order.Status` has no `REFUNDED` state. A duplicate payment is marked `FAILED` with a `gateway_payload.requires_refund` audit flag (manual, off-system) — cosmetic only, never netted out of revenue. |

**Every number below maps to `Order.amount` / `Order.created_at` /
`Order.course` on rows where `status='paid'`.** Nothing here is computed from
a formula without a backing table.

## Endpoints

Both gated `[IsAuthenticated, IsEmailVerified, IsInstructorUser]`, matching
every other instructor-scoped analytics endpoint.

### `GET /api/v1/analytics/instructor/revenue/summary/`

Non-paginated. Cards + breakdown + trend, all describing the whole dataset:

```json
{
  "success": true,
  "data": {
    "gross": "1240.00",
    "currency": "BDT",
    "paid_orders": 6,
    "window_days": 30,
    "window_gross": "370.00",
    "growth_pct": 12.5,
    "avg_order_value": "206.67",
    "by_course": [
      {"id": 12, "title": "Full Stack Development", "slug": "...", "gross": "450.00", "paid_orders": 3}
    ],
    "trend": {
      "granularity": "monthly",
      "periods": 6,
      "series": [{"period": "2026-03", "value": 120.0}, ...]
    },
    "courses": [{"id": 12, "title": "...", "slug": "..."}]
  }
}
```

- `growth_pct` follows the same no-baseline rule as every other growth field
  in this codebase: `null` when the previous window had zero revenue, never a
  fabricated `0%`.
- `trend` mirrors `admin_analytics_service.revenue_trend()` exactly — same
  `build_value_series(qs, 'created_at', Sum('amount'), granularity, periods)`
  call, same `?granularity=monthly|weekly&periods=N` query params, just scoped
  to the instructor's own courses instead of the whole platform. Bucketing on
  `created_at` (when the order was placed), not `paid_at`, to match the admin
  precedent bit-for-bit — an order that failed validation never reaches
  `status='paid'` in the first place, so the two fields agree for every row
  that counts.
- `courses` is the same course-option list as the students summary
  (`instructor_course_options`), reused so the frontend's course filter
  dropdown behaves identically across both pages.

### `GET /api/v1/analytics/instructor/revenue/orders/`

Paginated order history. `StandardResultsSetPagination`.

| Param | Values | Default |
|---|---|---|
| `course_id` | int; must be owned, else 404 | all owned courses |
| `sort` | `-paid_at` / `paid_at` / `-amount` / `amount` | `-paid_at` |
| `page`, `page_size` | paginator | 1, 10 |

Row shape:

```json
{
  "order_id": 41,
  "course": {"id": 12, "title": "...", "slug": "..."},
  "learner_name": "Md. Al Amin",
  "amount": "150.00",
  "currency": "BDT",
  "paid_at": "2026-08-17T05:52:18Z"
}
```

`learner_name` is a plain string snapshot of `order.user.full_name` at read
time, not a nested object — an instructor viewing their own sales ledger has
no reason to see the buyer's email or id, so only the name is exposed. This
deliberately narrows what `Enrollment`-based endpoints (students roster)
expose about the same person.

Every `Order` here is already `status='paid'` — a failed/cancelled/initiated
order was never anyone's money and has no place in a revenue ledger.

## Query budget

Summary: ~6 aggregate queries (gross, window comparison, by-course group-by,
trend series, course options) — independent of order volume.

Orders list: 2 queries (count + page), `select_related('course', 'user')`
covers every serialized field.

## File layout

| File | Role |
|---|---|
| `analytics/services/instructor_revenue_service.py` | Query building, aggregates, row shape |
| `analytics/all_views/instructor_revenue_views.py` | Two `APIView`s |
| `analytics/urls.py` | Two routes under `instructor/revenue/` |

No serializer file — same as `instructor_students_service.py`, the
`analytics` app has no serializer layer and every endpoint returns plain
dicts built in its service.

## Frontend

`src/lib/instructor-revenue-api.ts`, `src/hooks/use-instructor-revenue.ts`,
and a rewrite of `src/components/dashboard/instructor/revenue-page.tsx`
replacing `KPI` / `MONTHLY_EARNINGS` / `PAYOUTS` / `EarningsChart`'s
hand-drawn SVG with a real trend series and a paginated order table. CSV
export stays (`Export CSV`) but exports the real order rows.

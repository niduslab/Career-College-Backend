# System Audit

This file collects audit passes over the codebase. Each pass is dated and
scoped; resolved items stay in the closed table for the paper trail, open
items keep full detail until they ship.

## Audit Passes

1. [Catalog Filtering & Sorting (opened 2026-05-21)](#1-catalog-filtering--sorting) — 9 of 10 actionable items closed; 1 blocked + 1 informational open.
2. [System-Wide Security Sweep (opened 2026-05-21)](#2-system-wide-security-sweep) — 18 findings; 9 closed, 9 open.
3. [Payments Integration Review (opened 2026-07-06)](#3-payments-integration-review) — 9 findings; 2 closed, 7 open.
4. [Whole-Project Sweep (opened 2026-07-06)](#4-whole-project-sweep) — 32 findings (4 high, 12 medium, 16 low); all open. Covers auth, id_verification, courses (non-catalog), messaging, notifications, realtime, webinars, analytics, core + config.

---

# 1. Catalog Filtering & Sorting

**Date opened:** 2026-05-21
**Last updated:** 2026-05-21
**Scope:** the multi-criteria catalog filter/sort wiring in
`courses/services/enrollment_service.py` → `filter_catalog_courses`, plus
the surfaces it touches (`CatalogCourseListView`, `CatalogCourseListSerializer`,
and the queryset returned by `get_catalog_courses`).

## TL;DR

Catalog list path has no N+1 — `select_related('created_by', 'category')` +
`prefetch_related('instructors')` covers everything the list serializer reads.
The original sweep surfaced one critical, two high-severity, six medium,
and three low-severity items. **Every actionable code issue is now closed**
except M3 (blocked on a model field). L1 stays open as a documented future
hotspot — it's not broken today, and the prescribed fix is a schema-level
denormalization that should wait until EXPLAIN on real data proves the need.

## Closed issues

These were fixed in the 2026-05-21 pass. Code references point at the
current implementation; if you need the original problem narrative, see
git history for the prior version of this file.

| ID | Severity | What was wrong | What shipped |
|---|---|---|---|
| C1 | Critical | Instructor name search hit `first_name` / `last_name` (unpopulated AbstractUser columns), silently returning zero matches. | Search now ORs against `instructors__full_name`. See [`enrollment_service.py:230-239`](courses/services/enrollment_service.py#L230-L239). |
| H1 | High | `?category=X&subcategory=Y` ANDed two equality predicates on the same single FK column → always empty. | Three-way conditional joins through `category__parent`. See [`enrollment_service.py:170-191`](courses/services/enrollment_service.py#L170-L191). |
| H2 | High | `?category=parent` didn't include rows tagged with a subcategory of that parent. | Same branch as H1 — category-only path broadens to `Q(category__slug) | Q(category__parent__slug)`. |
| M1 | Medium | `description__icontains` had no supporting index — seq-scan on large catalogs. | GIN trigram indexes on `title` and `description`; `pg_trgm` enabled. See [`0007_add_catalog_filter_indexes.py`](courses/migrations/0007_add_catalog_filter_indexes.py). **Deploy note:** the migrating Postgres role needs `CREATE EXTENSION` privilege. |
| M2 | Medium | `price` and `duration_minutes` range filters had no index. | Composite btrees `(is_published, price)` and `(is_published, duration_minutes)`, same migration. |
| M4 | Medium | Unknown `?sort=foo` silently fell back to default. | `_validate_catalog_params` raises `ValidationError`; view returns 400 listing valid sort keys. See [`enrollment_service.py:77-130`](courses/services/enrollment_service.py#L77-L130) and [`enrollment_views.py:47-54`](courses/all_views/enrollment_views.py#L47-L54). |
| M5 | Medium | Negative or non-numeric `price_min`/`price_max`/`duration_min`/`duration_max` were silently accepted. | Same validator path — 400 on non-numeric or negative values. |
| M6 | Medium | `?level=foobar` silently returned zero rows. | Same validator path — 400 listing the bad tokens and the valid set. |
| L2 | Low | `_csv_param` docstring claimed it deduped; it didn't. | Now dedupes via `dict.fromkeys`, order preserved. See [`enrollment_service.py:50-60`](courses/services/enrollment_service.py#L50-L60). |

---

## Open issues

### M3. `?sort=rating` is a silent no-op

**Status: OPEN** — blocked on a model field.

The `'rating'` branch of `_apply_catalog_sort`
([`enrollment_service.py:289-292`](courses/services/enrollment_service.py#L289-L292))
falls back to `published_at` ordering because there is no `avg_rating`
column on `NidusCourse` (or equivalent aggregate). Frontend has no
signal that the requested sort was ignored — UI may show "Sort by:
Top-rated" while the rows are actually newest-first.

**Why it matters:** a learner clicks "Sort by: Top rated" and trusts
the dropdown label. The grid below is still ordered by publish date,
so a 1.2-star course can sit above a 4.9-star course. Once the rating
field actually ships, this gets *worse*: frontend code that worked
around the bug ("just call it newest, I guess") keeps showing newest
while the API silently starts honoring `rating`, and the sort behavior
appears to "drift" with no deploy explaining it.

**Recommended interim:** drop `'rating'` from `CATALOG_SORT_OPTIONS`.
With M4 fixed, `?sort=rating` would then return an honest 400 listing
the currently valid sorts. Re-add `'rating'` to the set when
`NidusCourse.avg_rating` lands and the corresponding branch in
`_apply_catalog_sort` is wired up.

### L1. Popularity-sort + search path is the heaviest in the filter set

**Status: OPEN (informational)** — no fix today; revisit with EXPLAIN
once the catalog grows.

When both `?search=` and `?sort=popularity` are supplied, the generated
SQL stacks:

- `LEFT JOIN nidus_courses_instructors` + `LEFT JOIN users` (for search)
- `LEFT JOIN enrollments` (for `Count('enrollments', distinct=True)`)
- `SELECT DISTINCT` (from the search's `.distinct()`)
- `GROUP BY` on the SELECT'd columns

It is correct — `Count(distinct=True)` defends the count against the
instructor-join fan-out — but it's the heaviest path in the filter set.
With a debounced search input (300 ms) on a busy catalog page that
defaults to popularity sort, that's 3–4 of these joined queries per
second per active user.

**Action when triggered:** EXPLAIN on representative data (~10k+
published rows) and, if the plan stops being index-driven, materialize
`enrollment_count` onto `NidusCourse` via the `Enrollment` post_save
signal so the popularity sort can `ORDER BY` a denormalized column
instead of an aggregate.

## N+1 sweep — clean

Walked the request path end-to-end for `GET /api/v1/courses/catalog/`:

| Touchpoint | What it reads | How it's loaded | Verdict |
|---|---|---|---|
| `CatalogCourseListView.get` | queryset | `get_catalog_courses()` base | — |
| `CatalogCourseListSerializer.thumbnail` | `ImageField` | model attribute | OK |
| `CatalogCourseListSerializer.category` (FK) | `category.id/name/slug` | `select_related('category')` | OK |
| `CatalogCourseListSerializer.instructors` (M2M) | `id/full_name/email` per instructor | `prefetch_related('instructors')` | OK |
| `InstructorBriefSerializer.full_name` | direct column | already in user table | OK |
| `StandardResultsSetPagination` | `count(*)` | separate query | expected |

Catalog list endpoint emits **3 queries total** per page (main +
instructor prefetch + count). The prefetch is one extra IN-query per
request, not an N+1 — do not "optimize" it into a JOIN, that would
explode row count and confuse pagination.

## Out-of-scope follow-ups noticed during the fix pass

- `price_type` is not validated — `?price_type=garbage` silently
  becomes a no-op. Same shape as the M6 bug; add to
  `_validate_catalog_params` if you want symmetry.
- Once production traffic exists, watch p95 of `/catalog/?search=…`
  to confirm the new trigram indexes are getting picked up by the
  planner.

---

# 2. System-Wide Security Sweep

**Date opened:** 2026-05-21
**Last updated:** 2026-05-25
**Scope:** project settings, middleware stack, URL routing, permission
classes, cookie helpers, auth/OTP/password views, and file upload
handling. Not in scope: dependency CVEs (run `pip-audit` separately),
infrastructure (nginx/wsgi/redis), or static analysis (run `bandit`
separately).

## TL;DR

18 findings. **9 closed** (2026-05-25 pass). **9 open**: 1 critical,
5 medium, 2 low. All blockers for non-local deployment are resolved
except SEC-C2 (3 commented-out `print()` lines that should be deleted).

| ID | Severity | Status | One-liner |
|---|---|---|---|
| SEC-C1 | Critical | ✅ Closed | `CorsMiddleware` not registered → CORS headers never emitted |
| SEC-C2 | Critical | ❌ Open | `print(OTP)` in 3 views — commented out but not deleted |
| SEC-H1 | High | ✅ Closed | No `CORS_ALLOWED_ORIGINS` / `CORS_ALLOW_CREDENTIALS` defined |
| SEC-H2 | High | ✅ Closed | `SECRET_KEY` unsafe dev fallback |
| SEC-H3 | High | ✅ Closed | `DEBUG` defaulted to `True` |
| SEC-H4 | High | ✅ Closed | No throttling on login endpoint |
| SEC-H5 | High | ✅ Closed | Forgot-password confirmed account existence |
| SEC-H6 | High | ✅ Closed | No file-type validation on uploads |
| SEC-H7 | High | ✅ Closed | `OTP_RATE_LIMIT=None` default silently disabled throttling |
| SEC-M1 | Medium | ❌ Open | OTP throttle is IP-based only — email-rotation bypass |
| SEC-M2 | Medium | ❌ Open | OTP rate `20/min` too permissive |
| SEC-M3 | Medium | ❌ Open | No HSTS / `CSRF_COOKIE_SECURE` / `SECURE_SSL_REDIRECT` / `CSRF_TRUSTED_ORIGINS` |
| SEC-M4 | Medium | ❌ Open | Access token lifetime 12 hours |
| SEC-M5 | Medium | ❌ Open | No global file upload size cap |
| SEC-M6 | Medium | ❌ Open | Django admin at default `/admin/` path |
| SEC-M7 | Medium | ✅ Closed | Password change/reset doesn't blacklist refresh tokens |
| SEC-L1 | Low | ❌ Open | `RequestLoggingMiddleware` is a no-op placeholder |
| SEC-L2 | Low | ❌ Open | `IsAdminOrReadOnly` permission class is unused dead code |

---

## Closed issues

| ID | Severity | What was wrong | What shipped (2026-05-25) |
|---|---|---|---|
| SEC-C1 | Critical | `CorsMiddleware` missing from `MIDDLEWARE` | Added at position 2 in `MIDDLEWARE` (after `SecurityMiddleware`). [`settings.py:72`](career_college_backend/settings.py#L72) |
| SEC-H1 | High | No CORS config — every preflight failed | `CORS_ALLOWED_ORIGINS = env.list(...)` defaulting to `FRONTEND_URL`; `CORS_ALLOW_CREDENTIALS = True`. [`settings.py:171-172`](career_college_backend/settings.py#L171-L172) |
| SEC-H2 | High | `SECRET_KEY = os.getenv('SECRET_KEY', 'unsafe-dev-secret-key')` | `SECRET_KEY = env('SECRET_KEY')` — raises `ImproperlyConfigured` when missing. |
| SEC-H3 | High | `DEBUG` defaulted to `True` | `environ.Env(DEBUG=(bool, False))` — requires opt-in via `.env`. |
| SEC-H4 | High | No throttle on `UserLoginView` | `LoginThrottle(AnonRateThrottle)` added; rate from `LOGIN_RATE_LIMIT` env (default `10/min`). [`auth_views.py:15-17`](authentication/all_views/auth_views.py#L15-L17) |
| SEC-H5 | High | `ForgotPasswordView` returned distinct 400 errors per account state — email enumeration | `ForgotPasswordSerializer.validate()` silently swallows all ineligible states; view always returns same 200 regardless. [`serializers.py:385`](authentication/serializers.py#L385), [`password_views.py:30`](authentication/all_views/password_views.py#L30) |
| SEC-H6 | High | No `FileExtensionValidator` on any upload field | `core/validators.py` — `validate_image_file`, `validate_video_file`, `validate_pdf_file` (extension + magic bytes). Applied to all `ImageField`/`FileField` fields. Migrations `courses/0009`, `id_verification/0002`. |
| SEC-H7 | High | `OTP_RATE_LIMIT = env('OTP_RATE_LIMIT', default=None)` — `getattr` trap silently disabled throttling | Changed to `default='20/min'` in `settings.py`. [`settings.py:269`](career_college_backend/settings.py#L269) |
| SEC-M7 | Medium | Password change/reset didn't blacklist outstanding refresh tokens — stolen token survives password change | `_blacklist_all_tokens(user)` helper added; called in both `ResetPasswordSerializer.save()` and `ChangePasswordSerializer.save()`. [`serializers.py:24`](authentication/serializers.py#L24) |

---

## Open issues

### SEC-C2. OTPs leaked to stdout via debug `print()` statements

**Status: OPEN.** Statements are commented out but not deleted — delete them.

Three views contain commented `print()` lines that should be removed entirely:

- [`auth_views.py:59`](authentication/all_views/auth_views.py#L59) — registration
- [`otp_views.py:98`](authentication/all_views/otp_views.py#L98) — resend OTP
- [`password_views.py`](authentication/all_views/password_views.py) — forgot password (removed in H5 fix, verify)

```python
#print(f"Generated OTP for {user.email}: {otp_code}")  # Debugging log
```

**Why not closed:** commented code is not deleted code. A future dev uncommenting
for debugging re-opens the leak. The lines should not exist.

**Fix:** delete all three lines. For local dev, `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`
already prints the full email (including OTP) to the console — no server-side print needed.

---

### SEC-M1. OTP throttle is IP-based, not email-based

**Status: OPEN.**

`OTPVerifyThrottle` extends `AnonRateThrottle`, which keys on client IP.
An attacker rotating IPs bypasses the per-IP cap entirely.

**Impact:** OTP space is 10⁶ (6-digit numeric). With IP rotation and a
2-minute expiry window, an attacker can attempt far more codes than the
rate limit implies.

**Fix:** custom throttle keyed on the `email` field from the request body:

```python
class OTPEmailThrottle(SimpleRateThrottle):
    scope = 'otp_email'
    def get_cache_key(self, request, view):
        email = (request.data.get('email') or '').lower()
        if not email:
            return None
        return f'throttle_otp_email_{hashlib.sha256(email.encode()).hexdigest()}'
```

---

### SEC-M2. OTP throttle default is very permissive

**Status: OPEN.**

`20/min` against a 6-digit OTP = 1,200 attempts/hour per IP. OTP space
is 1,000,000. With IP rotation the brute-force window is ~14 hours of
sustained attempts. The 2-minute OTP expiry is the main mitigation — the
throttle is not adding much at 20/min.

**Fix:** tighten to `5/min` in settings, layered on top of the per-email
throttle from SEC-M1.

---

### SEC-M3. No production-hardening settings

**Status: OPEN.**

`settings.py` is missing all of:

- `SECURE_HSTS_SECONDS`
- `SECURE_HSTS_INCLUDE_SUBDOMAINS`
- `SECURE_HSTS_PRELOAD`
- `SECURE_SSL_REDIRECT`
- `SECURE_PROXY_SSL_HEADER` — `request.is_secure()` returns False behind a reverse proxy
- `SESSION_COOKIE_SECURE`
- `CSRF_COOKIE_SECURE`
- `CSRF_TRUSTED_ORIGINS` — required for cross-origin POSTs in Django 4.0+

**Fix:** add an environment-gated block:

```python
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31_536_000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])
```

Run `python manage.py check --deploy` to confirm all warnings clear.

---

### SEC-M4. Access token lifetime is 12 hours

**Status: OPEN.**

[`settings.py:153`](career_college_backend/settings.py#L153):

```python
'ACCESS_TOKEN_LIFETIME': timedelta(hours=12),
```

Industry norm is 5–15 minutes. Access tokens are bearer credentials on
every request — captured by proxy logs, analytics, browser screenshots.
12-hour validity window for any single leak is large.

**Fix:** drop to 15 minutes. Refresh tokens already exist (7 days, rotation
+ blacklist). Frontend must handle 401 → refresh → retry, which the
cookie-helpers setup is already wired for.

```python
'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
```

---

### SEC-M5. No global file upload size limit

**Status: OPEN.**

No `DATA_UPLOAD_MAX_MEMORY_SIZE` or `FILE_UPLOAD_MAX_MEMORY_SIZE` cap.
Django defaults allow files of any size to stream to disk as temp files.

**Impact:** malicious user uploads a 50 GB video → disk fills → DoS.

**Fix:**

```python
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024   # 10 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
MAX_VIDEO_UPLOAD_MB = 500
MAX_IMAGE_UPLOAD_MB = 5
MAX_DOCUMENT_UPLOAD_MB = 10
```

Add per-field size validators in upload serializers. Combine with
nginx `client_max_body_size` at the reverse-proxy layer.

---

### SEC-M6. Admin served at default `/admin/` path

**Status: OPEN.**

[`urls.py:23`](career_college_backend/urls.py#L23):

```python
path('admin/', admin.site.urls),
```

Default path is probed by automated scanners constantly. Combined with
brute-force attempts against admin credentials, it's the highest-value
target on the deployment.

**Fix (defence in depth — all three, not just rename):**

1. Rename to a non-obvious slug: `path('back-office-x7k/', admin.site.urls)`
2. Restrict by IP at the reverse-proxy layer (nginx `allow`/`deny`)
3. Require VPN-only access or a separate admin subdomain

---

### SEC-L1. `RequestLoggingMiddleware` is a no-op placeholder

**Status: OPEN (dead code).**

[`core/middleware.py`](core/middleware.py) — class body calls
`self.get_response(request)` and nothing else. Not registered in
`MIDDLEWARE`.

**Fix:** delete the file, or implement actual request logging.

---

### SEC-L2. `IsAdminOrReadOnly` permission class is unused

**Status: OPEN (dead code).**

[`core/permissions.py:20`](core/permissions.py#L20) declares
`IsAdminOrReadOnly`. No view imports or uses it.

**Fix:** delete it. Dead code in the permissions module misleads readers
into thinking it guards something.

---

## Out-of-scope follow-ups noticed during the sweep

- **`ACCOUNT_EMAIL_VERIFICATION = 'none'`** — allauth email verification
  disabled because the project rolls its own OTP flow. Confirm no allauth
  login path bypasses the OTP check (none visible in `urls.py` currently).
- **`EMAIL_USE_SSL = False`** hardcoded rather than env-driven. Some mail
  providers require SSL on port 465; promote to env-driven.
- **`OTP_RATE_LIMIT` read at view-import time** — changing the env var
  requires a process restart. Fine, but worth documenting.
- **Dependency CVEs:** run `pip-audit` against the lock file separately.
- **Static analysis:** run `bandit -r .` separately.
- **Secret scanning:** run `git-secrets` or `trufflehog` against repo history.

---

# 3. Payments Integration Review

**Date opened:** 2026-07-06
**Last updated:** 2026-07-06
**Scope:** the SSLCommerz payments PR (`feature/ssl_commerz_sandbox_payment`) —
the new `payments/` app (`Order` model, `order_service`, `sslcommerz_service`,
callback/checkout/order views, reaper task) plus the free-enroll / free-register
gates it added in `courses/all_views/enrollment_views.py`,
`courses/services/enrollment_service.py`, and
`webinars/services/registration_service.py`, and the notification wiring.

## TL;DR

Strong, defensively-written integration. Trust model is correct: `finalize_payment`
is the sole PAID path, re-validates against the SSLCommerz Validation API, checks
tran_id/amount/currency/store_id against the price snapshot, is idempotent under
double-fire, treats PAID as terminal, and flags duplicate payments for refund.
62 tests cover the tamper/duplicate/idempotency cases. **No blocking correctness
bugs.** 9 findings: 2 fixed in this pass (admin webinar fields, order-detail
N+1), 7 open — 2 medium (both architectural/UX, not correctness), 5 low.

## Closed issues

Fixed in the 2026-07-06 pass.

| ID | Severity | What was wrong | What shipped |
|---|---|---|---|
| PAY-L6 | Low | `OrderAdmin` referenced only `course` in `list_display`/`search_fields`/`readonly_fields` — webinar orders showed a blank column, weren't searchable, and `webinar` rendered as an *editable* FK dropdown (contradicting the read-only audit-row intent). | `webinar` added to all three tuples. See [`admin.py:8-15`](payments/admin.py#L8-L15). |
| PAY-L7 | Low | `OrderDetailView` did `select_related('course')` only → one extra query on webinar order detail; inconsistent with `OrderListView`. | `select_related('course', 'webinar')`. See [`order_views.py:54`](payments/all_views/order_views.py#L54). |

---

## Open issues

### PAY-M1. Paid-course enroll gate lives in the view, not the service

**Status: OPEN.**

The `price > 0 → require a PAID order` check sits in `CourseEnrollView`
([`enrollment_views.py:110-127`](courses/all_views/enrollment_views.py#L110-L127)).
`enroll_learner()` itself will create a FREE enrollment on a paid course if
called with the default `enrollment_type`. Today only two callers exist — the
view (gated) and `finalize_payment` (passes `PAID` explicitly) — so there is no
live leak. But the invariant is one careless future caller away from giving away
paid content for free.

**Why it matters:** the webinar side already puts the equivalent gate *inside*
`register_for_webinar` (with a `via_payment=True` bypass for finalize). The
course side is the outlier — the guarantee should not depend on every call site
remembering to pre-check.

**Recommended:** move the "no free enrollment on a paid course without a PAID
order" check into `enroll_learner`, with a keyword bypass mirroring
`allow_unpublished` for the finalize path.

### PAY-M2. Duplicate payment is marked FAILED with no user-facing signal

**Status: OPEN.**

`_record_duplicate_payment`
([`order_service.py:262-278`](payments/services/order_service.py#L262-L278))
grants access, sets `gateway_payload.requires_refund=True`, and `logger.critical`s
for a manual refund — but dispatches **no notification**, and the learner's order
history shows the second order as `failed`. The learner paid twice, got access,
sees "failed", and has no signal that a refund is owed to them.

**Recommended:** dispatch a distinct notification (or a dedicated order sub-state),
and surface `requires_refund` in `OrderSerializer` so the frontend can show
"refund pending" instead of a bare "failed".

### PAY-L1. `courses`/`webinars` now depend on `payments` (conceptual cycle)

**Status: OPEN (architectural).**

`payments` imports `enroll_learner` / `register_for_webinar` at module top level;
`courses` and `webinars` import `payments.Order` **lazily inside functions**
([`enrollment_views.py:114`](courses/all_views/enrollment_views.py#L114),
[`registration_service.py`](webinars/services/registration_service.py)) to dodge the
import-time cycle. It works, but the lazy import papers over bidirectional coupling —
unlike the clean one-way `analytics → courses/webinars` dependency documented in
CLAUDE.md.

**Recommended:** expose a `has_paid_order(user, target)` helper from `payments`
and have `courses`/`webinars` call that, or invert so `payments` owns the grant and
the upstream apps never reach into it. Low priority; if kept as-is, document the
deviation.

### PAY-L2. Notification dispatch is untested

**Status: OPEN (test gap).**

The 4-edit wiring for `PAYMENT_SUCCESSFUL` / `PAYMENT_FAILED` (event type, builder,
`EVENT_TO_CATEGORY`, `_EVENT_TEMPLATE_MAP` + templates) is present and correct, but
no test asserts either event actually dispatches, and none render the templates. A
missing `ctx` key in a builder would only surface at runtime.

**Recommended:** one dispatch test per event (assert a `Notification` row lands with
the expected `data`), plus a template-render smoke test.

### PAY-L3. Unauthenticated callback endpoints trigger an outbound gateway call, unthrottled

**Status: OPEN.**

`/success/` and `/ipn/` are `AllowAny`. An unknown `tran_id` short-circuits to 404
before any network call (good), but a *known* tran_id with an arbitrary `val_id`
triggers a Validation-API round-trip. `tran_id` is non-guessable (`CC` + 24 hex),
so the risk is low.

**Recommended:** add a rate limit on the callback views as defence in depth.

### PAY-L4. `logger.info` fires on the money path via `enroll_learner`

**Status: OPEN.**

The payments app rule is "warning/error/critical/exception only — never
`logger.info`". `finalize_payment` calls `enroll_learner`, which does
`logger.info('Enrollment reactivated …')`. It's in the `courses` logger namespace,
so it doesn't violate the rule literally, but the intent (no info-level noise on the
money path) leaks.

**Recommended:** minor — accept, or demote that log when reached via finalize.

### PAY-L5. Truncated comment in the capacity block

**Status: OPEN (trivial).**

[`registration_service.py:47`](webinars/services/registration_service.py#L47) —
`# When the webinar is capacity-limited, lock its row so the capacity check` ends
mid-sentence. Pre-existing, but touched by this diff. Finish or drop it.

## Trust-model verification — clean

Walked the money path end-to-end:

| Property | Where enforced | Verdict |
|---|---|---|
| Only `finalize_payment` grants PAID | `order_service.finalize_payment` | OK |
| Gateway body never trusted; re-validates via API | `validate_transaction` + `_verification_failure` | OK |
| amount/currency/store_id/tran_id checked vs snapshot | `_verification_failure` | OK |
| `store_id` fails closed in production, tolerated in sandbox | `_verification_failure:162-173` | OK — tested both ways |
| PAID terminal; late fail/cancel no-op | `_terminal_mark:316` | OK |
| Idempotent under double IPN / redirect+IPN | pre-check + `select_for_update(of=('self',))` | OK — tested |
| Duplicate payment → refund flag, access still granted | `_record_duplicate_payment` | OK (but see PAY-M2) |
| fail/cancel callbacks require valid signature | `verify_callback_signature` | OK |
| `gateway_payload` / `val_id` never serialized to learner | `OrderSerializer` field list | OK |
| Stranded `processing` orders reconciled | `reap_stale_processing_orders_task` | OK |

---

# 4. Whole-Project Sweep

**Date opened:** 2026-07-06
**Last updated:** 2026-07-06
**Scope:** every app not already covered by passes 1–3 — `authentication`,
`id_verification`, `courses` (excluding the catalog filter/sort of pass 1 and
the payments-enroll gate of pass 3), `messaging`, `notifications`, `realtime`,
`webinars`, `analytics`, and `core` + `career_college_backend` config +
project-wide convention adherence. Run as five parallel domain audits; each
finding below was verified against the actual code. The four HIGH items and the
messaging/celery claims were independently re-verified before publishing.

## TL;DR

32 findings — **4 high, 12 medium, 16 low; all open.** No blockers *inside*
already-shipped features fire in normal happy-path use, but two of the highs are
"silent breakage" class: notification emails never send under the documented
worker command (CORE-H1), and LinkedIn OAuth 500s on every attempt (AUTH-H1).
Broad architecture, learner-safe serialization, payment trust model, analytics
institution-scoping, capacity locking, and the send-gate are all sound (see the
per-domain "clean" notes at the end). This pass found no critical data-loss or
auth-bypass holes.

## Severity index

| ID | Sev | Domain | One-liner |
|---|---|---|---|
| CORE-H1 | High | core/config | `notifications` Celery queue routed but no documented worker consumes it → notification emails silently never fire |
| AUTH-H1 | High | auth | LinkedIn OAuth feeds raw `/v2/me` into Google provisioning → `KeyError` 500 on every LinkedIn sign-in |
| AUTH-H2 | High | auth | OAuth `state` generated + stored but never validated on callback → login-CSRF |
| CRS-H1 | High | courses | Transcode task `.delay()` inside caller's `atomic()` (not `on_commit`) → worker races the commit / phantom-row retries |
| AUTH-M1 | Med | auth | `TokenRefreshView` never reads/writes the refresh cookie → pure cookie clients can't refresh |
| AUTH-M2 | Med | auth | Login errors leak account existence + state (deactivated / unverified / invalid) |
| AUTH-M3 | Med | auth | `User.save()` swallows `clean()` `ValidationError` → model invariants never block a write |
| AUTH-M4 | Med | id_verification | `transition_to` status write + verify side effect not atomic and not row-locked |
| CRS-M1 | Med | courses | No stuck-submission reaper for `AssignmentSubmission` (coding has one) → learner can be permanently blocked |
| CRS-M2 | Med | courses | Inconsistent owner scoping (`instructors` vs `instructors\|created_by`) across authoring endpoints |
| MSG-M1 | Med | messaging | Unread counts never exclude `sender` → caller's own sent messages count as unread |
| MSG-M2 | Med | notifications | `NotificationPreference.push_enabled` is a dead toggle — never consulted in `_push_ws` |
| MSG-M3 | Med | notifications/realtime | Zero test coverage for the entire notification + realtime layer |
| ANL-M1 | Med | analytics | Engagement composite bakes in a structurally-zero attendance weight → score capped ~85, never 100 |
| ANL-M2 | Med | analytics | `top_courses(sort='completion')` ranks by completion *count*, contradicting the `completion_rate` it displays |
| CORE-M1 | Med | core | `ValidationError`→400/422 mapping copy-pasted across ~15 sites; belongs in `core/` |
| AUTH-L1 | Low | auth | `create_user` default `user_type='customer'` is not a valid choice |
| AUTH-L2 | Low | auth | Partner-institution slug derived from registrant `full_name`, not institution name |
| AUTH-L3 | Low | id_verification | Duplicate-in-progress verification check is TOCTOU with no DB backstop |
| AUTH-L4 | Low | auth | `User.save()` recomputes slug + runs an `.exists()` query on every save (incl. OTP writes) |
| CRS-L1 | Low | courses | Duplicate `COURSE_COMPLETED` notification possible under concurrent recalc |
| CRS-L2 | Low | courses | Coding reaper can flip a still-running long submission to `error` |
| CRS-L3 | Low | courses | `courses/selectors.py` is a dead module (imported nowhere) |
| CRS-L4 | Low | courses | Transcoder always emits all 5 renditions → upscales low-res sources |
| MSG-L1 | Low | notifications | Template-less events (`MESSAGE_RECEIVED` etc.) enqueue no-op email tasks every dispatch |
| MSG-L2 | Low | realtime | `JWTAuthMiddleware(AuthMiddlewareStack(...))` redundantly runs session auth per WS connect |
| MSG-L3 | Low | messaging | `institution_expert` conversation accepts an arbitrary, unscoped `course_id` |
| ANL-L1 | Low | analytics | Stale comment "no payments/orders model exists" — one now exists |
| ANL-L2 | Low | analytics | `top_courses` docstring says "published" but query includes draft/archived |
| ANL-L3 | Low | analytics | `active_ratio` divides distinct-user count by active-*enrollment* count (unit mismatch) |
| CORE-L1 | Low | config | `.env.example` `DB_ENGINE=sqlite3` contradicts the Postgres-only requirement |
| CORE-L2 | Low | config | `settings.py` does import-time `LOG_DIR.mkdir` (no `parents=True`) + writability probe |

Cross-refs: `RequestLoggingMiddleware` no-op and `IsAdminOrReadOnly` dead code
were also independently surfaced this pass — already tracked as SEC-L1 / SEC-L2.

---

## High

### CORE-H1. Notification emails routed to a queue no documented worker consumes

**Status: OPEN.**

[`settings.py:255-258`](career_college_backend/settings.py#L255-L258) —
`CELERY_TASK_ROUTES` sends `send_notification_email_task` and
`purge_old_notifications_task` to `{'queue': 'notifications'}`. There is no
`CELERY_TASK_DEFAULT_QUEUE` override, and the only documented worker command
(CLAUDE.md, README) is `celery -A career_college_backend worker -l info` — no
`-Q notifications`. No Procfile / compose / script starts a worker on that queue.
A default worker consumes only the `celery` queue, so **every** notification email
(verification decisions, `PAYMENT_SUCCESSFUL/FAILED`, `WEBINAR_*`,
`MESSAGE_RECEIVED`, expert onboarding, review events) and the daily purge pile up
unconsumed and never send — in dev *and* prod. Auth OTP/credentials tasks are not
routed, so they still work, which masks the problem. Tests are unaffected
(`CELERY_TASK_ALWAYS_EAGER`).

**Fix:** drop the two routes (let them use the default queue), or run + document a
worker with `-Q celery,notifications`.

### AUTH-H1. LinkedIn OAuth 500s on every sign-in

**Status: OPEN.**

`fetch_linkedin_profile` ([`linkedin_oauth.py:116-139`](authentication/services/linkedin_oauth.py#L116-L139))
returns the raw `/v2/me` payload plus an `email` key. It is then fed into the
*Google* provisioning functions (aliased in `linkedin_views.py`), which require
`profile['sub']`, `['full_name']`, `['email_verified']`, `['given_name']`,
`['family_name']`, `['picture']`
([`user_provisioning.py:53,74-79`](authentication/services/user_provisioning.py#L53-L79))
— none of which LinkedIn sets → `KeyError` → 500. Compounding: `/v2/me` +
`/v2/emailAddress` are the deprecated r_liteprofile APIs and don't work with the
requested OIDC scopes (`openid email profile`); the correct call is
`GET /v2/userinfo`. No LinkedIn tests exist, so this shipped unnoticed.

**Fix:** switch to `/v2/userinfo` and normalize its response to the same dict shape
as `google_oauth._normalize_profile` (`sub`, `email`, `email_verified`,
`full_name`, `given_name`, `family_name`, `picture`).

### AUTH-H2. OAuth `state` is never validated → login-CSRF

**Status: OPEN.**

[`google_views.py:81`](authentication/all_views/google_views.py#L81) /
[`linkedin_views.py:74`](authentication/all_views/linkedin_views.py#L74) generate a
`state` and store it in the session, but it is only ever read back to *forward* to
the frontend — no code path compares the returned `state` to the session value, and
the `exchange-token` POST ignores `state` entirely. Enables OAuth login-CSRF
(attacker splices their own auth code to log a victim into the attacker's account,
or forces sign-in).

**Fix:** compare returned `state` against the session-stored value; reject on
mismatch.

### CRS-H1. Transcode task dispatched inside an open transaction, not on commit

**Status: OPEN.**

[`section_service.py:173`](courses/services/section_service.py#L173) —
`replace_lecture_video_and_enqueue_transcoding` calls
`transcode_video_asset_task.delay(...)` directly. In the lecture-create-with-video
path (`content_views.py` `_create_lecture` wraps the service call *and* the
subsequent `create_section_content_for_object` in one `transaction.atomic()`), two
failures result: (a) the worker can dequeue before the outer transaction commits →
`VideoAsset.objects.get(pk=...)` raises `DoesNotExist`; (b) if the `(section,
position)` unique constraint trips and the transaction rolls back, the just-queued
task now points at a deleted row. Every other dispatch in the app uses
`transaction.on_commit(lambda: task.delay(...))`. Masked in tests by
`CELERY_TASK_ALWAYS_EAGER` (runs inline while the row still exists), so there is no
coverage for the real async race.

**Fix:** defer the dispatch to `transaction.on_commit`.

---

## Medium

### AUTH-M1. `TokenRefreshView` is cookie-blind

[`auth_views.py:184-217`](authentication/all_views/auth_views.py#L184-L217) reads
the refresh token only from the request body and returns new tokens only in the
body. The auth model stores tokens in HttpOnly cookies (JS can't read them), so a
pure cookie client can't refresh, and a successful refresh doesn't update the
`access_token` cookie. **Fix:** fall back to `request.COOKIES[refresh_cookie]` and
call `set_jwt_cookies` on the response (mirror login/OAuth).

### AUTH-M2. Login errors leak account existence and state

[`serializers.py:73-81`](authentication/serializers.py#L73-L81)
(`UserLoginSerializer.validate`) returns distinct messages for deactivated
("deactivated or restricted"), unverified ("verify your email"), and other
("Invalid email or password"). Enables enumeration of registered emails and their
state. Registration `validate_email` (serializers.py:146-150) has the same class of
leak, lower impact. **Fix:** generic message for all pre-auth failures, or only
differentiate after a correct password.

### AUTH-M3. `User.save()` silently discards model validation

[`models.py:207-217`](authentication/models.py#L207-L217) wraps `self.clean()` in
`try/except ValidationError: pass`, so `User.clean()` and any future invariant never
block a write — invalid data persists platform-wide. **Fix:** don't swallow; drop
the in-`save` `clean()` and validate in serializers, or gate the swallow to the
specific social-auth path that needs it.

### AUTH-M4. Verification transition + side effect not atomic / not locked

[`id_verification/models.py:221-241`](id_verification/models.py#L221-L241) and
`:432-452` (`transition_to`) do `self.save()` then a *separate*
`_mark_instructor_verified()` / `_mark_institution_verified()` write. If the second
fails, the row is `approved` but the profile's `is_verified` stays `False`. No
`select_for_update`, so two concurrent admin reviews can both pass the transition
check. **Fix:** wrap transition + side effect in `transaction.atomic()` and lock the
row.

### CRS-M1. No stuck-submission reaper for assignments

Coding submissions have `reap_stuck_coding_submissions_task`; assignments have no
equivalent. If the `on_commit` `grade_assignment_submission_task.delay()` never
lands on the broker (Redis down at commit — the callback raises *after* the row is
committed), the submission is stuck in `submitted`/`grading` forever: the in-flight
partial unique blocks re-submission and `retry_assignment_grading`
([`learner_service.py:632`](courses/services/learner_service.py#L632)) only accepts
`grading_failed`. `acks_late` covers worker death, not a failed initial enqueue.
**Fix:** add a reaper flipping long-stale in-flight assignment rows to
`grading_failed`.

### CRS-M2. Inconsistent owner scoping across authoring endpoints

`LectureDetailAPIView`, `SectionContentReorderAPIView`, `QuizAnswerDetailAPIView`
scope by `Q(instructors=user) | Q(created_by=user)`; section CRUD,
`SectionContentListCreate`, quiz/question CRUD, and the `CourseItem*` bases scope by
`instructors=user` only ([`content_views.py`](courses/all_views/content_views.py)
lines 194/510/732 vs 61/78/110/563/604/776/817). For institution-owned courses
(`created_by` = institution user, not in `instructors`), the same actor can edit an
existing lecture but gets 404 creating a section — incoherent. **Fix:** apply the
project-wide `Q(instructors) | Q(created_by)` policy uniformly.

### MSG-M1. Unread counts include the caller's own sent messages

All three unread computations —
[`messaging_service.py:300-312`](messaging/services/messaging_service.py#L300-L312)
(`get_unread_conversation_count`), `:315-330` (`get_unread_counts`), and
[`serializers.py:78-90`](messaging/serializers.py#L78-L90)
(`ConversationSerializer.get_unread_count`) — filter only on `created_at >
last_read_at` and never exclude `sender=user`. The cursor is bumped only by explicit
`mark_read`. A learner who sends the opener immediately sees their own thread as 1
unread until they POST `/read/`. The three stay mutually consistent (the "always
agree" invariant holds) but all agree on the wrong number. **Fix:** add
`~Q(sender=user)` to all three, or bump the sender's cursor in `send_message`.

### MSG-M2. `push_enabled` is a dead preference toggle

[`dispatcher.py:82-99`](notifications/services/dispatcher.py#L82-L99) (`_push_ws`)
always fires `group_send`; nothing reads `push_enabled` (only `email_enabled` is
consulted). The field is stored, serialized, and writable via
`NotificationPreferenceView.patch`, so a user can "disable" in-app/WS push and keep
receiving it. **Fix:** honor it in `_push_ws`, or remove it from
model/serializer/API to stop advertising a no-op control.

### MSG-M3. Notification + realtime layers have zero tests

No test files exist in `notifications/` or `realtime/`. Untested: `dispatch()` dedup
+ `IntegrityError` race, `skip_email`, preference-gated email enqueue,
`PlatformConsumer` JWT reject-on-connect (`close(4001)`), stream group add/discard,
gate-error surfacing, unread recompute on push. High-risk code (races, `on_commit`
ordering, async ORM wrapping) with no regression net. Distinct from PAY-L2. **Fix:**
add dispatch/dedup/consumer-connect regression tests.

### ANL-M1. Engagement composite bakes in a structurally-zero attendance term

[`analytics_service.py:211`](analytics/services/analytics_service.py#L211) —
`_engagement_score` weights `attendance` at `0.15`, but `attendance_rate` is always
`0.0` (the `attended` field is never populated; tracking is unbuilt). Every
institution's `engagement_score.composite` is capped at ~85/100 and can never reach
100. The `webinars` block flags `attendance_tracking_enabled: False`, but the
engagement score gives no such signal — it just reports a depressed number.
**Fix:** drop attendance from the weights (renormalize the other three to sum to 1)
until tracking ships, or add an `attendance_included: False` flag.

### ANL-M2. `top_courses(sort='completion')` ranks by count, not the rate it shows

[`analytics_service.py:347`](analytics/services/analytics_service.py#L347) orders by
`-completed_count, -enrollment_count`, but each row surfaces `completion_rate` (a
percentage, line 359). A course at 10% (100/10) ranks above one at 100% (5/5),
contradicting the rendered field. **Fix:** annotate/sort by the ratio so ranking
matches `completion_rate`.

### CORE-M1. `ValidationError`→400/422 mapping is copy-pasted ~15 times

`webinars/all_views/status_views.py:20` extracts `_transition_error_response`; the
byte-identical block is inlined instead in `courses/all_views/status_views.py` (8×),
`courses/all_views/enrollment_views.py` (2×), and the three
`id_verification/all_views/*` modules. CLAUDE.md says duplicated helpers belong in
`core/`. **Fix:** extract one helper (e.g. `core/http.py`) and import everywhere.

---

## Low

| ID | Location | Problem → fix |
|---|---|---|
| AUTH-L1 | [`models.py:35`](authentication/models.py#L35) | `create_user` default `user_type='customer'` isn't a valid choice → no profile signal fires. Change to `'learner'`. |
| AUTH-L2 | [`serializers.py:210-214`](authentication/serializers.py#L210-L214) | PI slug computed from registrant `full_name` before `institution_name` is overwritten (slug never recomputes). Set name before first profile save, or force recompute. |
| AUTH-L3 | [`id_verification/serializers.py:32-38`](id_verification/serializers.py#L32-L38) | Duplicate-in-progress guard is `.exists()` TOCTOU with no partial unique on active statuses → two concurrent POSTs both create a draft. Add a DB backstop. |
| AUTH-L4 | [`models.py:219-235`](authentication/models.py#L219-L235) | `User.save()` recomputes slug + runs `.exists()` even when the name is unchanged (fires on every OTP write). Short-circuit when name unchanged. |
| CRS-L1 | [`enrollment_service.py:434-447`](courses/services/enrollment_service.py#L434-L447) | Concurrent recalc can dispatch `COURSE_COMPLETED` twice (cert is idempotent, notification isn't). Guard dispatch on `created` from `get_or_create`. |
| CRS-L2 | [`tasks.py:539-561`](courses/tasks.py#L539-L561) | Coding reaper's blind bulk `.update()` can flip a legitimately long-running submission to `error`. Key the cutoff off `time_limit_ms * total_tests`, or re-check per row. |
| CRS-L3 | `courses/selectors.py` | Dead module — `get_course_base_queryset` / `get_instructor_course(s)` imported nowhere. Remove or wire up. |
| CRS-L4 | [`transcoding.py:135`](courses/transcoding.py#L135) | Always produces 240p–1080p, upscaling low-res sources (wasted CPU/storage). Probe source height, cap renditions. |
| MSG-L1 | [`dispatcher.py:78-79`](notifications/services/dispatcher.py#L78-L79) | Template-less events (`MESSAGE_RECEIVED`, `LECTURE_COMPLETED`, `REVIEW_RECEIVED`, `VIDEO_*`, …) enqueue a no-op email task every dispatch. Short-circuit `_enqueue_email` when `_EVENT_TEMPLATE_MAP` has no entry. |
| MSG-L2 | [`realtime/middleware.py:34-36`](realtime/middleware.py#L34-L36) | `JWTAuthMiddleware(AuthMiddlewareStack(...))` runs a session lookup per WS connect after JWT already set `scope['user']`. Wrap only `URLRouter`. |
| MSG-L3 | [`conversation_views.py:182-188`](messaging/all_views/conversation_views.py#L182-L188) | `institution_expert` create fetches `NidusCourse.objects.get(pk=course_id)` unscoped — any course (incl. unowned/draft) can be attached as context and leaked via the serializer. Scope course to the institution or reject. |
| ANL-L1 | [`analytics_service.py:232`](analytics/services/analytics_service.py#L232) | Comment "No payments/orders model exists" is now false (payments app shipped). Update to "revenue aggregation deferred to Phase 2". |
| ANL-L2 | [`analytics_service.py:322-333`](analytics/services/analytics_service.py#L322-L333) | `top_courses` docstring says "published" but query has no status filter (draft/archived included). Add `is_published=True` or fix the docstring. |
| ANL-L3 | [`analytics_service.py:206`](analytics/services/analytics_service.py#L206) | `active_ratio = _pct(active_learners, active)` mixes distinct-user numerator with active-enrollment denominator → understated for multi-enrolled learners. Divide by distinct enrolled learners. |
| CORE-L1 | [`.env.example:8`](.env.example#L8) | `DB_ENGINE=sqlite3` contradicts the Postgres-only requirement (partial unique indexes, `django.contrib.postgres`). Set a Postgres backend in the example. |
| CORE-L2 | [`settings.py:318-330`](career_college_backend/settings.py#L318-L330) | Import-time `LOG_DIR.mkdir(exist_ok=True)` (no `parents=True`) + writability probe run on every management command; a nested custom `LOG_DIR` with a missing parent raises at import. |

---

## Verified clean this pass

- **Learner-safe serialization** (courses) — `is_correct` / `model_answer` /
  `rubric` / `solution_code` / hidden tests all handled by field *absence* on
  dedicated serializers plus service-layer filtering. Reveal-on-wrong /
  reveal-on-graded rules centralized and correct.
- **Submission atomicity + grading idempotency** (courses) — quiz/assignment/coding
  all `@transaction.atomic`, snapshot rubric/points, dispatch grading on
  `on_commit`, short-circuit on terminal status under `acks_late`. Certificate
  `get_or_create` idempotent. Reorder uses `select_for_update` + two-phase offset.
  `vote_on_review` race-safe. Code runner sandbox hardened (`cap_drop=ALL`,
  no-new-privileges, network off, always removed).
- **Send-gate** (messaging) — enforced service-only; consumer/views never re-check.
  `_push_ws_and_notify` recipient-only (no duplicate delivery). Dedup race-safe.
  `PlatformConsumer` rejects unauthenticated connects; async ORM wrapped in
  `database_sync_to_async`. `EVENT_TO_CATEGORY` + `_BUILDERS` cover all 30 events.
- **Analytics** — institution-scoping derived from the token on *every* query, no
  client-supplied institution id; 403-vs-404 correct; expert-performance attribution
  (creator+instructor dedup, co-taught double-credit, removed-expert exclusion) and
  trend zero-fill/bucket alignment correct and tested; no N+1.
- **Webinars** — capacity lock (`select_for_update().filter().first()`) genuinely
  holds the row lock and serializes concurrent first-time registrants; `meeting_url`
  registrant-only via dedicated serializers; presenter roles kept distinct; publish
  state machine consistent (no reintroduced `rejected`).
- **Departments + expert provisioning** (auth) — service-layer, per-institution
  scoped, `IntegrityError` race handling, `on_commit` email/notification, password
  kept out of the notification payload, deactivation blocks authoring.
- **Project conventions** (core) — response envelope consistent across all 9 apps;
  every view is an `APIView` subclass (no generics/ViewSets); all permissions in
  `core/permissions.py`; `message_dict`→400 / plain→422 uniform;
  `makemigrations --check` clean; all 4 `CELERY_BEAT_SCHEDULE` tasks exist with
  matching signatures.

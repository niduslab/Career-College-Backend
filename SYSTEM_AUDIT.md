# System Audit

This file collects audit passes over the codebase. Each pass is dated and
scoped; resolved items stay in the closed table for the paper trail, open
items keep full detail until they ship.

## Audit Passes

1. [Catalog Filtering & Sorting (opened 2026-05-21)](#1-catalog-filtering--sorting) — 9 of 10 actionable items closed; 1 blocked + 1 informational open.
2. [System-Wide Security Sweep (opened 2026-05-21)](#2-system-wide-security-sweep) — 18 findings; 9 closed, 9 open.

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

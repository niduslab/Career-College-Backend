# System Audit

This file collects audit passes over the codebase. Each pass is dated and
scoped; resolved items stay in the closed table for the paper trail, open
items keep full detail until they ship.

## Audit Passes

1. [Catalog Filtering & Sorting (opened 2026-05-21)](#1-catalog-filtering--sorting) — 9 of 10 actionable items closed; 1 blocked + 1 informational open.
2. [System-Wide Security Sweep (opened 2026-05-21)](#2-system-wide-security-sweep) — 18 findings; all open. Three are critical and should be fixed before any non-local deployment.

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
**Last updated:** 2026-05-21
**Scope:** project settings, middleware stack, URL routing, permission
classes, cookie helpers, auth/OTP/password views, and file upload
handling. Not in scope: dependency CVEs (run `pip-audit` separately),
infrastructure (nginx/wsgi/redis), or static analysis (run `bandit`
separately).

## TL;DR

**18 findings.** Two are **critical** and should block any non-local
deployment until fixed — most worryingly, CORS is silently broken (the
middleware isn't in the stack), and OTPs leak to stdout via debug
`print()` statements in three different views. A third issue (the
`OTP_RATE_LIMIT` config footgun, SEC-H7) is High rather than Critical
— it only manifests if the env var is missing, which `.env.example`
documents correctly today. The rest are a mix of production-hardening
gaps (HSTS, CSRF, cookie secure flags) and small hardening wins
(file-upload validation, login throttling, email enumeration).

| ID | Severity | One-liner |
|---|---|---|
| [SEC-C1](#sec-c1-corsmiddleware-missing-from-the-middleware-stack) | Critical | `CorsMiddleware` not registered → CORS headers never emitted |
| [SEC-C2](#sec-c2-otps-leaked-to-stdout-via-debug-print-statements) | Critical | `print(f"Generated OTP for {user.email}: {otp_code}")` in three views |
| [SEC-H1](#sec-h1-no-cors-configuration-at-all) | High | No `CORS_ALLOWED_ORIGINS` / `CORS_ALLOW_CREDENTIALS` etc. defined anywhere |
| [SEC-H2](#sec-h2-secret_key-has-an-unsafe-dev-fallback) | High | Missing env var → app runs with `'unsafe-dev-secret-key'` |
| [SEC-H3](#sec-h3-debug-defaults-to-true) | High | Missing env var → `DEBUG=True` in production |
| [SEC-H4](#sec-h4-no-throttling-on-login) | High | Password brute-force is wide open |
| [SEC-H5](#sec-h5-forgot-password-confirms-account-existence) | High | Email enumeration via `/auth/password/forgot/` |
| [SEC-H6](#sec-h6-no-file-type-validation-on-uploads) | High | XSS via `.html` masquerading as ID-doc / profile photo |
| [SEC-H7](#sec-h7-otp_rate_limit-is-a-footgun-when-the-env-var-is-missing) | High | Missing `OTP_RATE_LIMIT` env var silently disables throttling (env is set today, but the default is unsafe) |
| [SEC-M1](#sec-m1-otp-throttle-is-ip-based-not-email-based) | Medium | `AnonRateThrottle` lets attacker rotate IPs to brute one email |
| [SEC-M2](#sec-m2-otp-throttle-default-is-very-permissive) | Medium | `20/min` against a 6-digit code |
| [SEC-M3](#sec-m3-no-production-hardening-settings) | Medium | Missing HSTS / CSRF_COOKIE_SECURE / SECURE_PROXY_SSL_HEADER / CSRF_TRUSTED_ORIGINS |
| [SEC-M4](#sec-m4-access-token-lifetime-is-12-hours) | Medium | Industry norm is 5–15 minutes; stolen access tokens valid for 12h |
| [SEC-M5](#sec-m5-no-global-file-upload-size-limit) | Medium | Disk-fill DoS via large uploads |
| [SEC-M6](#sec-m6-admin-served-at-default-admin-path) | Medium | Easily discoverable by automated scanners |
| [SEC-M7](#sec-m7-changing-password-does-not-rotate-refresh-tokens) | Medium | Stolen refresh tokens survive a password change |
| [SEC-L1](#sec-l1-requestloggingmiddleware-is-a-no-op-placeholder) | Low | Dead code; delete or implement |
| [SEC-L2](#sec-l2-isadminorreadonly-permission-class-appears-unused) | Low | Dead permission class; delete or document |

---

## Critical

### SEC-C1. `CorsMiddleware` missing from the middleware stack

**Status: OPEN.**

`corsheaders` is in `INSTALLED_APPS`
([`settings.py:59`](career_college_backend/settings.py#L59)) but its
middleware is **not** in `MIDDLEWARE`
([`settings.py:71-80`](career_college_backend/settings.py#L71-L80)):

```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
```

Without `corsheaders.middleware.CorsMiddleware` (and registered as the
**top** middleware), no `Access-Control-Allow-*` headers are emitted on
any response. Any browser-based frontend running on a different origin
(localhost:3000, the production frontend, etc.) will hit a CORS error
on every API call.

**Impact:** the entire API is effectively unusable from any browser
frontend on a different origin. The fact that an integration is shipping
with this missing suggests either (a) the frontend is on the same origin
as the API today, or (b) someone disabled CORS at the reverse-proxy
layer — both are fragile.

**Fix:**

```python
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',  # MUST be first
    'django.middleware.security.SecurityMiddleware',
    ...
]
```

Pair with the SEC-H1 fix (define an allowlist).

---

### SEC-C2. OTPs leaked to stdout via debug `print()` statements

**Status: OPEN.**

Three different views print the generated OTP to stdout:

- [`auth_views.py:50`](authentication/all_views/auth_views.py#L50) — registration
- [`otp_views.py:98`](authentication/all_views/otp_views.py#L98) — resend OTP
- [`password_views.py:45`](authentication/all_views/password_views.py#L45) — forgot password

```python
print(f"Generated OTP for {user.email}: {otp_code}")  # Debugging log
```

**Impact:** in any environment where `stdout` is captured (most
production setups: systemd journal, Docker logs, Kubernetes logs, CI),
this writes every registration, resend, and password-reset OTP — paired
with the target email — into a log stream that's typically queryable by
anyone with deploy access. OTPs are short-lived (2 minutes) but the
window is long enough for an attacker with log access to take over an
account during the verification window. And operationally, OTPs in logs
violate any reasonable data-handling policy.

**Fix:** delete the three `print` statements. The OTP is already
delivered via `send_otp_email`; logging it server-side serves no
production purpose. For local development, `EMAIL_BACKEND=django.core.
mail.backends.console.EmailBackend` already prints the full email
(including OTP) to the console.

---

## High

### SEC-H1. No CORS configuration at all

**Status: OPEN.** Pairs with SEC-C1.

Even after adding `CorsMiddleware`, no allowlist is defined anywhere:

- No `CORS_ALLOWED_ORIGINS`
- No `CORS_ALLOW_ALL_ORIGINS`
- No `CORS_ALLOW_CREDENTIALS`
- No `CORS_ALLOWED_ORIGIN_REGEXES`

Default behavior of `django-cors-headers` with no config: no origins are
allowlisted, meaning even after fixing SEC-C1, every preflight will
still fail.

**Fix:** define an explicit allowlist driven by env:

```python
CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS', default='http://localhost:3000')
CORS_ALLOW_CREDENTIALS = True  # needed because JWT is in HttpOnly cookies
```

Do not use `CORS_ALLOW_ALL_ORIGINS = True` — it is incompatible with
`CORS_ALLOW_CREDENTIALS = True`, and even alone it's a bad default
because the API serves authenticated user data.

---

### SEC-H2. `SECRET_KEY` has an unsafe dev fallback

**Status: OPEN.**

[`settings.py:37`](career_college_backend/settings.py#L37):

```python
SECRET_KEY = os.getenv('SECRET_KEY', 'unsafe-dev-secret-key')
```

If the env var is missing in production, the app runs with a hardcoded,
publicly-known secret. Any attacker can forge session cookies,
password-reset tokens (which include `password_reset_token` random
string but the JWT signing key is `SECRET_KEY`), and JWTs.

**Impact:** total auth bypass if the fallback ever activates in prod —
and the failure is silent (Django doesn't warn about a fallback that
looks like a real secret).

**Fix:** fail-loud when missing.

```python
SECRET_KEY = os.environ['SECRET_KEY']  # KeyError if missing
```

Or a more friendly variant:

```python
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise ImproperlyConfigured('SECRET_KEY env var is required.')
```

Same shape: better to crash on boot than to silently run insecurely.

---

### SEC-H3. `DEBUG` defaults to `True`

**Status: OPEN.**

[`settings.py:40`](career_college_backend/settings.py#L40):

```python
DEBUG = env_bool('DEBUG', default=True)
```

`DEBUG=True` in production:

- exposes full stack traces (with local variables) on every 500
- exposes the SQL of failing queries
- enables the `/admin/` debug toolbar features
- skips `ALLOWED_HOSTS` enforcement
- serves `MEDIA_URL` via the dev URL config ([`urls.py:29-30`](career_college_backend/urls.py#L29-L30))

**Fix:** flip the default and let env opt-in:

```python
DEBUG = env_bool('DEBUG', default=False)
```

---

### SEC-H4. No throttling on login

**Status: OPEN.**

`UserLoginView` ([`auth_views.py:91`](authentication/all_views/auth_views.py#L91))
has `permission_classes = [AllowAny]` and no `throttle_classes`. DRF's
global `DEFAULT_THROTTLE_RATES` is also not set in `REST_FRAMEWORK`
settings.

**Impact:** an attacker can brute-force passwords at unlimited rate
against any known email. There is no account-lockout, no captcha, no
rate limit. With the existing default password validator (which accepts
8-character passwords, common-password list excluded), an online dictionary
attack is trivially feasible.

**Fix:** add a per-IP + per-email throttle:

```python
class LoginRateThrottle(AnonRateThrottle):
    scope = 'login'
    rate = '5/min'  # per IP

# In settings:
REST_FRAMEWORK = {
    ...
    'DEFAULT_THROTTLE_RATES': {
        'login': '5/min',
        'otp': '5/min',
        ...
    },
}
```

Belt-and-braces: also add a per-email key (custom throttle that hashes
the email and stores attempts in cache) to prevent IP-rotation from
bypassing the IP cap.

---

### SEC-H5. Forgot-password confirms account existence

**Status: OPEN.**

Per the Postman testing guide and serializer behaviour, `ForgotPasswordView`
returns `400` with `email - No account found with this email.` when the
email isn't registered. That's account-existence enumeration.

**Impact:** anyone can probe `/auth/password/forgot/` with a list of
candidate emails to learn which addresses have accounts. Useful for
credential-stuffing attacks (focus password lists on real accounts) and
for confirming whether a target person uses your platform.

**Fix:** return the same `200` "OTP sent" envelope regardless of
whether the email exists. Internally, no-op when the user doesn't
exist (or also no-op when the user exists but `is_email_verified=False`,
matching the existing constraint). Do not change the response shape based
on account existence.

---

### SEC-H6. No file-type validation on uploads

**Status: OPEN.**

The codebase has at least four FileField/ImageField surfaces:

- ID verification: `document_front`, `document_back`, `selfie`, `resume` (any file accepted)
- Profile photo: `LearnerProfile.profile_photo`, `InstructorProfile.profile_photo`
- Partner institution: `logo`, `cover_image`
- Course: `thumbnail`, video lectures (raw video upload)

Grep'd the project for `FileExtensionValidator`, `validate_image`, or
content-type allowlist checks — **none exist** outside of the test
fixtures and `mime_type` field population.

**Impact:**

1. **XSS via DEBUG mode media serving.** While `DEBUG=True` (default —
   see SEC-H3), `urls.py:29-30` serves uploaded files through Django's
   static helper with no `Content-Disposition`. An attacker uploading
   `.html` with embedded JS as `document_front` and then sharing the
   media URL with an admin reviewer (or a course-page viewer in case
   of `thumbnail`) can XSS them.
2. **Content-type spoofing.** A `.exe` named `selfie.jpg` can be
   uploaded — `mime_type` is recorded from the client-supplied header,
   not verified against the bytes.
3. **Disk fill from arbitrary file types** (pairs with SEC-M5).

**Fix:** add `FileExtensionValidator` to every `FileField`/`ImageField`
and, for image fields, use `ImageField` (which validates the magic
bytes via Pillow) rather than `FileField`. For videos and resumes,
validate extension *and* run a magic-bytes sniff (e.g. `python-magic`)
on save. For HTML/SVG protection in particular, never accept `.html`,
`.svg`, `.htm` extensions on any user-content field.

---

### SEC-H7. `OTP_RATE_LIMIT` is a footgun when the env var is missing

**Status: OPEN.** (Not active in the current deployment — `.env` and
`.env.example` both set `OTP_RATE_LIMIT=20/min`. Flagged because the
code's behavior on missing config is silently insecure.)

[`settings.py:250`](career_college_backend/settings.py#L250):

```python
OTP_RATE_LIMIT = os.getenv('OTP_RATE_LIMIT')
```

No default. If the env var is unset, `os.getenv` returns `None`, so
`settings.OTP_RATE_LIMIT = None`.

The throttle classes do:

```python
# otp_views.py:14 and password_views.py:19
OTP_RATE_LIMIT = getattr(settings, 'OTP_RATE_LIMIT', '20/min')

class OTPGenerateThrottle(AnonRateThrottle):
    rate = OTP_RATE_LIMIT
```

The `'20/min'` default in `getattr` looks like a safe fallback but
**does not fire** when the env var is missing — the settings attribute
exists (as `None`), so `getattr` returns `None`. DRF's
`SimpleRateThrottle.allow_request` treats `rate=None` as "no limit."
Result: all three throttle classes (`OTPGenerateThrottle`,
`OTPVerifyThrottle`, `ForgotPasswordThrottle`) silently become no-ops.

**Why it's not Critical today:** your active `.env` has
`OTP_RATE_LIMIT=20/min`, so throttling works. `.env.example` documents
the same value, so a fresh clone that copies `.env.example` to `.env`
is also safe.

**Why it's still High:** anyone who builds their own `.env` from
memory, or any commit that drops the line from `.env.example`, removes
brute-force protection for OTP and forgot-password — with zero log
output, zero startup warning, and zero response-surface indication.
"Silent on misconfig" + "removes a security control" + "easy to miss
in code review" is the textbook High-severity shape.

**Fix:** put the default in `settings.py` itself, not in the view
modules where it's a no-op trap. Make the floor `5/min` (per SEC-M2)
and let env override.

```python
# settings.py
OTP_RATE_LIMIT = os.getenv('OTP_RATE_LIMIT', '5/min')
```

Then the `getattr(..., '20/min')` lines in the view modules can be
simplified to `settings.OTP_RATE_LIMIT` — no fallback needed, since
settings is the single source of truth.

---

## Medium

### SEC-M1. OTP throttle is IP-based, not email-based

**Status: OPEN.**

`OTPVerifyThrottle` extends `AnonRateThrottle`, which keys on client IP
([DRF docs](https://www.django-rest-framework.org/api-guide/throttling/#anonratethrottle)).
An attacker who controls multiple IPs (rotating proxies, datacenter
egress pool) is not slowed by the per-IP limit.

**Impact:** the OTP space is 10⁶ (6-digit numeric). With a 2-minute
expiry and IP-rotation, an attacker can attempt many more codes than
the rate limit suggests. The OTP is short-lived enough that this is
mitigation-grade, not catastrophic — but the throttle should be keyed
per-email instead.

**Fix:** custom throttle keyed on the request payload's `email` field:

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

`20/min` against a 6-digit OTP gives 1,200 attempts/hour from a single
IP. The OTP space is 1,000,000. With perfect IP rotation, the
brute-force horizon is ~14 hours of sustained attempts to expect a hit.
The 2-minute OTP expiry partially mitigates this (need to land within
two minutes of OTP generation), but the math is tight.

**Fix:** tighten the default to `5/min` per IP, layered on top of the
per-email throttle from SEC-M1. A legitimate user mistyping their OTP
a handful of times in a row doesn't need more.

---

### SEC-M3. No production-hardening settings

**Status: OPEN.**

`settings.py` is missing all of:

- `SECURE_HSTS_SECONDS` — no HSTS header
- `SECURE_HSTS_INCLUDE_SUBDOMAINS`
- `SECURE_HSTS_PRELOAD`
- `SECURE_SSL_REDIRECT` — no automatic HTTP→HTTPS redirect at the Django layer
- `SECURE_PROXY_SSL_HEADER` — Django won't know it's behind HTTPS termination, so `request.is_secure()` returns False and the `Secure` cookie attribute logic gets confused
- `SESSION_COOKIE_SECURE`
- `CSRF_COOKIE_SECURE`
- `CSRF_TRUSTED_ORIGINS` — required for cross-origin POST requests in Django 4.0+

**Impact:** in a production HTTPS deployment behind a reverse proxy
(standard architecture), `request.is_secure()` returns False, so any
cookie/redirect logic relying on it misbehaves. Browsers also won't
upgrade the next visit to HTTPS via HSTS.

**Fix:** add an environment-gated block:

```python
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31_536_000   # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', default='')
```

Run `python manage.py check --deploy` to confirm all warnings are
addressed.

---

### SEC-M4. Access token lifetime is 12 hours

**Status: OPEN.**

[`settings.py:155`](career_college_backend/settings.py#L155):

```python
'ACCESS_TOKEN_LIFETIME': timedelta(hours=12),
```

Industry norm for JWT access tokens is **5–15 minutes**, paired with
longer-lived refresh tokens. The reason: access tokens are bearer
credentials sent on every request; they get captured by proxy logs,
analytics tools, browser dev-tools screenshots, and so on. A 12-hour
window of validity for any single leak is large.

**Fix:** drop to 15 minutes. Refresh tokens already exist
(7 days, with rotation + blacklist) — the frontend silently refreshes
when the access token expires.

```python
'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
```

Note that this also requires the frontend to handle 401 retries via the
refresh endpoint, which the current cookie-helpers setup is already
wired for.

---

### SEC-M5. No global file upload size limit

**Status: OPEN.**

Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` defaults to 2.5 MB (in-memory
threshold), but the **disk-spill ceiling** (`DATA_UPLOAD_MAX_NUMBER_FIELDS`
and `FILE_UPLOAD_MAX_MEMORY_SIZE` aside) is uncapped — anything larger
streams to a temp file on disk. There is no application-level cap on
how big a `video_file` (or any other upload) can be.

**Impact:** a malicious user can upload a 50 GB file to a video lecture
endpoint, filling the disk and DoSing the service. Or upload many large
files in parallel.

**Fix:**

```python
# settings.py
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024     # 10 MB JSON/form bodies
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
# Per-field caps in serializers — videos can be larger:
MAX_VIDEO_UPLOAD_MB = 500
MAX_IMAGE_UPLOAD_MB = 5
MAX_DOCUMENT_UPLOAD_MB = 10
```

Validate per-field size in the upload serializers. Combine with
nginx-level `client_max_body_size` at the reverse-proxy layer for
defence in depth.

---

### SEC-M6. Admin served at default `/admin/` path

**Status: OPEN.**

[`urls.py:23`](career_college_backend/urls.py#L23):

```python
path('admin/', admin.site.urls),
```

Default path; trivially discoverable by automated scanners (every
hour, every Django project gets probed at `/admin/`).

**Impact:** doesn't make the admin vulnerable per se (admin login still
requires valid credentials + CSRF), but it concentrates brute-force
attempts there and makes for noisy logs. Combined with SEC-H4 (no login
throttling), this is the highest-value brute-force target on the
deployment.

**Fix (defence in depth):**

1. Rename: `path('back-office-x7k/', admin.site.urls)` (any non-obvious slug).
2. Restrict by IP at the reverse-proxy layer (nginx `allow`/`deny`).
3. Require staff users to use a separate admin domain (e.g. `admin.example.com`) with VPN-only access.

Don't rely on rename alone — it's security through obscurity. Combine
with throttling + IP allowlist for real protection.

---

### SEC-M7. Changing password does not rotate refresh tokens

**Status: OPEN.**

`ChangePasswordView` ([`password_views.py:108`](authentication/all_views/password_views.py#L108))
updates the password but does not blacklist existing refresh tokens for
the user. The same applies to `ResetPasswordView`.

**Impact:** if an attacker has stolen a refresh token, the legitimate
user changing their password does **not** revoke the attacker's
session. The attacker keeps issuing fresh access tokens via the refresh
endpoint until the refresh token itself expires (7 days).

**Fix:** in both `ChangePasswordSerializer.save()` and
`ResetPasswordSerializer.save()`, blacklist all outstanding refresh
tokens for the user:

```python
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken

for outstanding in OutstandingToken.objects.filter(user=user):
    BlacklistedToken.objects.get_or_create(token=outstanding)
```

Document this in the API testing guide ("changing your password logs
you out of all other devices") so the UX is intentional.

---

## Low

### SEC-L1. `RequestLoggingMiddleware` is a no-op placeholder

**Status: OPEN (dead code).**

[`core/middleware.py:4`](core/middleware.py#L4):

```python
class RequestLoggingMiddleware:
    """Simple request logger placeholder for future project-wide logging."""
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        response = self.get_response(request)
        return response
```

The class does nothing. It's also not actually registered in
`MIDDLEWARE`. Either implement it or delete the file.

---

### SEC-L2. `IsAdminOrReadOnly` permission class appears unused

**Status: OPEN (dead code).**

[`core/permissions.py:20`](core/permissions.py#L20) declares
`IsAdminOrReadOnly`, but a grep of the project finds no view importing
or using it. Dead code in the security-critical permissions module is
a smell — readers may assume it's in use somewhere and not audit
removals.

**Fix:** delete it, or if a planned-future use exists, add a comment
naming the intended consumer.

---

## Out-of-scope follow-ups noticed during the sweep

These don't merit a full finding but are worth surfacing:

- **`ACCOUNT_EMAIL_VERIFICATION = 'none'`** ([`settings.py:196`](career_college_backend/settings.py#L196))
  — allauth's email verification is disabled because the project rolls
  its own OTP flow. Confirm that no allauth-driven login path exists
  that bypasses the OTP check (a brief view audit of any allauth-mounted
  URL should suffice — none appear in `urls.py` currently, so this is
  likely fine).
- **`EMAIL_USE_SSL = False`** is hardcoded ([`settings.py:244`](career_college_backend/settings.py#L244))
  rather than env-driven, while `EMAIL_USE_TLS` is. Some mail providers
  require SSL on port 465; promote to env-driven.
- **`AUTHENTICATION_BACKENDS`** includes `ModelBackend` even though all
  user-facing auth is JWT. ModelBackend is needed by the Django admin
  and by allauth's social account flow. Confirm — and if you ever
  remove admin or social, audit what depends on it.
- **`OTP_RATE_LIMIT` is read at view-import time** (module-level
  `getattr` in the throttle classes). Changing the env var requires a
  process restart — fine, but document it.
- **Dependency CVEs are out of scope here.** Run `pip-audit` (or
  `safety check`) against the lock file as a separate pass.
- **Static analysis is out of scope here.** Run `bandit -r .` for
  Python-level security smell scanning as a separate pass.
- **Secret scanning is out of scope here.** Run `git-secrets` or
  `trufflehog` against the repo history; this audit only confirms
  there's no `.env` checked into the working tree.

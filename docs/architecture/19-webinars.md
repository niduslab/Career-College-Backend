# 19 — Webinars

Live webinars owned by **verified partner institutions**. A webinar is a schedulable, reviewable,
registerable catalog entity — metadata plus an **external meeting link** (Zoom / Google Meet / Jitsi),
**not** a curriculum tree. Live delivery (HD conferencing, screen-share, whiteboard, polls, recording)
is delegated to the external provider; the platform owns scheduling, catalog, registration, and the
publish lifecycle.

App: `webinars/`, mounted at `/api/v1/webinars/`. It mirrors the `courses/` app structure
(`all_models/`, `all_serializers/`, `all_views/`, `services/`, `all_tests/`, thin `models.py` /
`serializers.py` / `views.py` re-export shims).

> Scoping decision: building in-house WebRTC + whiteboard + recording is months of work and the wrong
> call for an MVP. Treating a webinar as a catalog entity that links out to a provider captures the 80%
> win. Provider gives video/polls/recording for free; we own scheduling, catalog, registration, review.
> Source requirement: `cc_docs/CC_SRS_Partner.md` §3.4.

---

## Data model

### `Webinar` (`webinars/all_models/webinar_models.py`)

Inherits `AuthoredModel` (so `created_by` / `last_edited_by` are stamped — the institution is
`created_by`). `clean()` enforces `created_by.user_type == 'partner_institution'`.

| Field | Type | Notes |
|---|---|---|
| `partner_institution` | FK `PartnerInstitutionProfile` (`SET_NULL`, null) | Owner. Set automatically at create, **never API-writable**. |
| `host_expert` | FK User (`SET_NULL`, null) | Single assigned lead host. Set via the `/host/` endpoint. |
| `institutional_speakers` | M2M User | Platform experts credited as speakers (credit-only). |
| `guest_speakers` | JSONField (`default=list`) | External presenters with no account: `[{full_name, title, bio}]`. |
| `category` | FK `CourseCategory` (`SET_NULL`, null) | Reuses the course category taxonomy. |
| `title`, `slug`, `description`, `thumbnail` | | `slug` auto-generated + de-duplicated in `save()`. |
| `scheduled_at` | DateTimeField (null) | Start time, stored UTC. |
| `timezone` | CharField (`default='UTC'`) | Display tz, e.g. `Asia/Dhaka`. |
| `duration_minutes` | PositiveInt (`default=0`) | |
| `max_capacity` | PositiveInt (null) | Null = unlimited. |
| `price` | Decimal (`default=0`, `MinValueValidator(0)`) | `0` = free (payment not integrated). |
| `meeting_provider` | CharField | `zoom` \| `meet` \| `jitsi` \| `other`. |
| `meeting_url` | URLField (null) | External join link — **registrant-only, never in catalog**. |
| `status` | CharField | `draft` \| `published` \| `archived`. |
| `is_published` | Bool (`db_index`) | Denormalized `status == published` flag for fast catalog queries. |
| `published_at` | DateTimeField (null) | Stamped on first publish; cleared when not published. |

`save()` owns three invariants: slug generation (`slugify(title)` + numeric suffix on collision),
`is_published = (status == PUBLISHED)`, and `published_at` set-on-publish / cleared-otherwise. Indexes:
`(status, -created_at)`, `(is_published, scheduled_at)`, `(partner_institution, status)`.

### Presenters — three distinct roles, do not conflate

| Role | Storage | Rights | How set |
|---|---|---|---|
| `host_expert` | FK→User (single) | **Publishes** the webinar; required before publish | `POST/DELETE /<pk>/host/` (`WebinarHostView`, institution-only) |
| `institutional_speakers` | M2M→User | **Credit-only, no authoring rights** | `institutional_speaker_ids` write-only list in create/update payload (full replace) |
| `guest_speakers` | JSONField | Display only (no account) | `guest_speakers` list in create/update payload |

Rule: **presenter with a platform account → FK/M2M; no account → JSON.** `host_expert` and every id in
`institutional_speaker_ids` must be an **active affiliated expert of the owning institution**, validated
by `_get_active_expert_user` (`courses/services/institution_course_service.py`) — the same rule as the
course roster and host. Foreign / inactive / unknown → `WebinarError(422)`. Overlap between
`host_expert` and `institutional_speakers` is allowed.

### `WebinarRegistration` (`webinars/all_models/registration_models.py`)

Parallels `Enrollment`. Inherits `TimestampedModel`.

| Field | Type | Notes |
|---|---|---|
| `user` | FK User (`CASCADE`) | Learner. |
| `webinar` | FK `Webinar` (`CASCADE`) | |
| `is_active` | Bool (`default=True`, `db_index`) | Soft-cancel flag (mirrors enrollment). |
| `attended` | Bool (`default=False`) | Reserved for the live-day join flow (later phase). |
| `joined_at` | DateTimeField (null) | Reserved (later phase). |

`UniqueConstraint(user, webinar)` — one row per learner per webinar; a cancelled row is reactivated
rather than duplicated. `clean()` enforces learner-only + published-only. Indexes:
`(user, is_active, -created_at)`, `(webinar, is_active)`.

---

## Status machine

`Webinar.transition_to(new_status, actor=None)` is the **single entry point** — never set `status`
directly. Three states, **no approval gates**: the assigned host expert publishes directly (a webinar
has no curriculum to vet). There is **no** institution-forward step, **no** admin review, and **no**
`rejection_reason` field — do not reintroduce them.

```
draft ──publish──► published ──archive──► archived
  ▲                                           │
  └──────────────── rework ───────────────────┘
```

| From | To | Endpoint | Who |
|---|---|---|---|
| `draft` | `published` | `POST /<pk>/publish/` (`WebinarPublishView`) | Assigned **host expert** only (scoped `host_expert=request.user`; institution user → 404) |
| `published` | `archived` | `POST /<pk>/archive/` (`WebinarArchiveView`) | Owner, host, or admin |
| `archived` | `draft` | `POST /<pk>/rework/` (`WebinarReworkView`) | Owner or host |

Publishing runs `_validate_webinar_completeness()`: `title`, `description`, future `scheduled_at`,
`duration_minutes`, `meeting_url`, and an assigned `host_expert` — collects all problems into one
`ValidationError`. Completeness failure → 400 (field errors); illegal transition → 422 (string message).
Views map both via `_transition_error_response` (the project-wide `message_dict` → 400 / `messages[0]` →
422 rule).

`is_editable()` = `status in ('draft', 'archived')`. Publishing freezes editing; rework reopens it.

---

## Editing scope — GET vs PATCH

`WebinarDetailView` (`/<int:pk>/`) is permission-gated `IsVerifiedCourseCreator` but scopes rows in
`_get_webinar(request, pk, owner_only=)`:

- **GET** — visible to the owning institution **or** the assigned host (`Q(created_by) | Q(host_expert)`).
  A host can read the webinar they host.
- **PATCH** — **institution-only** (`owner_only=True` → `Q(created_by)` only). A host expert patching
  metadata → 404. Webinar authoring stays with the institution; the host's only mutating power is
  `/publish/`. This is unlike the course roster, where assigned experts edit content.

Metadata edits are guarded by `_guard_editable(webinar)` (422 when not `draft`/`archived`).

---

## Services

### `webinars/services/webinar_service.py`

- `WebinarError(message, http_status=400)` — business-rule exception carrying an HTTP status (mirrors
  `InstitutionCourseError` / `ReviewError`). Raised inside `serializer.save()` and caught by the
  create/patch views, returned in the standard envelope with `exc.http_status`.
- `get_catalog_webinars()` — published webinars, `select_related` owner/category/institution/host,
  ordered `scheduled_at` (soonest first).
- `filter_catalog_webinars(qs, params)` — optional `?category=<id>` and `?upcoming=true` filters. A
  non-integer `category` raises a Django `ValidationError` → 400.
- `assign_webinar_host(webinar, institution_profile, expert_user_id)` / `clear_webinar_host(...)` —
  institution-scoped (`partner_institution_id` mismatch → 404), editable-only (else 422), validates the
  expert via `_get_active_expert_user`.
- `set_institutional_speakers(webinar, institution_profile, expert_user_ids)` — full replace (idempotent;
  `[]` clears). De-dups ids, validates each via `_get_active_expert_user`, `.set()` inside a transaction.

### `webinars/services/registration_service.py`

- `register_for_webinar(user, webinar)` (`@transaction.atomic`) — learner-only + published-only guards;
  reactivates a cancelled row or creates a new one; dispatches `WEBINAR_REGISTERED` on commit.
- **Capacity concurrency:** when `max_capacity is not None`, the function takes
  `Webinar.objects.select_for_update()` on the webinar row **before** counting active registrations.
  This serializes concurrent first-time registrants (neither holds a row to lock) so two callers can't
  both pass the `>= max_capacity` check and over-subscribe. Postgres enforces the lock; SQLite (tests)
  no-ops it silently.

---

## Endpoints

`webinars/urls.py`. Access-denied policy follows the project-wide rule: **slug → 403, numeric ID → 404.**

### Authoring (institution-owned)

| Method | Endpoint | View | Auth | Purpose |
|---|---|---|---|---|
| GET | `` (root) | `WebinarListAPIView` | `IsVerifiedCourseCreator` | Own webinars (owner **or** host), paginated |
| POST | `create/` | `WebinarCreateAPIView` | `IsVerifiedPartnerInstitution` | Create a draft (institution only) |
| GET | `<int:pk>/` | `WebinarDetailView` | `IsVerifiedCourseCreator` | Detail (owner or host) |
| PATCH | `<int:pk>/` | `WebinarDetailView` | `IsVerifiedCourseCreator`, **owner-scoped** | Edit metadata (institution only) |
| POST/DELETE | `<int:pk>/host/` | `WebinarHostView` | `IsVerifiedPartnerInstitution` | Assign / clear host expert |

### Status transitions

| Method | Endpoint | View | Auth | Transition |
|---|---|---|---|---|
| POST | `<int:pk>/publish/` | `WebinarPublishView` | `IsVerifiedCourseCreator`, host-scoped | `draft → published` |
| POST | `<int:pk>/rework/` | `WebinarReworkView` | `IsVerifiedCourseCreator`, owner/host | `archived → draft` |
| POST | `<int:pk>/archive/` | `WebinarArchiveView` | `IsEmailVerified` (owner/host/admin) | `published → archived` |

### Public catalog + learner registration

| Method | Endpoint | View | Auth | Purpose |
|---|---|---|---|---|
| GET | `catalog/` | `CatalogWebinarListView` | `AllowAny` | Published webinars, soonest first. `?category=`, `?upcoming=true` |
| GET | `catalog/<slug>/` | `CatalogWebinarDetailView` | `AllowAny` | Public detail — **no `meeting_url`** |
| POST | `<slug>/register/` | `WebinarRegisterView` | `IsLearnerUser` | Register (201; duplicate → 422; capacity → 422) |
| GET | `my-webinars/` | `MyWebinarsListView` | `IsLearnerUser` | Learner's active registrations |
| GET | `my-webinars/<slug>/` | `MyWebinarDetailView` | `IsLearnerUser` | Registrant detail — **exposes `meeting_url`**; 403 if not registered |

---

## Serializers (`webinars/all_serializers/`)

`meeting_url` visibility is enforced by **serializer choice**, not conditional stripping — absence is a
stronger guarantee (same pattern as the learner-safe course serializers):

| Serializer | `meeting_url`? | Used by |
|---|---|---|
| `WebinarSerializer` (authoring read) | ✅ | List / detail / create / patch responses (owner + host) |
| `CatalogWebinarListSerializer` / `CatalogWebinarDetailSerializer` | ❌ | Public catalog |
| `RegistrantWebinarSerializer` (nested in `WebinarRegistrationSerializer`) | ✅ | `my-webinars/` (caller has registered) |

- `WebinarCreateUpdateSerializer` — write path. `category` is a `PrimaryKeyRelatedField` (active only);
  `guest_speakers` validated by `GuestSpeakerSerializer` (many); `institutional_speaker_ids` is a
  write-only int list routed to `set_institutional_speakers`. `create()` stamps `created_by`,
  `last_edited_by`, `partner_institution` from the request user's profile; `update()` re-stamps
  `last_edited_by`. Both run in `transaction.atomic`. Title min-length 5.
- Read serializers expose `host_expert` / `institutional_speakers` / `created_by` / `last_edited_by` as
  nested `InstructorBriefSerializer`; list/detail loaders `prefetch_related('institutional_speakers')`
  and `select_related` the FKs to avoid N+1.

---

## Notifications

Two `webinar.*` event types (`notifications/models.py` → `NotificationEventType`), both wired into the
three notification maps:

| Event | Recipients | Category (`EVENT_TO_CATEGORY`) | Email template (`_EVENT_TEMPLATE_MAP`) |
|---|---|---|---|
| `WEBINAR_PUBLISHED` | Owning institution **+** host | `COURSE_MANAGEMENT` | `notifications/emails/webinar_published.html` |
| `WEBINAR_REGISTERED` | Registering learner | `COURSE_ACTIVITY` | `notifications/emails/webinar_registered.html` |

Both dispatch via `transaction.on_commit` (publish in `WebinarPublishView`; registration in
`_dispatch_registration_notification`). Each event delivers an in-app feed row, a WS push, **and** an
email (subject to the recipient's per-category preference). A builder in
`notifications/services/builders.py` (`_webinar_published` / `_webinar_registered`) supplies the
title/body and a `{webinar_slug}` data payload.

> Adding a `webinar.*` event requires **four** edits or it half-works: `NotificationEventType`, a builder
> in `builders.py`, `EVENT_TO_CATEGORY` (else email preference can't be honored), and `_EVENT_TEMPLATE_MAP`
> (else the email task logs `no template …, skipping`). Future events (`webinar.reminder`,
> `webinar.recording_available`) follow the same four-point wiring.

---

## Not built (future phases)

Deliberately out of scope for this slice — do not assume these exist:

- **Live-day flow** — `/join/`, `attended` / `joined_at` stamping, `published → live → ended` states.
- **Reminders** — Celery-beat `send_webinar_reminders_task` (T-24h / T-1h) + `webinar.reminder` /
  `webinar.starting_soon` events.
- **Recording** — `recording_url` field + `webinar.recording_available` event.
- **Ratings** — reuse the `CourseReview` pattern later.
- **Paid webinars** — `price` exists but payment is not integrated; registration is free-only.

---

## File map

| File | Responsibility |
|---|---|
| `webinars/all_models/webinar_models.py` | `Webinar` model, status machine, completeness check, slug/publish invariants |
| `webinars/all_models/registration_models.py` | `WebinarRegistration` model |
| `webinars/services/webinar_service.py` | `WebinarError`, catalog loaders, host + speaker assignment |
| `webinars/services/registration_service.py` | Registration + capacity lock + reactivation + notify |
| `webinars/all_serializers/webinar_serializers.py` | Authoring read + create/update, `GuestSpeakerSerializer` |
| `webinars/all_serializers/catalog_serializers.py` | Public catalog serializers (no `meeting_url`) |
| `webinars/all_serializers/registration_serializers.py` | Registrant serializers (with `meeting_url`) |
| `webinars/all_views/webinar_views.py` | List / create / detail / patch |
| `webinars/all_views/status_views.py` | Publish / rework / archive |
| `webinars/all_views/host_views.py` | Host assign / clear |
| `webinars/all_views/catalog_views.py` | Public catalog list / detail |
| `webinars/all_views/registration_views.py` | Register / my-webinars |
| `webinars/all_tests/test_webinar_flow.py` | End-to-end flow, editing scope, transitions, capacity, notifications |

See also `docs/api-testing/postman-webinars.md` for the manual-test walkthrough.

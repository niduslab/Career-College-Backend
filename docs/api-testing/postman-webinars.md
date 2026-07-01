# Postman Guide — Webinars (Authoring · Host · Publish · Catalog · Registration)

Manual API testing for the webinar core slice. A webinar is metadata + an external meeting link
(Zoom/Meet/Jitsi) — **no curriculum tree**. Every webinar is owned by a verified partner institution;
the institution creates it, assigns a **host expert**, optionally credits additional presenters — either
**institutional speakers** (its own active affiliated experts, picked by User id) or external **guest
speakers** (typed inline, no account) — then the **host publishes it directly** (`/publish/`). **There
are no institution-forward or admin-approval gates.** Learners discover it in the public catalog and
register for free.

> **Presenters, three roles:** `host_expert` (single lead, assigned via `/host/`, required before
> publishing, and the only actor who may publish), `institutional_speakers` (M2M of active affiliated
> experts, credit-only — no authoring rights), `guest_speakers` (JSON list of external people with no
> platform account). Institutional and guest speakers are set together in the create/update payload and
> can be mixed freely on one webinar.

Flow under test:

1. **Authoring** — institution creates / edits a webinar (institution-only).
2. **Host assignment** — institution assigns one of its active experts as host (or clears it).
3. **Publish** — the assigned host publishes directly (`draft → published`). No review steps.
4. **Catalog** — public list + detail (no `meeting_url`).
5. **Registration** — learner registers; `my-webinars` exposes the join link.

> **Prerequisite feature:** complete the partner-institution setup first (see
> `postman-partner-institution.md`) so you have a **verified** institution and at least one **active
> affiliated expert**. Webinars reuse that exact ownership + expert model.

---

## Environment Variables

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `institution_token` | `Bearer eyJ...` | Verified partner institution (`user_type=partner_institution`, `is_verified=true`) |
| `other_institution_token` | `Bearer eyJ...` | A second, unrelated verified institution (cross-institution 404 checks) |
| `expert_token` | `Bearer eyJ...` | Active affiliated expert of `institution_token` (the assigned host + publisher) |
| `admin_token` | `Bearer eyJ...` | Platform admin (`user_type=admin` / `is_staff`) — only used to archive a published webinar |
| `learner_token` | `Bearer eyJ...` | Any learner (registration + negative authz) |
| `learner2_token` | `Bearer eyJ...` | A second learner (capacity test) |
| `expert_user_id` | _(filled during tests)_ | The expert's **User** PK (host assignment + institutional speaker) |
| `expert_user_id_2` | _(filled during tests)_ | A second active affiliated expert's User PK (multi-speaker test) |
| `foreign_expert_user_id` | _(filled during tests)_ | A User PK that is NOT an active expert of `institution_token` |
| `webinar_pk` | _(filled during tests)_ | PK of a webinar owned by the institution |
| `webinar_slug` | _(filled during tests)_ | Slug of that webinar (catalog / register / my-webinars) |

> **Celery worker:** notifications (`webinar.*`) dispatch asynchronously. Start the worker
> (`celery -A career_college_backend worker -l info -Q celery,notifications`) to see them; the API
> responses themselves do not depend on it.

---

## Access-Denied Policy (applies throughout)

| URL identifier | No-access response |
|---|---|
| **Slug** (`catalog/<slug>/`, `<slug>/register/`, `my-webinars/<slug>/`) | **403** (public slugs leak nothing) |
| **Numeric ID** (`<pk>/`, `<pk>/host/`, `<pk>/publish/`, …) | **404** (existence not leaked) |
| Wrong `user_type` on a typed endpoint | 403 |
| Unverified / non-institution on an institution-gated endpoint | 403 |

`meeting_url` appears **only** in registrant-facing payloads (`my-webinars`), never in the catalog.

---

## Group 1: Authoring (Partner Institution)

> All of Group 1 uses `institution_token` (verified institution).

### 1.1 Create a webinar — happy path

```
POST {{base_url}}/webinars/create/
Authorization: {{institution_token}}
Content-Type: application/json

{
    "title": "Live Intro to Applied AI",
    "description": "A 90-minute live session on shipping ML in production.",
    "scheduled_at": "2026-09-01T15:00:00Z",
    "timezone": "Asia/Dhaka",
    "duration_minutes": 90,
    "max_capacity": 100,
    "price": "0.00",
    "meeting_provider": "zoom",
    "meeting_url": "https://zoom.example.com/j/123456789",
    "institutional_speaker_ids": [ {{expert_user_id}} ],
    "guest_speakers": [
        { "full_name": "Dr. Jane Guest", "title": "Head of AI, Acme", "bio": "20y in applied ML." }
    ]
}
```

**Expected:** `201 Created`, `data.status == "draft"`, `data.host_expert == null`, `partner_institution`
auto-set, `guest_speakers` echoed back, `data.institutional_speakers` is a one-element list of the
expert brief (`institutional_speaker_ids` is write-only — the **response** field is the nested
`institutional_speakers`). Save `data.id` → `webinar_pk`, `data.slug` → `webinar_slug`.

> `institutional_speaker_ids` is optional and a **full replace**: omit it to leave speakers untouched,
> send `[]` to clear them. Each id must be an **active affiliated expert of the calling institution** —
> a foreign / inactive / unknown id → `422` (see 1.8). It is independent of `host_expert`; the same
> expert may be both host and a listed speaker.
>
> ⚠️ **Write key vs read key — common 200-but-no-change trap.** You **write** speakers with
> `institutional_speaker_ids` (a list of **User ids**). The response field `institutional_speakers`
> (nested objects) is **read-only** — sending *that* key is silently ignored by DRF and the request
> still returns `200` with the speaker set unchanged. If a PATCH "succeeds" but speakers don't update,
> check you sent `institutional_speaker_ids`, not `institutional_speakers`. Likewise the ids are the
> expert's **`user_id`** from `/api/v1/auth/partner/experts/`, **not** that list's `id`
> (which is the `InstructorProfile` pk).

**Postman Test:**
```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
const d = pm.response.json().data;
pm.expect(d.status).to.equal("draft");
pm.expect(d.host_expert).to.equal(null);
pm.environment.set("webinar_pk", d.id);
pm.environment.set("webinar_slug", d.slug);
```

---

### 1.2 Malformed guest speaker — 400

```
POST {{base_url}}/webinars/create/
Authorization: {{institution_token}}
Content-Type: application/json

{
    "title": "Bad Guest Webinar",
    "description": "desc",
    "guest_speakers": [ { "title": "No name supplied" } ]
}
```

**Expected:** `400 Bad Request`, `errors.guest_speakers` reports the missing `full_name`.

---

### 1.3 Non-institution cannot create — 403

```
POST {{base_url}}/webinars/create/
Authorization: {{expert_token}}
Content-Type: application/json

{ "title": "Expert Tries To Create", "description": "Should be blocked." }
```

**Expected:** `403 Forbidden` (`IsVerifiedPartnerInstitution`). A learner token → `403` too.

---

### 1.4 List my institution's webinars

```
GET {{base_url}}/webinars/
Authorization: {{institution_token}}
```

**Expected:** `200 OK`, paginated; includes the webinar from 1.1. (Scope: `created_by` = caller OR
`host_expert` = caller, so the assigned host also sees it here with `expert_token`.)

---

### 1.5 Detail (GET)

```
GET {{base_url}}/webinars/{{webinar_pk}}/
Authorization: {{institution_token}}
```

**Expected:** `200 OK`, full authoring payload including `meeting_url`, `status`, `host_expert`,
`institutional_speakers`, `guest_speakers`, `created_by`, `last_edited_by`.

---

### 1.6 Edit metadata (PATCH)

```
PATCH {{base_url}}/webinars/{{webinar_pk}}/
Authorization: {{institution_token}}
Content-Type: application/json

{ "duration_minutes": 120, "guest_speakers": [ { "full_name": "Dr. Jane Guest", "title": "CTO, Acme" } ] }
```

**Expected:** `200 OK`, fields updated, `last_edited_by` now the caller. PATCH is allowed only while the
webinar `is_editable()` (draft / archived) — editing a `published` webinar → `422`.

---

### 1.7 Foreign webinar — 404

```
GET {{base_url}}/webinars/{{webinar_pk}}/
Authorization: {{other_institution_token}}
```

**Expected:** `404 Not Found` (numeric pk — existence not leaked).

---

### 1.8 Foreign / inactive institutional speaker — 422

```
PATCH {{base_url}}/webinars/{{webinar_pk}}/
Authorization: {{institution_token}}
Content-Type: application/json

{ "institutional_speaker_ids": [ {{foreign_expert_user_id}} ] }
```

**Expected:** `422 Unprocessable Entity`,
`message: "This user is not an active expert of your institution."` (same rule as host assignment —
the speaker set is rejected atomically, none are added).

---

### 1.9 Mix institutional + guest speakers

```
PATCH {{base_url}}/webinars/{{webinar_pk}}/
Authorization: {{institution_token}}
Content-Type: application/json

{
    "institutional_speaker_ids": [ {{expert_user_id}}, {{expert_user_id_2}} ],
    "guest_speakers": [
        { "full_name": "Dr. Jane Guest", "title": "Head of AI, Acme" },
        { "full_name": "Sam External", "title": "Founder, Beta Labs" }
    ]
}
```

**Expected:** `200 OK`. `data.institutional_speakers` is a two-element list of expert briefs;
`data.guest_speakers` is the two typed entries. Both sets coexist on the one webinar. Re-sending with a
shorter `institutional_speaker_ids` replaces (does not append) — verify the count drops.

```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
const d = pm.response.json().data;
pm.expect(d.institutional_speakers).to.have.lengthOf(2);
pm.expect(d.guest_speakers).to.have.lengthOf(2);
```

> Editing speakers obeys the same `is_editable()` gate as all metadata — a `published` webinar → `422`.

---

## Group 2: Host Assignment

> `webinar_pk` owned by `institution_token`; `expert_user_id` is an **active affiliated expert's User PK**.

### 2.1 Assign host — happy path

```
POST {{base_url}}/webinars/{{webinar_pk}}/host/
Authorization: {{institution_token}}
Content-Type: application/json

{ "expert_user_id": {{expert_user_id}} }
```

**Expected:** `200 OK`, `message: "Host expert assigned."`, `data.host_expert` is the expert brief.

```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("host set", () => pm.expect(pm.response.json().data.host_expert.id).to.eql(Number(pm.environment.get("expert_user_id"))));
```

---

### 2.2 Missing `expert_user_id` — 400

```
POST {{base_url}}/webinars/{{webinar_pk}}/host/
Authorization: {{institution_token}}
Content-Type: application/json

{ }
```

**Expected:** `400 Bad Request`, `message: "expert_user_id is required."`.

---

### 2.3 Non-affiliated / inactive expert — 422

```
POST {{base_url}}/webinars/{{webinar_pk}}/host/
Authorization: {{institution_token}}
Content-Type: application/json

{ "expert_user_id": {{foreign_expert_user_id}} }
```

**Expected:** `422 Unprocessable Entity`,
`message: "This user is not an active expert of your institution."`.

---

### 2.4 Clear the host

```
DELETE {{base_url}}/webinars/{{webinar_pk}}/host/
Authorization: {{institution_token}}
```

**Expected:** `200 OK`, `message: "Host expert cleared."`. (Clearing when no host is set → `422`.)
Re-assign via 2.1 before continuing to Group 3.

---

### 2.5 Foreign webinar — 404

```
POST {{base_url}}/webinars/{{webinar_pk}}/host/
Authorization: {{other_institution_token}}
Content-Type: application/json

{ "expert_user_id": {{expert_user_id}} }
```

**Expected:** `404 Not Found`.

---

## Group 3: Host Direct Publish

State machine: `draft → published → archived`, with `archived → draft` (rework). **No approval gates** —
the assigned host publishes directly. **Never** set status directly — only the transition endpoints move it.

> Precondition: webinar in `draft` with a host assigned (Group 2.1) and all required fields
> (`title`, `description`, `scheduled_at` in the **future**, `duration_minutes`, `meeting_url`).

### 3.1 `/publish/` before complete — 400

Create a bare webinar (no `meeting_url`), assign a host, then:

```
POST {{base_url}}/webinars/{{webinar_pk}}/publish/
Authorization: {{expert_token}}
```

**Expected:** `400 Bad Request`, `errors` lists the missing field(s) — e.g. `meeting_url`, or
`scheduled_at` if it is in the past, or `host_expert` if none assigned.

---

### 3.2 Host publishes — happy path

Using the complete webinar from Group 1–2, authenticated as the **assigned host**:

```
POST {{base_url}}/webinars/{{webinar_pk}}/publish/
Authorization: {{expert_token}}
```

**Expected:** `200 OK`, `data.status == "published"`, `data.is_published == true`, `published_at` set.
The institution + host receive `webinar.published`. The webinar now appears in the public catalog.

```javascript
pm.test("published", () => {
    const d = pm.response.json().data;
    pm.expect(d.status).to.equal("published");
    pm.expect(d.is_published).to.equal(true);
});
```

> The institution account is **not** the host → `POST .../publish/` with `institution_token` → **404**
> (scoped to `host_expert=request.user`). Only the assigned host may publish.

---

### 3.3 Archive a published webinar

```
POST {{base_url}}/webinars/{{webinar_pk}}/archive/
Authorization: {{institution_token}}    (or {{expert_token}} / {{admin_token}})
```

**Expected:** `200 OK`, `data.status == "archived"` (drops out of the catalog).

---

### 3.4 Rework an archived webinar

```
POST {{base_url}}/webinars/{{webinar_pk}}/rework/
Authorization: {{expert_token}}    (or {{institution_token}})
```

**Expected:** `200 OK`, `data.status == "draft"` — editable again, ready to re-publish.

---

## Group 4: Public Catalog (no auth)

### 4.1 List published webinars

```
GET {{base_url}}/webinars/catalog/
```

**Expected:** `200 OK`, paginated, soonest first. Optional filters: `?category=<id>`, `?upcoming=true`.
Draft / unpublished webinars are excluded. **No `meeting_url`** on any row.

### 4.2 Catalog detail — hides meeting_url

```
GET {{base_url}}/webinars/catalog/{{webinar_slug}}/
```

**Expected:** `200 OK`. Payload has `host_expert`, `guest_speakers`, schedule, capacity, price — and
**no `meeting_url`** key.

```javascript
pm.test("no meeting_url in catalog", () => pm.expect(pm.response.json().data).to.not.have.property("meeting_url"));
```

### 4.3 Unpublished slug — 404

```
GET {{base_url}}/webinars/catalog/{{webinar_slug}}/
```
(while the webinar is still `draft`)

**Expected:** `404 Not Found` (filtered to `is_published=true`).

---

## Group 5: Learner Registration

> Precondition: webinar is `published` (Group 3.2).

### 5.1 Register — happy path

```
POST {{base_url}}/webinars/{{webinar_slug}}/register/
Authorization: {{learner_token}}
```

**Expected:** `201 Created`, `message: "Registered successfully."`, `data.webinar` summary returned.
Learner receives `webinar.registered`.

### 5.2 Duplicate registration — 422

Re-run 5.1 with the same learner.

**Expected:** `422 Unprocessable Entity`, `message: "You are already registered for this webinar."`.

### 5.3 Capacity reached — 422

On a webinar with `max_capacity: 1` already filled by `learner_token`:

```
POST {{base_url}}/webinars/{{webinar_slug}}/register/
Authorization: {{learner2_token}}
```

**Expected:** `422 Unprocessable Entity`, `message: "This webinar has reached its capacity."`.

### 5.4 My webinars — list

```
GET {{base_url}}/webinars/my-webinars/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, paginated, the learner's active registrations with nested webinar summary
(includes `meeting_url`).

### 5.5 My webinar detail — exposes meeting_url

```
GET {{base_url}}/webinars/my-webinars/{{webinar_slug}}/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, `data.webinar.meeting_url` present (the registrant's join link).

```javascript
pm.test("meeting_url visible to registrant", () => pm.expect(pm.response.json().data.webinar.meeting_url).to.be.a("string"));
```

### 5.6 Not registered — 403

```
GET {{base_url}}/webinars/my-webinars/{{webinar_slug}}/
Authorization: {{learner2_token}}    (never registered)
```

**Expected:** `403 Forbidden` (slug-based — `"You are not registered for this webinar."`).

### 5.7 Register for an unpublished webinar — 404

```
POST {{base_url}}/webinars/{{webinar_slug}}/register/
Authorization: {{learner_token}}
```
(while the webinar is `draft`)

**Expected:** `404 Not Found` (`get_object_or_404(..., is_published=True)`).

---

## Response Shape Reference

**Authoring detail / create (institution-facing — includes `meeting_url`):**
```json
{
    "success": true,
    "data": {
        "id": 10,
        "title": "Live Intro to Applied AI",
        "slug": "live-intro-to-applied-ai",
        "description": "A 90-minute live session...",
        "thumbnail": null,
        "scheduled_at": "2026-09-01T15:00:00Z",
        "timezone": "Asia/Dhaka",
        "duration_minutes": 90,
        "max_capacity": 100,
        "price": "0.00",
        "meeting_provider": "zoom",
        "meeting_url": "https://zoom.example.com/j/123456789",
        "status": "draft",
        "is_published": false,
        "published_at": null,
        "host_expert": { "id": 47, "full_name": "Jane Expert", "email": "jane@example.com" },
        "institutional_speakers": [ { "id": 47, "full_name": "Jane Expert", "email": "jane@example.com" } ],
        "guest_speakers": [ { "full_name": "Dr. Jane Guest", "title": "Head of AI, Acme", "bio": "..." } ],
        "partner_institution": { "id": 1, "institution_name": "Acme Institute", "slug": "acme-institute" },
        "category": null,
        "created_by": { "id": 3, "full_name": "Acme Institute", "email": "inst@example.com" },
        "last_edited_by": { "id": 3, "full_name": "Acme Institute", "email": "inst@example.com" },
        "created_at": "...",
        "updated_at": "..."
    }
}
```

**Catalog detail (public — note: NO `meeting_url`):**
```json
{
    "success": true,
    "data": {
        "id": 10, "title": "...", "slug": "...", "description": "...", "thumbnail": null,
        "scheduled_at": "2026-09-01T15:00:00Z", "timezone": "Asia/Dhaka",
        "duration_minutes": 90, "max_capacity": 100, "price": "0.00",
        "partner_institution": { "id": 1, "institution_name": "Acme Institute", "slug": "acme-institute" },
        "category": null,
        "host_expert": { "id": 47, "full_name": "Jane Expert", "email": "jane@example.com" },
        "institutional_speakers": [ { "id": 47, "full_name": "Jane Expert", "email": "jane@example.com" } ],
        "guest_speakers": [ { "full_name": "Dr. Jane Guest", "title": "Head of AI, Acme", "bio": "..." } ],
        "published_at": "..."
    }
}
```

**Registration (registrant-facing — webinar summary includes `meeting_url`):**
```json
{
    "success": true,
    "message": "Registered successfully.",
    "data": {
        "id": 5, "is_active": true, "attended": false, "joined_at": null,
        "created_at": "...", "updated_at": "...",
        "webinar": { "id": 10, "title": "...", "slug": "...", "meeting_url": "https://zoom.example.com/j/123456789", "...": "..." }
    }
}
```

**Error (4xx):**
```json
{ "success": false, "message": "This user is not an active expert of your institution." }
```

---

## Error Code Summary

| Scenario | Status |
|----------|--------|
| Non-institution (expert/learner) creates webinar | 403 |
| Malformed `guest_speakers` entry (missing `full_name`) | 400 |
| `institutional_speaker_ids` with a foreign / inactive / unknown expert id | 422 |
| Sending read-only `institutional_speakers` instead of `institutional_speaker_ids` | 200 (silently ignored — no change) |
| Edit a non-draft/archived (i.e. published) webinar (incl. speakers) | 422 |
| Foreign webinar (numeric pk) — author / host endpoints | 404 |
| Assign host: missing `expert_user_id` | 400 |
| Assign host: non-affiliated / inactive expert | 422 |
| Clear host when none set | 422 |
| `/publish/` while incomplete (fields / future date / no host) | 400 |
| `/publish/` by the institution (not the host) | 404 |
| Catalog / register on unpublished slug | 404 |
| `my-webinars/<slug>/` when not registered | 403 |
| Duplicate registration | 422 |
| Capacity reached | 422 |

---

## Recommended Run Order

```
─ authoring ─
1.1  Create webinar (institution)     → save webinar_pk, webinar_slug
1.6  Edit metadata                    → 200
1.9  Mix institutional + guest speakers → 2 + 2

─ host ─
2.1  Assign host expert               → host set

─ publish ─
3.2  Host /publish/                   → published, is_published=true

─ catalog (public) ─
4.1  Catalog list                     → webinar present
4.2  Catalog detail                   → no meeting_url

─ registration ─
5.1  Learner register                 → 201
5.5  My-webinar detail                → meeting_url visible

─ error paths ─
1.2  Malformed guest → 400
1.8  Foreign institutional speaker → 422
1.3  Expert creates → 403
1.7  Foreign webinar → 404
2.3  Non-affiliated host → 422
3.1  Incomplete /publish/ → 400
3.2* Institution /publish/ → 404
4.3  Unpublished catalog slug → 404
5.2  Duplicate register → 422
5.3  Capacity reached → 422
5.6  Not-registered my-webinar → 403
```

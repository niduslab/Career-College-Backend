# 03) Profiles

One `User` record; separate profile table per role. Profiles are auto-created by a Django signal
when the user row is first saved.

## Key files

| File | Purpose |
|------|---------|
| `authentication/models.py` | `LearnerProfile`, `InstructorProfile`, `PartnerInstitutionProfile`, `Education`, `WorkExperience` |
| `authentication/signals.py` | `post_save` signal that auto-creates the correct profile row |
| `authentication/all_views/profile_views.py` | Private profile management + public profile listing/detail |
| `authentication/serializers.py` | Profile serializers |
| `authentication/urls.py` | Profile endpoint routing |

---

## Auto-creation via signal

When a new `User` row is created (`created=True` on `post_save`), `authentication/signals.py`
fires and creates the matching profile:

```
User.objects.create_user(...)
          │
          ▼  post_save signal fires (created=True)
          │
  user.user_type == 'learner'            → LearnerProfile.objects.create(user=user)
  user.user_type == 'instructor'         → InstructorProfile.objects.create(user=user)
  user.user_type == 'partner_institution'→ PartnerInstitutionProfile.objects.create(
                                              user=user,
                                              institution_name=user.full_name
                                           )
```

The profile row always exists after user creation — views never need to handle a missing profile.

---

## `user_type` → profile mapping

| `user_type` | Profile model | OneToOne FK |
|-------------|--------------|-------------|
| `learner` | `LearnerProfile` | `user` |
| `instructor` | `InstructorProfile` | `user` |
| `partner_institution` | `PartnerInstitutionProfile` | `user` |
| `admin` | *(no profile model)* | — |

---

## Model: `LearnerProfile`

**Link:** `user` (OneToOneField → `User`)

**Personal:**
- `profile_photo` (ImageField)
- `headline` — short tagline
- `bio` — longer description
- `date_of_birth`

**Location:**
- `city`, `state`, `country`

**Learning context:**
- `experience_level` — `student | entry | mid | senior | expert`
- `learning_goal` (TextField)
- `interests` (JSONField — list of interest strings)
- `preferred_language`

**Social links:**
- `linkedin_url`, `github_url`, `website_url`

**Visibility:**
- `is_profile_public` — controls whether profile appears in public browse endpoints

---

## Model: `InstructorProfile`

**Link:** `user` (OneToOneField → `User`)

**Personal:**
- `profile_photo`, `headline`, `bio`

**Location:**
- `city`, `state`, `country`

**Professional:**
- `specialization` (JSONField — list of specialization strings)
- `years_of_experience`
- `current_title`, `current_organization`

**Social links:**
- `linkedin_url`, `github_url`, `website_url`

**Status:**
- `is_verified` — set to `True` when identity verification is approved (see doc 07)
- `is_accepting_students`

**Institution affiliation:**
- `affiliated_institution` (FK → `PartnerInstitutionProfile`, nullable)
- `onboarding_source` — `self_registered | institution_onboarded`
- `affiliation_status` — `pending | active | removed`
- `affiliated_at` (DateTimeField, nullable)

---

## Model: `PartnerInstitutionProfile`

**Link:** `user` (OneToOneField → `User`)

**Branding:**
- `logo` (ImageField)
- `cover_image` (ImageField)
- `institution_name`, `slug` (unique URL slug)
- `tagline`, `description`

**Classification:**
- `institution_type` — `university | college | training_center | corporate | nonprofit | other`
- `founded_year`

**Location:**
- `address`, `city`, `state`, `country`

**Contact:**
- `contact_email`, `contact_phone`, `website_url`, `linkedin_url`

**Status:**
- `is_verified` — admin-verified institution
- `is_active`

---

## Model: `Education`

Per-user education history rows. Allowed for `learner` and `instructor` users.

**Fields:**
- `user` (FK → `User`)
- `institution_name`, `degree`, `field_of_study`
- `start_date`, `end_date`
- `is_current` — if `True`, `end_date` must be null
- `description` (optional)

**Validation (`model.clean()`):**
- `end_date` must be ≥ `start_date`
- `end_date` must be null when `is_current=True`

---

## Model: `WorkExperience`

Per-user employment history rows. Allowed for `learner` and `instructor` users.

**Fields:**
- `user` (FK → `User`)
- `company`, `title`, `employment_type`
- `start_date`, `end_date`
- `is_current` — if `True`, `end_date` must be null
- `location`, `description` (optional)

**Validation (`model.clean()`):**
- Same date-range rules as `Education`

---

## API surface

### Private endpoints (owner only)

```
GET  /api/v1/auth/profile/me/                  → return own profile (type-specific)
PATCH /api/v1/auth/profile/me/                 → update own profile

GET  /api/v1/auth/profile/me/education/        → list own education rows
POST /api/v1/auth/profile/me/education/        → add education row
GET  /api/v1/auth/profile/me/education/{id}/   → get one
PATCH /api/v1/auth/profile/me/education/{id}/  → update
DELETE /api/v1/auth/profile/me/education/{id}/ → delete

GET  /api/v1/auth/profile/me/work-experience/        → list
POST /api/v1/auth/profile/me/work-experience/        → add
GET  /api/v1/auth/profile/me/work-experience/{id}/   → get one
PATCH /api/v1/auth/profile/me/work-experience/{id}/  → update
DELETE /api/v1/auth/profile/me/work-experience/{id}/ → delete
```

### Public browse endpoints

```
GET /api/v1/auth/profiles/learners/          → paginated public learner profiles
GET /api/v1/auth/profiles/instructors/       → paginated public instructor profiles
GET /api/v1/auth/profiles/institutions/      → paginated public institution profiles
GET /api/v1/auth/profiles/{slug}/            → single profile by name_slug
```

Public endpoints respect `is_profile_public` for learners and `is_verified`/`is_active`
for instructors and institutions.

---

## Profile update flow

```
PATCH /api/v1/auth/profile/me/
  Permission: IsAuthenticated + IsEmailVerified
         │
         ▼
View identifies caller's user_type
Picks correct serializer (Learner/Instructor/Institution)
         │
         ▼
Serializer validates partial update (only changed fields required)
         │
         ▼
Profile saved — only declared fields are overwritten
         │
         ▼
200 OK — { success: true, data: updated_profile }
```

---

## Why this design

- **One `User` + type-specific profile tables** avoids a single sparse mega-model with dozens of
  nullable columns. Each profile table contains only the fields relevant to that role.
- **Signal-based auto-creation** ensures the profile row always exists; views never need to handle
  missing profiles or create them lazily.
- **Separate `Education`/`WorkExperience` tables** support scalable timeline history — unlimited
  rows per user with clean date-range integrity checks.
- **Public/private endpoint split** prevents accidental overexposure of private data — public
  browse endpoints never return email addresses, OTP fields, or account status flags.
- **`is_profile_public` flag** gives learners opt-in visibility control, so instructors can browse
  learner backgrounds only when the learner has consented.

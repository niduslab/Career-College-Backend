# 03) Profiles

Profiles are separated by user type but share one `User`.

## Key files

- `auth/models.py`: `LearnerProfile`, `InstructorProfile`, `PartnerInstitutionProfile`, `Education`, `WorkExperience`
- `auth/all_views/profile_views.py`: private profile management + public profile listing/detail
- `auth/serializers.py`: profile serializers
- `auth/urls.py`: profile endpoint routing

## Models and fields

## `LearnerProfile`

- Link: `user` (OneToOne -> `User`)
- Personal: `profile_photo`, `headline`, `bio`, `date_of_birth`
- Location: `city`, `state`, `country`
- Learning context: `experience_level`, `learning_goal`, `interests`, `preferred_language`
- Links: `linkedin_url`, `github_url`, `website_url`
- Visibility: `is_profile_public`

## `InstructorProfile`

- Link: `user` (OneToOne)
- Personal: `profile_photo`, `headline`, `bio`
- Location: `city`, `state`, `country`
- Professional: `specialization`, `years_of_experience`, `current_title`, `current_organization`
- Links: `linkedin_url`, `github_url`, `website_url`
- Status: `is_verified`, `is_accepting_students`
- Institution relation:
  - `affiliated_institution` (FK -> `PartnerInstitutionProfile`)
  - `onboarding_source`
  - `affiliation_status`
  - `affiliated_at`

## `PartnerInstitutionProfile`

- Link: `user` (OneToOne)
- Branding: `logo`, `cover_image`, `institution_name`, `slug`, `tagline`, `description`
- Type/details: `institution_type`, `founded_year`
- Location: `address`, `city`, `state`, `country`
- Contact: `contact_email`, `contact_phone`, `website_url`, `linkedin_url`
- Status: `is_verified`, `is_active`

## `Education` and `WorkExperience`

- Both are per-user history tables (FK -> `User`)
- Allowed for user types: learner and instructor
- Include date-range integrity checks in model `clean()`

## API process

- Private profile:
  - `/auth/profile/me/`
  - `/auth/profile/me/education/`
  - `/auth/profile/me/work-experience/`
- Public browse:
  - `/auth/profiles/learners/`
  - `/auth/profiles/instructors/`
  - `/auth/profiles/institutions/`
  - `/auth/profiles/{slug}/`

Typical flow:

1. User registers.
2. Profile row for that user type is managed through profile APIs.
3. Education/work rows are attached over time.
4. Public visibility is controlled per profile type/flags.

## Workflow

1. `User.user_type` determines profile path (learner/instructor/institution).
2. Private endpoints update owner profile and related history rows.
3. Public endpoints expose curated profile data by visibility/status.
4. Slugs and indexes support discoverability and listing performance.

## System Explanation (Why This Design)

- One base `User` plus type-specific profile tables avoids sparse mega-models.
- Separate `Education`/`WorkExperience` tables support scalable timeline history.
- Public/private endpoint split prevents accidental overexposure of user data.

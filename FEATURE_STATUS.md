# Career College — Feature Status

Built vs. not-built against the SRS (`cc_docs/CC_SRS.md`, `cc_docs/CC_SRS_Partner.md`).
**AI-powered features are excluded from this document** (SRS §7 AI section and any
AI-flagged sub-features). Everything below is the non-AI functional surface.

Legend: a feature marked **partial** in prose is listed under "Not Built" with the
gap noted, since the SRS requirement is not fully met.

---

## Learner

### Features Built
- Email registration with OTP verification, JWT auth (access + refresh, rotation + blacklist)
- Google & LinkedIn OAuth login
- Learner profile: profile picture, personal info, short bio, skills
- Notification preferences (email, per-event-type)
- Course catalog with multi-criteria filtering (skill level, duration, price range, category/subcategory, rating, reviews) and sorting (relevance, rating, newest, popularity, price)
- Course preview before enrollment (preview lectures on catalog detail page)
- One-click enrollment for free courses
- Progress tracking: percentage completion, completed vs. pending items, resume position
- Cross-device progress continuity (server-side `WatchProgress`)
- Automatic progress saving (idempotent upsert)
- Time-spent tracking per lecture (`watched_seconds`)
- Quiz attempts with scoring and per-question verdicts
- Coding exercises with Docker-backed execution, multiple languages, visible/hidden test cases
- Assignment submission with rubric-based auto-grading (deterministic)
- HLS adaptive-bitrate video streaming (360p / 480p / 720p / 1080p, capped at the source height)
- Learner dashboard ("My Courses"): enrolled courses, progress, completion status, earned certificates
- Completion certificates: auto-issued at 100%, unique UUID, PDF download, public verification URL
- Course reviews & ratings with helpful up/down votes
- Direct messaging with instructors
- In-app notification feed + WebSocket real-time delivery
- Paid course **and** paid webinar purchase via SSLCommerz hosted checkout (sandbox; BDT; cards/bKash/Nagad/mobile banking through the gateway), server-side validation, PAID enrollment / webinar registration on success, order history endpoints
- Learner dashboard aggregates: KPI summary, recent-activity feed (six sources), upcoming cohort/drip/webinar dates, resume-target ("continue learning")
- Day streak backed by an append-only activity-day record (`LearnerActivityDay`) — counts lecture/quiz/assignment/coding engagement, excludes mere browsing and instructor preview
- Certificates list endpoint (alongside the existing per-course fetch, PDF download and public verify)
- Course wishlist: save/unsave, list, and an `is_wishlisted` flag on catalog cards
- Private learner notes with tags, colours, pinning, and optional course/lecture/timestamp anchors

### Features Not Built
- Learning preferences (topics/difficulty/learning style) and privacy controls
- Voice search
- Similar-course recommendations ("courses like this")
- Personalized recommendations
- Dynamic learning-path recommendations
- Stripe payment option (SSLCommerz only today; SRS also lists Stripe)
- Price-drop alerts on wishlisted courses (the wishlist itself is built; alerting is not)
- Predicted completion timeline
- Skill assessments (pre-course / post-course)
- Dashboard XP / gamification stats — `total_xp` is deliberately absent from the summary endpoint until a `LearnerXpEvent` ledger exists (see `docs/architecture/27-learner-dashboard.md`)
- Bookmarks (distinct from notes and wishlist)
- Adaptive content delivery (reorder by learning style/performance)
- Playback-speed control (backend not required; no server support)
- Closed captions, subtitles, video transcripts, interactive transcripts
- Additional question types: true/false, fill-in-the-blank, matching, sequence, drag-and-drop
- Offline content download and sync
- Live learning sessions (HD conferencing, screen share, whiteboard, live Q&A/polls)
- Community: discussion boards, threaded Q&A, peer review, study groups
- Gamification: XP, badges, streaks, leaderboards, progress celebrations
- Custom / branded certificate templates, blockchain certificates
- Direct certificate social-share integrations (LinkedIn/Twitter) — only public verify URL exists
- Multi-dimensional ratings (quality/instructor/value/difficulty separately) — single rating only
- Review filter by recency/helpfulness (only basic listing)
- Lifetime-access / re-enrollment policy controls

---

## Instructor

### Features Built
- Course authoring: metadata, sections, lectures, learning objectives, prerequisites, audiences
- Curriculum builder with drag-and-drop reordering (`SectionContent` single source of ordering)
- Video upload with automatic transcode to multiple HLS renditions
- Article/text lectures
- Image upload (thumbnails, profile)
- Quiz authoring (MCQ with correct answers)
- Coding-exercise authoring: starter/solution code per language, test cases (visible + hidden)
- Assignment authoring with rubric definitions and auto-grading criteria
- Multi-instructor collaboration (co-instructor invite / accept flow)
- Course submission → manual admin review → publish/reject state machine
- Course lifecycle: draft, under_review, published, rejected, archived, rework
- Identity verification (required before publishing)
- Preview of own course as an instructor
- Direct messaging with enrolled learners
- Notifications for course/enrollment/review events

### Features Not Built
- Pricing promotions (coupons, promo codes, time-limited discounts) — base free/paid pricing + SSLCommerz checkout now exist; promo tooling does not
- Multimedia upload beyond video/article/image: audio, PDF/DOCX/PPTX documents, SCORM/xAPI, code repos
- Automatic video thumbnails / preview clips
- Screen recording tool
- Assessment types beyond MCQ + coding + rubric assignment (true/false, fill-blank, matching, drag-drop)
- Partial-credit MCQ scoring
- Peer-review assessment workflow
- Question bank / difficulty tagging / adaptive assessment
- AI-free automated course quality scoring (SRS ties this to AI; no manual scoring either)
- Course preview-before-publish beyond current instructor view (availability/enrollment date windows)
- Live session management (schedule/host webinars with conferencing, polls, breakout rooms, attendance)
- Instructor analytics dashboard (enrollment/engagement/content-performance metrics) — analytics is partner-only
- Revenue / earnings dashboard, payouts, commission, tax reporting
- Promotional tools (discount codes, bundles, referral, affiliate)
- Custom certificate designer, conditional certificate criteria, bulk issue/revoke
- Content versioning / change tracking / archive
- Broadcast announcements to all course participants
- Discussion-thread creation / office hours
- Consultancy session booking (calendar, scheduling, session recording, payment)
- Blog creation tools (rich editor, SEO tools, drafts/scheduling, categories/tags)

---

## Platform Administrator

### Features Built
- Instructor identity verification review workflow (approve / reject / action_required / expire)
- Partner-institution verification review workflow (approve / reject / action_required)
- Manual course review: approve → publish, or reject with reason
- Course category taxonomy
- Django admin for direct data management
- Notifications to admins on verification submissions

### Features Not Built
- Dedicated user-management console: search/filter accounts, role editing, suspend/deactivate, dispute/ticket handling, activity/audit logs
- Course management: feature/promote courses, take down inappropriate content, platform-wide pricing/refund policies
- System-wide analytics (total users/enrollments/revenue/active courses, funnels, financial reporting)
- Automated approval workflows (beyond the verification state machines)
- Platform configuration (settings/policies, email templates, branding, commission rates, content guidelines)
- Content moderation queue and manual moderation workflow (non-AI portion)
- Instructor onboarding automation, performance management, instructor-segment communication
- Financial & revenue management, payout administration, payment-gateway config
- Platform health/performance monitoring (uptime, load times, API/DB metrics, error tracking, usage analytics)

---

## Partner Institution

### Features Built
- Institution registration + application with document upload
- Verification & approval workflow (pending → under_review → approved / rejected / action_required)
- Auto-set verified/active profile on approval
- Expert (instructor) onboarding: auto-provision account, preset password emailed, immediate login
- Expert management: list, edit, activate/deactivate, scope to own institution
- Departments: CRUD, soft-delete, expert assignment
- Per-expert performance analytics (courses credited, content authored, ratings, enrollments, completion, certificates, webinars, last-active)
- Institution-owned course creation (institution as `created_by`)
- Course roster management: add/remove affiliated experts directly (no invite/accept)
- Two-stage submission for institution courses: expert `/finish/` → `institution_review` → institution forwards to admin or sends back
- Asynchronous content types: video, article, quiz, coding exercise, assignment
- Open enrollment for institution courses
- Per-learner progress tracking and per-course/cohort aggregate analytics dashboard (KPI summary, enrollment/webinar/certificate trends, top courses)
- Certificate auto-issuance on completion (UUID + PDF + verify URL)
- Institution-owned webinars: metadata + external meeting link, publish state machine, catalog listing, learner registration with capacity, host/speaker roles
- Institution ↔ expert direct messaging

### Features Not Built
- Dedicated partner portal / dashboard module provisioning on approval (assigned account/success manager)
- Institution profile branding customization module
- Course scheduling: start date, end date, enrollment deadline enforcement, staggered/timed content release
- Access control by schedule (pre-start, active, post-end lockout/read-only, per-learner extensions)
- Restricted enrollment (email domain / named list / group mapping)
- Institution revenue capture from paid enrollments (learner-side SSLCommerz checkout exists; wallet/ledger attribution to the institution is Phase 2)
- Learner status relative to schedule (on track / behind / overdue)
- Cohort filtering, enrollment-date-range or department filtering of metrics
- Downloadable / exportable reports (enrollment, progress, at-risk learners, assessment stats, compliance/audit logs)
- Assessment plan configuration: component weighting, due dates, retake/attempt policies, penalty rules
- Configurable completion/passing rules (minimum score, mandatory-module gating, pass/fail states)
- Certificate hold-for-review, revoke, reissue; institution branding on certificate
- Revenue model: institutional wallet, transaction ledger, payout requests/processing, balance tracking
- Course content versioning; tracking which cohort saw which version
- Institutional helpdesk / ticketing integration
- External integrations: HR-system auto-enrollment, SSO/identity provider, completion-data sync back to systems of record
- Discussion forums / Q&A threads / announcements within institution courses

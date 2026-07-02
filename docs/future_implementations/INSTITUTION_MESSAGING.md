# Generalized Messaging — Institution↔Expert & Co-Instructor DMs (+ Announcements)

**Status:** DM generalization (§1–7) ✅ **implemented**. Announcements (§8) still planned.
**Shipped:** `messaging.Conversation` generalized to `conversation_type` (`learner_instructor` | `co_instructor` | `institution_expert`) + `ConversationParticipant` through-table (per-user `last_read_at`), nullable `course`, `participant_key` pair-uniqueness. Gate dispatch + `start_conversation()` in `messaging/services/messaging_service.py`; conversation-type-aware create in `conversation_views.py`; `IsPartnerInstitutionUser` added to `core/permissions.py`; migration `messaging/0002_generalize_conversation` (expand + backfill + cutover — old triad columns dropped after backfill). Tests: `messaging/tests/` (existing suite migrated, all green) + `test_conversation_types.py`. **Announcements (§8) remain unbuilt** — separate track (own model + the four notification edits).
**Depends on:** the `messaging` app, `notifications` + `dispatch()`, `realtime` consumer, the expert roster + course roster (all shipped).
**SRS:** §7.x institution & staff communications.

---

## 1. Decision

**Generalize `messaging.Conversation` into a role-neutral 2-party thread** and reuse it for all direct-message conversation types:

| conversation_type | parties | course-scoped? |
|---|---|---|
| `learner_instructor` | learner ↔ instructor | yes (existing behavior, unchanged) |
| `co_instructor` | instructor ↔ instructor (same course roster) | yes |
| `institution_expert` | partner-institution user ↔ affiliated expert | optional |

Rationale: **every party is a `User`.** A partner institution has `PartnerInstitutionProfile.user` (a `user_type='partner_institution'` User); experts, instructors, co-instructors, learners are all Users. The current `Conversation` *is* a 2-party User↔User thread with the roles hardcoded to `learner`/`instructor`. The threading, soft-delete, unread cursors, and WebSocket delivery are all party-agnostic already — only the field **names**, one **unique constraint**, the **required course**, and the **send-gate** are learner/instructor-specific. Generalizing removes triple-implementation of unread/WS/serializer/consumer code.

**Announcements stay separate** (§7): institution → all enrolled learners is genuinely one-to-many with no reply — that's notification fan-out, not a thread. Do not model it as a Conversation.

---

## 2. What actually has to change in `Conversation`

Current triad-specific bits (from `messaging/models.py` + `messaging/services/messaging_service.py`):
- Role FKs `learner` / `instructor`; role cursors `learner_last_read_at` / `instructor_last_read_at`.
- `UniqueConstraint(learner, instructor, course)`; `course` is **required** (FK, not null).
- Send-gate hardwired in `_assert_send_permission` (learner enrollment / instructor on course) and `get_or_create_conversation`.
- Unread (`get_unread_conversation_count`, `get_unread_counts`, `mark_read`) branch on `learner_id` vs `instructor_id`.

None is intrinsic. All become `conversation_type`-parameterized.

---

## 3. Generalized data model

Two viable shapes — **recommend the through-table** (cleaner unread, room to grow), with the 2-column rename as the lighter alternative.

### Recommended: participant through-table

`Conversation`
| Field | Type | Notes |
|---|---|---|
| `conversation_type` | CharField choices | `learner_instructor` \| `co_instructor` \| `institution_expert`. `db_index`. |
| `course` | FK `NidusCourse` (`CASCADE`, **null=True**) | Required for course-scoped conversation types; null allowed for `institution_expert`. |
| `updated_at` / `created_at` | (unchanged) | |

`ConversationParticipant`
| Field | Type | Notes |
|---|---|---|
| `conversation` | FK (`CASCADE`, `related_name='participants'`) | |
| `user` | FK User (`CASCADE`) | |
| `last_read_at` | DateTime (null) | Per-user unread cursor (replaces the two role cursors). |
| Meta | | `UniqueConstraint(conversation, user)`; index `(user, conversation)`. |

- Exactly **2** participant rows per conversation today (enforced in the service), but the shape supports N-party later (group threads) for free.
- **Uniqueness of a pair**: enforce in the service (`get_or_create` by conversation type + sorted participant pair + course); a raw DB unique on an unordered M2M pair is awkward, so use a deterministic `participant_key` (e.g. `min(id)-max(id)`) column with `UniqueConstraint(conversation_type, course, participant_key)` if a DB guarantee is wanted.

Unread simplifies to one query: conversations where the caller is a participant AND a `Message.created_at > that participant's last_read_at` exists (`Exists` subquery joined on the participant row) — replaces the two role-branched queries.

### Alternative: keep 2 columns, rename role-neutral

Rename `learner`→`participant_a`, `instructor`→`participant_b` (+ cursors) with `db_column` preserved so **no physical column rename** happens; add `conversation_type`; make `course` nullable. Smaller migration, but stays hard-2-party and the unread code keeps its two-branch shape. Pick this if the through-table migration risk isn't worth it.

---

## 4. Send-gate — dispatch by conversation type

Keep the single-entry-point discipline: **one** `send_message` / `get_or_create_conversation`, gate chosen by `conversation.conversation_type`. Roles inside a conversation are derived from `user.user_type` (no role column needed):

```python
_GATES = {
    'learner_instructor': _gate_learner_instructor,   # existing: learner active enrollment + instructor on course
    'co_instructor':      _gate_co_instructor,         # both users in course.instructors (symmetric)
    'institution_expert': _gate_institution_expert,    # expert is active affiliate of the institution
}
```
- `co_instructor`: `NidusCourse.objects.filter(pk=course_id, instructors=user).exists()` for **each** party at send time (a removed co-instructor loses send rights, keeps read history — mirrors the existing instructor rule).
- `institution_expert`: the `user_type='instructor'` party must be an **active affiliate** (`InstructorProfile.affiliation_status='active'`, `affiliated_institution=` the institution party's profile) at send time — the analogue of the enrollment gate. The institution party always may send.
- Never duplicate a gate in a view or the consumer — same rule as today (the send-gate lives only in the service and is shared by REST + WS).

Initiation rules (in `get_or_create_conversation`, per conversation type):
- `learner_instructor`: learner-initiated only (unchanged).
- `co_instructor`: either instructor may initiate.
- `institution_expert`: either party may initiate (institution or expert).

---

## 5. Migration strategy (expand → contract)

Working, tested subsystem — migrate without downtime or data loss:

1. **Expand.** Add `conversation_type` (default `learner_instructor`), make `course` nullable, add `ConversationParticipant`. Data migration: for every existing `Conversation`, create two participant rows carrying the old `learner_last_read_at` / `instructor_last_read_at` into `last_read_at`; stamp `conversation_type='learner_instructor'`.
2. **Cut over.** Update the service + serializers + consumer to read participants (and `conversation_type`) instead of the role fields. Re-run `messaging/tests/` — they must stay green (behavior for the existing conversation type is unchanged).
3. **Contract (a release later).** Drop `learner`, `instructor`, `learner_last_read_at`, `instructor_last_read_at` once nothing reads them.

Keeping the two phases apart means a rollback never loses cursor data.

---

## 6. Endpoints

Reuse the existing conversation surface; add conversation-type-aware creation. All under `/api/v1/messaging/`.

- `POST conversations/create/` — extend to accept `conversation_type` + the appropriate party ids. Keep backward compatibility: a body with `{course_id, instructor_id, body}` and no `conversation_type` defaults to `learner_instructor` (existing learner client keeps working). New: `{conversation_type: 'co_instructor', course_id, peer_instructor_id, body}`, `{conversation_type: 'institution_expert', expert_user_id, body, course_id?}`.
- `GET conversations/` — already lists by participant; with the through-table it lists across all conversation types the caller is in. Add `?conversation_type=` filter.
- `GET conversations/<id>/`, `POST <id>/read/`, `GET conversations/unread-count/` — unchanged surface; internally participant/conversation-type-aware. **There is no REST `POST <id>/messages/`** — follow-up messages are sent over the WebSocket `messaging` stream only (create persists the opener).

Permissions: today the messaging endpoints require `IsEmailVerified + (IsLearnerUser OR IsInstructorUser)`. Broaden to also allow `partner_institution` users (for `institution_expert`). The **send-gate** (service) remains the real authorization; the view permission is just the coarse gate. Numeric IDs → 404 on no-access (unchanged).

---

## 7. WebSocket & notifications — mostly free

- **WS unchanged.** Delivery already targets `messaging_user_{recipient_id}` per-user groups (`_push_ws_and_notify`) — party-agnostic. The `messaging` stream handler and `MESSAGE_RECEIVED` dispatch work as-is; just compute `recipient_id` as "the other participant" instead of "the non-sender role".
- **`MESSAGE_RECEIVED` reused** for all conversation types. Add `conversation_type` (and make `course_*` optional) to the notification context so the builder can word institution/co-instructor messages appropriately; `course_slug`/`course_title` become optional in the payload for course-less `institution_expert` threads. Confirm the builder + email template tolerate a missing course.

---

## 8. Announcements (separate feature — one-to-many, no reply)

Institution → its experts, or → learners enrolled in its courses. **Not** a Conversation. New `InstitutionAnnouncement(institution, created_by, audience, course?, title, body)`; recipients resolved at post time (active experts / distinct active enrollees), deduped; delivered through `dispatch()` (feed + email + WS free), Celery fan-out for large learner audiences. Needs a new `NotificationEventType.ANNOUNCEMENT_POSTED` with the **four edits** (event enum + migration, `builders.py`, `EVENT_TO_CATEGORY` in `preference_service.py`, `_EVENT_TEMPLATE_MAP` + `emails/announcement_posted.html`). Endpoints `POST/GET /partner/announcements/`. **Cross-institution scope is the #1 risk** — `learners` audience must resolve only to *this* institution's enrollees; test explicitly.

---

## 9. Files to change

**Generalized DMs (§3–7):**
1. `messaging/models.py` — add `conversation_type`, nullable `course`, `ConversationParticipant`; new constraints. + migrations (expand, then contract).
2. `messaging/services/messaging_service.py` — participant/conversation-type-aware `get_or_create_conversation`, `send_message`, `list_conversations`, `get_conversation_for_participant`, `mark_read`, `get_unread_*`; `_GATES` dispatch (`_gate_co_instructor`, `_gate_institution_expert`, keep `_gate_learner_instructor`). Single send-gate entry point preserved.
3. `messaging/serializers.py` — conversation-type-aware create serializer; expose `conversation_type` + participants; make course optional.
4. `messaging/all_views/conversation_views.py` — accept `conversation_type`; broaden permission to include `partner_institution`.
5. `realtime/` consumer/stream — recipient = "other participant" (no schema change; verify `send_message` WS path).
6. `notifications/services/builders.py` — `MESSAGE_RECEIVED` builder tolerates optional course + `conversation_type`.
7. `messaging/tests/` — existing suite stays green; add `co_instructor` + `institution_expert` gate/unread/WS tests.

**Announcements (§8):** new model + service + views + the four notification edits + template + tests (as above).

**Docs:** `CLAUDE.md` (messaging section), new `docs/architecture/21-institution-messaging.md`, `docs/api-testing/postman-institution-messaging.md`.

---

## 10. Edge cases & rules

- **Existing behavior frozen.** `learner_instructor` threads must behave identically post-refactor — the current tests are the contract. Cut over only when they pass.
- **Role derivation.** Roles within a conversation come from `user.user_type` + `conversation_type`, not a stored role — so a gate never mis-identifies which party is the learner/expert/institution.
- **Removed party keeps read access, loses send.** Co-instructor removed from the roster, or expert deactivated: can still read history, cannot send (gate re-checked at send time) — consistent with the existing instructor rule.
- **Course-less institution↔expert threads:** `course` null → notification/serializer must not assume a course. Test the null-course path.
- **Pair uniqueness across conversation types:** the same two instructors could have both a `co_instructor` thread (course X) and be learner/instructor elsewhere — uniqueness is per `(conversation_type, course, pair)`, not per pair.
- **403 vs 404:** conversation ids are numeric → non-participant access → 404 (unchanged).
- **No N-party yet:** the through-table allows it, but the service enforces exactly 2 participants until group chat is a real requirement (don't build speculative group logic).

---

## 11. Build order

1. Expand migration (`conversation_type`, nullable course, `ConversationParticipant` + backfill). Service cut over to participants for the **existing** conversation type only. Green `messaging/tests/`.
2. Add `co_instructor` (smallest new gate — both on roster) end-to-end + tests.
3. Add `institution_expert` (+ course-less path, broaden view permission) + tests.
4. Contract migration (drop old role columns) once stable.
5. Announcements (separate track — can proceed in parallel; only shares the notification app).

## 12. Future extensions

- Group threads (N participants) — the through-table already supports it; lift the 2-participant service cap.
- Institution ↔ learner support threads (another `conversation_type`, gate = learner enrolled in an institution course).
- Read receipts / typing indicators over the existing WS stream.
- Announcement read receipts + scheduled/targeted announcements.

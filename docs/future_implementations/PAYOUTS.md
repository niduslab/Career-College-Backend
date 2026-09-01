# Payouts (Instructor / Partner Institution Revenue Disbursement)

**Status:** 📋 Designed, not built. This document is the pre-build design plan.
**Depends on:** existing `payments.Order` (revenue source of truth), `analytics/services/instructor_revenue_service.py` + `institution_revenue_service.py` (existing gross-revenue attribution logic), `admin_console.PlatformSettings` (platform-wide config singleton), `admin_console.AdminActionLog` (audit trail). New app: `payouts/` (or a module inside `payments/` — decide at implementation time based on how large the model set grows).
**Related:** `docs/architecture/21-payments.md` §"Phase 2 (not built)" and `analytics/services/instructor_revenue_service.py`'s docstring both flag "Institution wallet/payout ... Phase 2" — this document supersedes those notes once implemented.

---

## 1. Problem

Instructors and partner institutions earn revenue through course sales (`payments.Order`, `status='paid'`), and the existing analytics surfaces (`analytics/instructor/revenue/summary/`, `analytics/institution/...`) already show them their gross earnings. But nothing in the system tracks:

- What portion of that gross revenue is actually **owed** to the instructor/institution after the platform's commission.
- Whether that owed amount has been **paid out** yet.
- **How** to pay it out — no bank/mobile-banking details are captured anywhere for instructors or institutions.
- Any admin workflow to review, approve, and record a payout.

This document defines a **Phase 1, admin-driven manual payout workflow** — the platform does not (and, per the payment gateway in use, currently cannot) send money out programmatically.

## 2. Scope decisions (locked)

These were decided up front and constrain everything below:

| Question | Decision |
|---|---|
| Automatic disbursement via a payment gateway? | **No.** SSLCommerz (the only gateway integrated, `payments/services/sslcommerz_service.py`) has no disbursement/payout API called anywhere in this codebase — only `initiate_session`, `validate_transaction`, `query_transaction`. Phase 1 payouts are recorded as **manually completed** by an admin after transferring money outside the system (bank transfer / mobile banking), the same pattern already used for the existing "duplicate payment, needs manual refund" case (`Order.gateway_payload.requires_refund`, off-system). |
| Commission rate: per-account negotiated, or platform-wide flat rate? | **Platform-wide flat rate only for v1.** No per-account override. Configured on the existing `admin_console.PlatformSettings` singleton (`default_commission_pct`), snapshotted onto each `Payout` row at generation time so a later rate change never rewrites an already-generated payout. |
| Payout period: fixed monthly cycle, or admin-picked range? | **Admin picks an arbitrary date range per generation.** No fixed monthly cycle, no scheduled Celery task in v1 — payout generation is an explicit admin action, not automatic. |
| Does an unverified payout account block anything? | **Yes — blocks payout generation entirely.** An instructor/institution's `PayoutAccount` must be admin-verified before any `Payout` row can even be generated for them. This avoids an admin reviewing/approving a payout that can never actually be completed because the bank details were never confirmed. |
| Multi-currency? | No — BDT only, matching `Order.currency`'s existing default. |
| Partial payouts / installments? | No — one `Payout` row is paid in full or not at all. |

## 3. Current-state audit (why these models, not a bolt-on)

Confirmed by reading the current codebase before designing:

- `payments.Order` (`payments/all_models/order_models.py`) has `amount` (gross price the learner paid, snapshotted at checkout), `status` (`initiated→processing→paid|failed|cancelled`), `currency` (default `'BDT'`). **No commission/platform-fee field exists anywhere** — not on `Order`, not in settings, not as a hardcoded constant. Confirmed by repo-wide search.
- `analytics/services/instructor_revenue_service.py` and `institution_revenue_service.py` already compute **gross-only** revenue per instructor/institution via `Order.objects.filter(Q(course__instructors=X) | Q(course__created_by=X), status='paid')` (instructor) and the equivalent `partner_institution` filter (institution) — both docstrings explicitly say "no payout, balance, commission split ... Phase 2." This attribution logic is the correct source of truth for "how much revenue does this recipient's courses represent" and must be reused, not re-derived, by the payout generation step.
- `authentication.InstructorProfile` and `authentication.PartnerInstitutionProfile` (`authentication/models.py`) have **no bank or mobile-banking fields at all** — confirmed by reading every field on both models. A payout feature must add new fields/model for payout-destination details; nothing to extend.
- `NidusCourse.created_by` / `.instructors` (M2M) / `.partner_institution` are the existing ownership fields the revenue-attribution services already key off — reused unchanged.
- `admin_console.AdminActionLog` is an append-only audit model (`actor`, `target_user` — nullable, `action`, `reason`, `metadata` JSON) written in the same transaction as the mutating action, already used for suspend/reactivate/role-change. New `Action` choices should be added here rather than building a parallel payout-specific audit log.
- `admin_console.PlatformSettings` is a `pk=1` singleton (`PlatformSettings.load()` is the only read path) already holding platform-wide config (`organization_name`, default certificate signatory) — the natural home for `default_commission_pct`.
- The closest existing state-machine pattern is **`NidusCourse.transition_to()`** (course review approve/reject): a single entry point, `TextChoices` states, `ValidationError` → 400 for field-level errors vs plain-string → 422 for business-rule violations. This fits a payout's multi-state lifecycle (`pending → approved → paid | rejected`) better than the admin_console suspend/reactivate boolean-toggle pattern.
- No `future_implementations` stub or prior design existed for this feature before this document — confirmed by directory listing.

## 4. New models

New app `payouts/` (or a `payments` submodule — decide based on final model count).

### 4.1 `PayoutAccount`

One row per instructor or institution. Polymorphic via nullable FKs with an exactly-one DB check constraint, mirroring `Order.course`/`Order.webinar`'s existing pattern.

```python
class PayoutAccount(TimestampedModel):
    class Method(models.TextChoices):
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
        MOBILE_BANKING = 'mobile_banking', 'Mobile Banking'

    class MobileBankingProvider(models.TextChoices):
        BKASH = 'bkash', 'bKash'
        NAGAD = 'nagad', 'Nagad'
        ROCKET = 'rocket', 'Rocket'

    instructor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    institution = models.ForeignKey(PartnerInstitutionProfile, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    payout_method = models.CharField(max_length=20, choices=Method.choices)

    # Bank transfer fields (blank unless payout_method == BANK_TRANSFER)
    bank_name = models.CharField(max_length=200, blank=True, default='')
    bank_account_number = models.CharField(max_length=50, blank=True, default='')
    bank_account_name = models.CharField(max_length=200, blank=True, default='')
    bank_routing_number = models.CharField(max_length=50, blank=True, default='')

    # Mobile banking fields (blank unless payout_method == MOBILE_BANKING)
    mobile_banking_provider = models.CharField(max_length=20, choices=MobileBankingProvider.choices, blank=True, default='')
    mobile_banking_number = models.CharField(max_length=20, blank=True, default='')

    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(Q(instructor__isnull=False, institution__isnull=True) |
                       Q(instructor__isnull=True, institution__isnull=False)),
                name='chk_payoutaccount_exactly_one_owner',
            ),
        ]
```

### 4.2 `Payout`

One row per generated payout for one recipient over one admin-picked date range.

```python
class Payout(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        PAID = 'paid', 'Paid'
        REJECTED = 'rejected', 'Rejected'

    payout_account = models.ForeignKey(PayoutAccount, on_delete=models.PROTECT, related_name='payouts')

    period_start = models.DateField()
    period_end = models.DateField()

    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    platform_fee_pct = models.DecimalField(max_digits=5, decimal_places=2)  # snapshotted from PlatformSettings at generation
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)       # gross_amount * (1 - platform_fee_pct/100)
    currency = models.CharField(max_length=3, default='BDT')

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    included_order_ids = models.JSONField(default=list)  # snapshot of Order PKs this payout covers — frozen, auditable

    admin_notes = models.TextField(blank=True, default='')
    rejection_reason = models.TextField(blank=True, default='')
    payment_reference = models.CharField(max_length=200, blank=True, default='')  # bank/mobile-banking txn ref, filled on mark-paid

    requested_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
```

State machine: `pending → approved → paid`, or `pending → rejected`. Single `transition_to(new_status, actor=None, reason='')` entry point on the model, same convention as `NidusCourse`/`Webinar`/`CourseSchedule` — never set `status` directly.

`included_order_ids` is the snapshot-everything-at-generation-time pattern already established by the certificate system (`Certificate` freezes learner name/course title/signatures at issuance) — a payout's numbers must never silently drift if new orders land in the same period after generation.

### 4.3 `PlatformSettings.default_commission_pct` (new field on existing singleton)

```python
default_commission_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('20.00'))
```

Added to `admin_console/all_models/platform_settings_models.py`. Exposed on the existing `GET/PATCH /api/v1/admin-console/platform-settings/` endpoint — no new endpoint needed for this piece.

## 5. Workflow

1. **Instructor/institution** adds/edits their own `PayoutAccount` via a self-service endpoint (mirrors the existing `PATCH /auth/profile/me/` pattern — reuse `IsInstructorUser`/`IsPartnerInstitutionUser`, no new permission class).
2. **Admin** reviews and verifies a `PayoutAccount` (`POST .../verify/`). Until verified, that account is invisible to payout generation.
3. **Admin** triggers `POST admin/payouts/generate/` with `{period_start, period_end}`. For every **verified** `PayoutAccount` with `gross_amount > 0` in that range (computed via the existing `_paid_orders()` filters), create a `Payout` row in `pending` status, snapshotting `gross_amount`, `platform_fee_pct` (from `PlatformSettings.load()`), `net_amount`, and `included_order_ids`.
4. **Admin** reviews pending payouts (list + detail — detail includes the `included_order_ids` breakdown for auditability) and approves or rejects: `POST admin/payouts/<pk>/review/` `{action: 'approve'|'reject', rejection_reason?}` — mirrors `CourseAdminReviewView`'s exact shape.
5. **Admin** transfers the money manually (bank transfer / mobile banking, outside this system), then calls `POST admin/payouts/<pk>/mark-paid/` `{payment_reference}` — the terminal step. `payment_reference` is stored for reconciliation but never validated against anything (there is nothing to validate it against — it's an off-system transfer).
6. **Instructor/institution** can view their own payout history via `GET my-payouts/`.

Every mutating admin action (`verify`, `generate`, `review`, `mark-paid`) writes an `AdminActionLog` row in the same transaction, with new `Action` choices (`PAYOUT_ACCOUNT_VERIFY`, `PAYOUT_APPROVE`, `PAYOUT_REJECT`, `PAYOUT_MARK_PAID`).

## 6. Endpoints (new `payouts` app, `/api/v1/payouts/`)

| Method + path | Permission | Purpose |
|---|---|---|
| `GET/PATCH payout-account/me/` | `IsInstructorUser` or `IsPartnerInstitutionUser` | Manage own payout account |
| `GET admin/payout-accounts/` | `IsPlatformAdmin` | Browse all accounts, `?is_verified=` filter |
| `POST admin/payout-accounts/<pk>/verify/` | `IsPlatformAdmin` | Verify an account — required before it can be targeted by generation |
| `POST admin/payouts/generate/` | `IsPlatformAdmin` | `{period_start, period_end}` — generate pending payouts for verified accounts with revenue in range |
| `GET admin/payouts/` | `IsPlatformAdmin` | List all payouts, `?status=`, `?search=` |
| `GET admin/payouts/<pk>/` | `IsPlatformAdmin` | Detail, including `included_order_ids` breakdown |
| `POST admin/payouts/<pk>/review/` | `IsPlatformAdmin` | `{action: 'approve'\|'reject', rejection_reason?}` |
| `POST admin/payouts/<pk>/mark-paid/` | `IsPlatformAdmin` | `{payment_reference}` — terminal manual-completion step |
| `GET my-payouts/` | `IsInstructorUser` or `IsPartnerInstitutionUser` | Own payout history |

All responses use the standard `{success, message, data}` / `{success, message, errors}` envelope; lists use `StandardResultsSetPagination`.

## 7. Frontend (after backend ships)

Admin Payouts page rebuilt on real data, mirroring the Approvals-page pattern already established in the frontend: stats cards (Total Pending, Total Paid This Month, Payout Accounts Needing Verification), a filterable table (status, search by recipient name), a detail modal showing the order-level breakdown behind a payout (`included_order_ids`), and an action menu (Approve / Reject / Mark Paid) — reusing the existing `Pagination`, portal-based row-action-menu, and detail-modal conventions already used across Users/Approvals/Verification/Categories.

## 8. What's intentionally not built (Phase 1)

- **No automatic gateway disbursement.** SSLCommerz has no disbursement API integrated in this codebase; adding one is a separate, larger integration project.
- **No scheduled/automatic payout generation.** Admin-triggered only.
- **No per-account negotiated commission rate.** Platform-wide flat rate only; revisit if a real negotiated deal ever requires it.
- **No multi-currency support.** BDT only.
- **No partial payouts or installment plans.**
- **No refund netting against payouts** — refunds remain the existing manual, off-system process (`Order.gateway_payload.requires_refund`); a payout's `gross_amount` is computed purely from `status='paid'` orders in the period and does not account for later refund activity in Phase 1.

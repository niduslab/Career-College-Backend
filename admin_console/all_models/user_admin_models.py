from django.conf import settings
from django.db import models


class AdminActionLog(models.Model):
    """
    Append-only record of a sensitive admin action.

    Written inside the same transaction as the mutation (suspend / reactivate /
    role change / certificate revoke / settings change) so the audit trail can
    never drift from the action. Never updated or deleted through the API.

    target_user is null for actions that do not target an account — a platform
    settings change, or a certificate revocation (whose subject is recorded in
    `metadata` instead).
    """

    class Action(models.TextChoices):
        SUSPEND = 'suspend', 'Suspend'
        REACTIVATE = 'reactivate', 'Reactivate'
        ROLE_CHANGE = 'role_change', 'Role change'
        CERTIFICATE_REVOKE = 'certificate_revoke', 'Certificate revoked'
        CERTIFICATE_RESTORE = 'certificate_restore', 'Certificate restored'
        PLATFORM_SETTINGS_UPDATE = 'platform_settings_update', 'Platform settings updated'
        PAYOUT_ACCOUNT_VERIFY = 'payout_account_verify', 'Payout account verified'
        PAYOUT_APPROVE = 'payout_approve', 'Payout approved'
        PAYOUT_REJECT = 'payout_reject', 'Payout rejected'
        PAYOUT_MARK_PAID = 'payout_mark_paid', 'Payout marked paid'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+',
    )
    action = models.CharField(max_length=32, choices=Action.choices, db_index=True)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['target_user', '-created_at']),
        ]

    def __str__(self):
        return f'AdminActionLog({self.action}, target={self.target_user_id})'

from django.conf import settings
from django.db import models


class AdminActionLog(models.Model):
    """
    Append-only record of a sensitive admin action on a user account.

    Written inside the same transaction as the mutation (suspend / reactivate /
    role change) so the audit trail can never drift from the action. Never
    updated or deleted through the API.
    """

    class Action(models.TextChoices):
        SUSPEND = 'suspend', 'Suspend'
        REACTIVATE = 'reactivate', 'Reactivate'
        ROLE_CHANGE = 'role_change', 'Role change'

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

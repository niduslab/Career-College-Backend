"""
Admin user-management service.

All mutations are atomic and write an ``AdminActionLog`` row in the same
transaction, so the audit trail can never drift from the action. Business-rule
violations raise ``AdminUserActionError`` carrying an HTTP status.
"""
import logging

from django.db import transaction
from django.db.models import Q

from admin_console.all_models import AdminActionLog
from authentication.models import User
from authentication.services.profile_service import ensure_profile_for_type
from authentication.services.token_service import blacklist_all_refresh_tokens

logger = logging.getLogger(__name__)

_VALID_USER_TYPES = {choice[0] for choice in User.USER_TYPE_CHOICES}
_SORT_WHITELIST = {
    'registration_date': 'registration_date',
    '-registration_date': '-registration_date',
    'email': 'email',
    '-email': '-email',
    'full_name': 'full_name',
    '-full_name': '-full_name',
}
_BOOL_FILTERS = ('is_active', 'is_restricted_by_admin', 'is_verified', 'is_email_verified')


class AdminUserActionError(Exception):
    """Raised on admin user-management business-rule violations. Carries an HTTP status."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def _parse_bool(value):
    """Query-param truthiness; returns None when the value isn't a clear bool."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ('true', '1', 'yes'):
        return True
    if v in ('false', '0', 'no'):
        return False
    return None


def _coerce_bool_strict(value):
    """
    Parse a required boolean flag. Accepts real bools and the usual string
    spellings; raises 400 on anything ambiguous — never silently truthy
    (``bool("false")`` is True, which would be a privilege-escalation footgun).
    """
    parsed = _parse_bool(value)
    if parsed is None:
        raise AdminUserActionError('is_staff must be a boolean.', 400)
    return parsed


def _is_admin(user):
    return bool(user.is_staff or user.user_type == 'admin')


def log_admin_action(actor, action, target, reason='', metadata=None):
    """
    Insert an append-only audit row. Call inside the mutation's transaction.

    Snapshots the actor/target emails into ``metadata`` so attribution survives
    even if either account is later deleted (the FKs are ``SET_NULL``).
    """
    data = dict(metadata or {})
    data.setdefault('actor_email', getattr(actor, 'email', None))
    data.setdefault('target_email', getattr(target, 'email', None))
    return AdminActionLog.objects.create(
        actor=actor,
        target_user=target,
        action=action,
        reason=reason or '',
        metadata=data,
    )


def search_users(params):
    """
    Build the filtered/sorted user queryset from query params. Read-only.

    Params: ``search`` (email/full_name icontains), ``user_type``, the four bool
    flags in ``_BOOL_FILTERS``, ``include_deleted`` (bool), ``sort`` (whitelist).
    """
    include_deleted = _parse_bool(params.get('include_deleted')) is True
    qs = User.objects.all_with_deleted() if include_deleted else User.objects.all()

    search = (params.get('search') or '').strip()
    if search:
        # Trigram indexes need ≥3 chars to help; a 1-char term degrades to a
        # scan and matches almost everything. Require a small minimum.
        if len(search) < 2:
            raise AdminUserActionError('Search term must be at least 2 characters.', 400)
        qs = qs.filter(Q(email__icontains=search) | Q(full_name__icontains=search))

    user_type = (params.get('user_type') or '').strip()
    if user_type:
        if user_type not in _VALID_USER_TYPES:
            raise AdminUserActionError('Invalid user_type filter.', 400)
        qs = qs.filter(user_type=user_type)

    for field in _BOOL_FILTERS:
        parsed = _parse_bool(params.get(field))
        if parsed is not None:
            qs = qs.filter(**{field: parsed})

    sort = (params.get('sort') or '-registration_date').strip()
    if sort not in _SORT_WHITELIST:
        raise AdminUserActionError('Invalid sort option.', 400)

    return qs.order_by(_SORT_WHITELIST[sort], 'id')


def _get_target(pk, *, with_deleted=False, lock=False):
    manager = User.objects.all_with_deleted() if with_deleted else User.objects
    qs = manager.select_for_update() if lock else manager
    try:
        return qs.get(pk=pk)
    except User.DoesNotExist:
        raise AdminUserActionError('User not found.', 404)


@transaction.atomic
def suspend_user(actor, pk, reason=''):
    """Restrict + deactivate a user so new logins AND existing tokens are blocked."""
    target = _get_target(pk, lock=True)
    if target.pk == actor.pk:
        raise AdminUserActionError('You cannot suspend your own account.', 422)
    if _is_admin(target):
        raise AdminUserActionError('Administrator accounts cannot be suspended here.', 422)
    if target.is_restricted_by_admin and not target.is_active:
        raise AdminUserActionError('This account is already suspended.', 422)

    target.is_restricted_by_admin = True
    target.is_active = False
    target.save(update_fields=['is_restricted_by_admin', 'is_active', 'updated_at'])

    log_admin_action(
        actor, AdminActionLog.Action.SUSPEND, target, reason=reason,
        metadata={'is_active': False, 'is_restricted_by_admin': True},
    )

    # Revoke outstanding refresh tokens so the user cannot mint a fresh access
    # token via /token/refresh/. Best-effort: the helper logs (CRITICAL) but
    # swallows its own errors, so a token-cleanup blip never blocks a suspend —
    # login is already blocked by is_active=False / is_restricted_by_admin.
    blacklist_all_refresh_tokens(target)
    logger.info('admin %s suspended user %s', actor.pk, target.pk)
    # Notify after commit only — a rolled-back suspend must not email.
    transaction.on_commit(
        lambda: _dispatch_account_email('account.suspended', target, reason=reason)
    )
    return target


@transaction.atomic
def reactivate_user(actor, pk):
    """Lift a suspension: clear the restriction and re-activate."""
    target = _get_target(pk, lock=True)
    # Only lift an admin suspension. A user who is merely is_active=False for a
    # non-suspension reason must not be silently re-activated here.
    if not target.is_restricted_by_admin:
        raise AdminUserActionError('This account is not suspended.', 422)

    target.is_restricted_by_admin = False
    target.is_active = True
    target.save(update_fields=['is_restricted_by_admin', 'is_active', 'updated_at'])

    log_admin_action(
        actor, AdminActionLog.Action.REACTIVATE, target,
        metadata={'is_active': True, 'is_restricted_by_admin': False},
    )
    logger.info('admin %s reactivated user %s', actor.pk, target.pk)

    transaction.on_commit(
        lambda: _dispatch_account_email('account.reactivated', target)
    )
    return target


def _dispatch_account_email(event_type, target, reason=''):
    """Send an account-status notification. Lazy import avoids an app-load cycle."""
    from notifications.services.dispatcher import dispatch

    ctx = {'reason': reason} if reason else {}
    dispatch(event_type, [target], context=ctx)


@transaction.atomic
def change_user_role(actor, pk, *, new_user_type=None, is_staff=None):
    """
    Change ``user_type`` and/or grant/revoke ``is_staff``.

    Switching ``user_type`` provisions the target profile (the create-time signal
    no longer fires); the previous type's profile is left dormant — deleting it
    would cascade real content.
    """
    if new_user_type is None and is_staff is None:
        raise AdminUserActionError('Provide user_type and/or is_staff.', 400)

    target = _get_target(pk, lock=True)
    if target.pk == actor.pk:
        raise AdminUserActionError('You cannot change your own role.', 422)

    update_fields = ['updated_at']
    old_type = target.user_type
    old_is_staff = target.is_staff

    if new_user_type is not None:
        if new_user_type not in _VALID_USER_TYPES:
            raise AdminUserActionError('Invalid user_type.', 400)
        if new_user_type == old_type:
            raise AdminUserActionError('User already has this role.', 422)
        target.user_type = new_user_type
        update_fields.append('user_type')

    if is_staff is not None:
        target.is_staff = _coerce_bool_strict(is_staff)
        update_fields.append('is_staff')

    target.save(update_fields=update_fields)

    if new_user_type is not None:
        ensure_profile_for_type(target)

    log_admin_action(
        actor, AdminActionLog.Action.ROLE_CHANGE, target,
        metadata={
            'old_user_type': old_type,
            'new_user_type': target.user_type,
            'old_is_staff': old_is_staff,
            'new_is_staff': target.is_staff,
        },
    )
    logger.info(
        'admin %s changed role of user %s: type %s->%s, is_staff %s->%s',
        actor.pk, target.pk, old_type, target.user_type, old_is_staff, target.is_staff,
    )
    return target

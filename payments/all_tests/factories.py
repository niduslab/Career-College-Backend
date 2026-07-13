"""Shared fixtures for payment tests."""
import hashlib
from decimal import Decimal

from django.conf import settings

from authentication.models import User
from courses.models import NidusCourse


def make_user(email, user_type='learner', name=None):
    return User.objects.create_user(
        email=email,
        password='pw12345!',
        full_name=name or email.split('@')[0],
        user_type=user_type,
        is_email_verified=True,
    )


def make_course(instructor, *, slug, price='49.00', published=True, delivery_mode=None):
    course = NidusCourse.objects.create(
        created_by=instructor,
        title=slug.replace('-', ' ').title(),
        slug=slug,
        description='Payment test course.',
        status=(
            NidusCourse.CourseStatus.PUBLISHED if published
            else NidusCourse.CourseStatus.DRAFT
        ),
        price=Decimal(price),
        delivery_mode=delivery_mode or NidusCourse.DeliveryMode.SELF_PACED,
    )
    course.instructors.add(instructor)
    return course


def valid_validation_response(order, **overrides):
    """A gateway validation payload that passes every check for `order`."""
    data = {
        'status': 'VALID',
        'tran_id': order.tran_id,
        'val_id': 'VAL0001',
        'amount': str(order.amount),
        'currency': order.currency,
        'store_id': 'test-store',
    }
    data.update(overrides)
    return data


def signed_callback(**fields):
    """Build a callback POST dict with a valid SSLCommerz `verify_sign` over
    `fields`, computed with the same algorithm the verifier uses."""
    verify_key = ','.join(sorted(fields))
    pairs = {k: str(v) for k, v in fields.items()}
    pairs['store_passwd'] = hashlib.md5(
        settings.SSLCOMMERZ_STORE_PASSWORD.encode()
    ).hexdigest()
    hash_string = '&'.join(f'{k}={pairs[k]}' for k in sorted(pairs))
    verify_sign = hashlib.md5(hash_string.encode()).hexdigest()
    return {**{k: str(v) for k, v in fields.items()}, 'verify_key': verify_key, 'verify_sign': verify_sign}

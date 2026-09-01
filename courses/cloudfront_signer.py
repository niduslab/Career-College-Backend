"""CloudFront signed-**cookie** helper for HLS video streaming.

Signed cookies (not signed URLs) are the correct fit for HLS: the browser
attaches them automatically to every `.m3u8` and `.ts` request under the
same host+path, so all sibling segments are authorized in one shot. Signed
URLs cannot do this — the query-string signature only applies to the master
playlist URL.

Returns ``None`` (never raises) when CloudFront is not configured, so local
dev can transparently fall back to the storage-relative URL.

Deployment note: for the browser to attach the cookies to CloudFront
requests, the CloudFront distribution must be reachable at a subdomain of
``CLOUDFRONT_COOKIE_DOMAIN`` (e.g. cookies with ``Domain=.example.com``
attach to ``videos.example.com``). If you're using the default
``d123.cloudfront.net`` host, set up a custom CNAME first — cookies scoped
to your API domain won't be sent to ``cloudfront.net``.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Optional, TypedDict

from django.conf import settings


class StreamContext(TypedDict):
    """What the view needs to serve a signed HLS response.

    ``build_signed_hls_cookies`` either returns ``None`` (CloudFront not
    configured) or a fully populated ``StreamContext``. So when the caller
    holds one, ``cookies`` and ``cookie_path`` are always non-empty;
    ``cookie_domain`` is ``None`` only when ``CLOUDFRONT_COOKIE_DOMAIN``
    was left unset (the browser will scope cookies to the API host — the
    fallback path is used instead when this actually matters).
    """
    playback_url: str
    cookies: dict
    cookie_path: str
    cookie_domain: Optional[str]


def _b64_url_safe(raw: bytes) -> str:
    """CloudFront-style base64 (+ / = replaced with - _ ~)."""
    return base64.b64encode(raw).decode('utf-8').replace('+', '-').replace('=', '_').replace('/', '~')


def _rsa_sign_sha1(private_key_pem: bytes, message: bytes) -> bytes:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = serialization.load_pem_private_key(
        private_key_pem, password=None, backend=default_backend()
    )
    return private_key.sign(message, padding.PKCS1v15(), hashes.SHA1())


def build_signed_hls_cookies(storage_relative_key: str) -> Optional[StreamContext]:
    """Build a signed-cookie context for an HLS master playlist.

    ``storage_relative_key`` is the storage-relative path the transcoder
    wrote (``courses/<slug>/lectures/<id>/hls/<va>/master.m3u8``). The
    signer prepends ``AWS_LOCATION`` (S3 key prefix used by
    django-storages), signs a wildcard covering every sibling `.m3u8`
    variant and `.ts` segment, and returns the plain playback URL plus
    the three cookies the view should Set-Cookie.

    Returns ``None`` when CloudFront is not configured — the caller should
    fall back to an unsigned storage URL for local dev.
    """
    if not storage_relative_key:
        return None

    domain = getattr(settings, 'CLOUDFRONT_DOMAIN', '') or ''
    key_id = getattr(settings, 'CLOUDFRONT_KEY_ID', '') or ''
    private_key_pem = getattr(settings, 'CLOUDFRONT_PRIVATE_KEY', '') or ''
    if not (domain and key_id and private_key_pem):
        return None

    aws_location = getattr(settings, 'AWS_LOCATION', '') or ''
    prefix_parts = [p for p in (aws_location.strip('/'), storage_relative_key.strip('/')) if p]
    object_path = '/'.join(prefix_parts)

    # Wildcard covers every sibling playlist + .ts segment under the same
    # directory. Path prefix ends with '/' so the cookie path scopes
    # cleanly to just this video's folder.
    slash_index = object_path.rfind('/')
    prefix_path = object_path[:slash_index + 1] if slash_index >= 0 else ''
    resource = f'https://{domain}/{prefix_path}*'
    playback_url = f'https://{domain}/{object_path}'

    ttl_seconds = int(getattr(settings, 'CLOUDFRONT_SIGNED_URL_TTL_SECONDS', 7200))
    expire_time = int(time.time()) + ttl_seconds

    policy = {
        'Statement': [
            {
                'Resource': resource,
                'Condition': {'DateLessThan': {'AWS:EpochTime': expire_time}},
            }
        ]
    }
    policy_bytes = json.dumps(policy, separators=(',', ':')).encode('utf-8')
    signature = _rsa_sign_sha1(private_key_pem.encode('utf-8'), policy_bytes)

    cookies = {
        'CloudFront-Policy': _b64_url_safe(policy_bytes),
        'CloudFront-Signature': _b64_url_safe(signature),
        'CloudFront-Key-Pair-Id': key_id,
    }
    # Scope cookies to just this video's directory. Different videos get
    # different cookie sets, so one playback session can't authorize
    # another lecture.
    cookie_path = f'/{prefix_path}'
    cookie_domain = getattr(settings, 'CLOUDFRONT_COOKIE_DOMAIN', '') or None

    return StreamContext(
        playback_url=playback_url,
        cookies=cookies,
        cookie_path=cookie_path,
        cookie_domain=cookie_domain,
    )

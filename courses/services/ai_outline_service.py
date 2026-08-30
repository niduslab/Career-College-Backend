"""Thin client for the AI services project (FastAPI) — pure HTTP I/O, no
business logic. Mirrors payments/services/sslcommerz_service.py.

One call: `generate_course_outline` asks the outline service for a module
breakdown. Server-to-server, authenticated with a shared secret header — no
end-user identity crosses this boundary, because Django has already authorized
the caller before it gets here.

Nothing is persisted. The caller decides what to do with the draft; see
`docs/architecture/32-ai-course-outline-generator.md`.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

COURSE_OUTLINE_ENDPOINT = '/v1/course-outline/'
# (connect, read) seconds. The read leg is long because generation is an LLM
# call, not a typical API round trip; the AI service's own LLM timeout (40s) is
# set below this so it gives up first and returns a real status.
#
# 45s is enough because the AI service's output is capped well below what would
# take longer: its `LLM_MAX_OUTPUT_TOKENS` is bounded by the Groq account's
# tokens-per-minute limit, so a generation cannot run away.
REQUEST_TIMEOUT = (5, 45)

_SERVICE_DOWN_MSG = 'Outline generation is temporarily unavailable. Please try again.'


class AIOutlineError(Exception):
    """Raised when the AI services project cannot produce an outline.

    Carries the HTTP status the view should return — same pattern as
    `ScheduleError` / `ReviewError` / `PaymentError`.
    """

    def __init__(self, message, http_status=503):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def generate_course_outline(*, title, description, audience, prerequisites='',
                            level='', language='English', duration_minutes=None,
                            category='', extra_instructions=''):
    """Request a course outline from the AI service.

    Args:
        title: Course title.
        description: Course description (plain text — strip markup first).
        audience: Intended audience, one segment per line.
        prerequisites: Pre-course requirements, one per line.
        level: `beginner` / `intermediate` / `advanced`, or blank.
        language: Language to write the outline in.
        duration_minutes: Target total duration; a hint, not a constraint.
        category: Free-text category hint.
        extra_instructions: Optional free-text steer from the instructor.

    Returns:
        The decoded response body: `{'modules': [...], 'outline_text': str}`.

    Raises:
        AIOutlineError: the service was unreachable, returned a non-200, or
            returned a body that is not JSON. Always a 503 — every failure
            here is transient from the caller's point of view, and the
            upstream reason must never reach the end user.
    """
    payload = {
        'title': title,
        'description': description,
        'audience': audience,
        'prerequisites': prerequisites,
        'level': level or None,
        'language': language,
        'duration_minutes': duration_minutes,
        'category': category or None,
        'extra_instructions': extra_instructions,
    }

    try:
        response = requests.post(
            f"{settings.AI_SERVICES_BASE_URL}{COURSE_OUTLINE_ENDPOINT}",
            json=payload,
            headers={'X-Service-Key': settings.AI_SERVICES_KEY},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        logger.exception('AI outline service unreachable for course "%s"', title)
        raise AIOutlineError(_SERVICE_DOWN_MSG, http_status=503)

    if response.status_code != 200:
        logger.error(
            'AI outline service rejected request for course "%s": status=%s',
            title, response.status_code,
        )
        raise AIOutlineError(_SERVICE_DOWN_MSG, http_status=503)

    try:
        return response.json()
    except ValueError:
        logger.exception(
            'AI outline service returned malformed JSON for course "%s"', title,
        )
        raise AIOutlineError(_SERVICE_DOWN_MSG, http_status=503)

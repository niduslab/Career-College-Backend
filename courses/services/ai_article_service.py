"""Thin client for the AI services project's article-lecture endpoint — pure
HTTP I/O, no business logic. Sibling of `ai_outline_service.py`; one module per
AI feature, matching the one-folder-per-service rule on the other side.

One call: `generate_article_lecture` asks the AI service to write the body of a
single article lecture. Server-to-server, authenticated with a shared secret
header — no end-user identity crosses this boundary, because Django has already
authorized the caller before it gets here.

Nothing is persisted. The instructor reads the draft, edits it, and decides
whether to save it onto the lecture; see
`docs/architecture/34-ai-article-lecture-generator.md`.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

ARTICLE_LECTURE_ENDPOINT = '/v1/article-lecture/'

REQUEST_TIMEOUT = (5, 45)

_SERVICE_DOWN_MSG = 'Article generation is temporarily unavailable. Please try again.'


class AIArticleError(Exception):
    """Raised when the AI services project cannot produce an article.

    Carries the HTTP status the view should return — same pattern as
    `AIOutlineError` / `ScheduleError` / `PaymentError`.
    """

    def __init__(self, message, http_status=503):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def generate_article_lecture(*, lecture_title, course_title='', section_title='',
                             description='', key_points=None, audience='',
                             level='', language='English',
                             target_duration_minutes=None,
                             include_code_examples=False, extra_instructions=''):
    """Request the body of one article lecture from the AI service.

    Args:
        lecture_title: The lesson's own title — the only required field.
        course_title: Title of the course the lesson belongs to.
        section_title: Title of the module the lesson sits in.
        description: What the lesson should cover, in the instructor's words
            (plain text — strip markup first).
        key_points: Points the article must cover, one per entry. Typically the
            `description` the outline generator produced for this item.
        audience: Intended audience, one segment per line.
        level: `beginner` / `intermediate` / `advanced`, or blank.
        language: Language to write the article in.
        target_duration_minutes: Target *reading* time; a hint, not a constraint.
        include_code_examples: Allow code samples. Off by default — a code block
            in a non-programming lesson is worse than none.
        extra_instructions: Optional free-text steer from the instructor.

    Returns:
        The decoded response body: `{'summary', 'sections', 'takeaways_heading',
        'key_takeaways', 'article_html', 'word_count',
        'estimated_reading_minutes'}`.

    Raises:
        AIArticleError: the service was unreachable, returned a non-200, or
            returned a body that is not JSON. Always a 503 — every failure here
            is transient from the caller's point of view, and the upstream
            reason must never reach the end user.
    """
    payload = {
        'lecture_title': lecture_title,
        'course_title': course_title,
        'section_title': section_title,
        'description': description,
        'key_points': key_points or [],
        'audience': audience,
        'level': level or None,
        'language': language,
        'target_duration_minutes': target_duration_minutes,
        'include_code_examples': include_code_examples,
        'extra_instructions': extra_instructions,
    }

    try:
        response = requests.post(
            f"{settings.AI_SERVICES_BASE_URL}{ARTICLE_LECTURE_ENDPOINT}",
            json=payload,
            headers={'X-Service-Key': settings.AI_SERVICES_KEY},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        logger.exception(
            'AI article service unreachable for lecture "%s"', lecture_title,
        )
        raise AIArticleError(_SERVICE_DOWN_MSG, http_status=503)

    if response.status_code != 200:
        logger.error(
            'AI article service rejected request for lecture "%s": status=%s',
            lecture_title, response.status_code,
        )
        raise AIArticleError(_SERVICE_DOWN_MSG, http_status=503)

    try:
        return response.json()
    except ValueError:
        logger.exception(
            'AI article service returned malformed JSON for lecture "%s"',
            lecture_title,
        )
        raise AIArticleError(_SERVICE_DOWN_MSG, http_status=503)

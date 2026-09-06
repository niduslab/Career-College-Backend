"""Thin client for the AI services project's coding-exercise endpoint — pure HTTP
I/O, no business logic. Sibling of `ai_outline_service.py`, `ai_article_service.py`
and `ai_quiz_service.py`; one module per AI feature.

One call: `generate_coding_exercise` asks the AI service to write a complete
exercise — description, starter code, solution and evaluation script. Nothing is
executed here and nothing is persisted; the caller runs the result through the
existing coding sandbox before the instructor sees it. See
`docs/architecture/36-ai-coding-exercise-generator.md`.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

CODING_EXERCISE_ENDPOINT = '/v1/coding-exercise/'

# (connect, read). The read leg must stay above the AI service's own
# LLM_TIMEOUT_SECONDS (40s) so that service fails first with a real status.
REQUEST_TIMEOUT = (5, 45)

_SERVICE_DOWN_MSG = 'Exercise generation is temporarily unavailable. Please try again.'


class AICodingError(Exception):
    """Raised when the AI services project cannot produce an exercise.

    Carries the HTTP status the view should return — same pattern as
    `AIQuizError` / `AIArticleError` / `ScheduleError`.
    """

    def __init__(self, message, http_status=503):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def generate_coding_exercise(*, exercise_title, language, course_title='',
                             section_title='', exercise_description='',
                             source_material='', audience='', level='',
                             natural_language='English', difficulty='core',
                             topic_hint='', time_limit_ms=2000,
                             avoid_titles=None, extra_instructions=''):
    """Request one coding exercise from the AI service.

    Args:
        exercise_title: The exercise's own title.
        language: python | javascript | cpp | java. Decides the evaluation-script
            contract, so it comes from the stored exercise, never the browser.
        course_title: The course the exercise sits in.
        section_title: The module the exercise sits in.
        exercise_description: Whatever the instructor has written so far.
        source_material: The module's lecture text, from
            `section_context_service.build_section_source_material`.
        audience: The audience declared on the course.
        level: The course's level; blank is sent as null.
        natural_language: The language the description is written in.
        difficulty: intro | core | challenge.
        topic_hint: Optional steer, e.g. "binary search".
        time_limit_ms: The exercise's whole-suite budget, mirrored into the prompt.
        avoid_titles: Other exercises in the module, so a regenerate differs.
        extra_instructions: Free-text steer from the instructor.

    Returns:
        The decoded response body: `{'description', 'starter_code',
        'solution_code', 'evaluation_script', 'test_names', 'language',
        'difficulty', 'grounded'}`.

    Raises:
        AICodingError: the service was unreachable, returned a non-200, or
            returned a body that is not JSON. Always a 503 — every failure here
            is transient from the caller's point of view, and the upstream
            reason must never reach the end user.
    """
    payload = {
        'exercise_title': exercise_title,
        'language': language,
        'course_title': course_title,
        'section_title': section_title,
        'exercise_description': exercise_description,
        'source_material': source_material,
        'audience': audience,
        'level': level or None,
        'natural_language': natural_language,
        'difficulty': difficulty,
        'topic_hint': topic_hint,
        'time_limit_ms': time_limit_ms,
        'avoid_titles': avoid_titles or [],
        'extra_instructions': extra_instructions,
    }

    try:
        response = requests.post(
            f"{settings.AI_SERVICES_BASE_URL}{CODING_EXERCISE_ENDPOINT}",
            json=payload,
            headers={'X-Service-Key': settings.AI_SERVICES_KEY},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        logger.exception(
            'AI coding service unreachable for exercise "%s"', exercise_title,
        )
        raise AICodingError(_SERVICE_DOWN_MSG, http_status=503)

    if response.status_code != 200:
        logger.error(
            'AI coding service rejected request for exercise "%s": status=%s',
            exercise_title, response.status_code,
        )
        raise AICodingError(_SERVICE_DOWN_MSG, http_status=503)

    try:
        return response.json()
    except ValueError:
        logger.exception(
            'AI coding service returned malformed JSON for exercise "%s"',
            exercise_title,
        )
        raise AICodingError(_SERVICE_DOWN_MSG, http_status=503)

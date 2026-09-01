"""Thin client for the AI services project's quiz-question endpoint — pure HTTP
I/O, no business logic. Sibling of `ai_outline_service.py` and
`ai_article_service.py`; one module per AI feature, matching the
one-folder-per-service rule on the other side.

One call: `generate_quiz_questions` asks the AI service to write multiple-choice
questions for a quiz. Server-to-server, authenticated with a shared secret
header — no end-user identity crosses this boundary, because Django has already
authorized the caller before it gets here.

The context this sends is assembled by `courses/services/quiz_service.py`, not by
the browser. Nothing is persisted: the instructor reads the draft, edits it, and
decides which questions to keep; see
`docs/architecture/35-ai-quiz-question-generator.md`.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

QUIZ_QUESTIONS_ENDPOINT = '/v1/quiz-questions/'

# (connect, read). The read timeout must stay ABOVE the AI service's own
# LLM_TIMEOUT_SECONDS (40s) so that service gives up first and returns a real
# status, instead of being cut off mid-generation by this client.
REQUEST_TIMEOUT = (5, 45)

_SERVICE_DOWN_MSG = 'Question generation is temporarily unavailable. Please try again.'


class AIQuizError(Exception):
    """Raised when the AI services project cannot produce questions.

    Carries the HTTP status the view should return — same pattern as
    `AIOutlineError` / `AIArticleError` / `ScheduleError`.
    """

    def __init__(self, message, http_status=503):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def generate_quiz_questions(*, quiz_title, course_title='', section_title='',
                            quiz_description='', source_material='', topics=None,
                            audience='', level='', language='English',
                            question_count=5, options_per_question=4,
                            difficulty='understanding', avoid_questions=None,
                            extra_instructions=''):
    """Request multiple-choice questions for one quiz from the AI service.

    Args:
        quiz_title: The quiz's own title — the only required field.
        course_title: The course the quiz sits in.
        section_title: The module the quiz sits in.
        quiz_description: The instructor's statement of what the quiz covers.
        source_material: The lecture text the questions must be answerable from,
            assembled by `quiz_service.build_quiz_source_material`.
        topics: Optional topic hints.
        audience: The audience declared on the course.
        level: The course's level; blank is sent as null.
        language: The course's language.
        question_count: How many questions to ask for (1-15).
        options_per_question: Options per question (2-5); 2 gives true/false.
        difficulty: recall | understanding | application.
        avoid_questions: Questions the quiz already has, so a regenerate does
            not return the same ones.
        extra_instructions: Free-text steer from the instructor.

    Returns:
        The decoded response body: `{'questions', 'grounded', 'requested_count'}`.

    Raises:
        AIQuizError: the service was unreachable, returned a non-200, or
            returned a body that is not JSON. Always a 503 — every failure here
            is transient from the caller's point of view, and the upstream
            reason must never reach the end user.
    """
    payload = {
        'quiz_title': quiz_title,
        'course_title': course_title,
        'section_title': section_title,
        'quiz_description': quiz_description,
        'source_material': source_material,
        'topics': topics or [],
        'audience': audience,
        'level': level or None,
        'language': language,
        'question_count': question_count,
        'options_per_question': options_per_question,
        'difficulty': difficulty,
        'avoid_questions': avoid_questions or [],
        'extra_instructions': extra_instructions,
    }

    try:
        response = requests.post(
            f"{settings.AI_SERVICES_BASE_URL}{QUIZ_QUESTIONS_ENDPOINT}",
            json=payload,
            headers={'X-Service-Key': settings.AI_SERVICES_KEY},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        logger.exception(
            'AI quiz service unreachable for quiz "%s"', quiz_title,
        )
        raise AIQuizError(_SERVICE_DOWN_MSG, http_status=503)

    if response.status_code != 200:
        logger.error(
            'AI quiz service rejected request for quiz "%s": status=%s',
            quiz_title, response.status_code,
        )
        raise AIQuizError(_SERVICE_DOWN_MSG, http_status=503)

    try:
        return response.json()
    except ValueError:
        logger.exception(
            'AI quiz service returned malformed JSON for quiz "%s"', quiz_title,
        )
        raise AIQuizError(_SERVICE_DOWN_MSG, http_status=503)

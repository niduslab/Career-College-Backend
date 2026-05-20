"""
Deterministic rubric-based grading for assignment submissions.

`RubricGrader` is the single entry point. It evaluates a learner's
`answer_text` against a rubric snapshot (list of criterion objects copied
onto AssignmentSubmissionAnswer at submit time) and returns:

    (score, criterion_results, feedback)

Each criterion type has a dedicated matcher in `_MATCHERS`. Adding a new
type is additive: add an entry there and document it in the design doc.

This module has zero Django-model dependencies on purpose — that keeps it
trivially unit-testable with plain dicts.
"""

import re
from typing import Callable, Tuple


# A matcher receives the learner's `answer_text` plus the full criterion
# dict (so it can read `case_sensitive`, etc.) and returns True/False.
_Matcher = Callable[[str, dict], bool]


def _matcher_keyword(answer: str, criterion: dict) -> bool:
    needle = criterion['value']
    if criterion.get('case_sensitive', False):
        return needle in answer
    return needle.lower() in answer.lower()


def _matcher_regex(answer: str, criterion: dict) -> bool:
    flags = 0 if criterion.get('case_sensitive', False) else re.IGNORECASE
    return re.search(criterion['value'], answer, flags) is not None


def _matcher_min_length(answer: str, criterion: dict) -> bool:
    return len(answer.strip()) >= int(criterion['value'])


def _matcher_max_length(answer: str, criterion: dict) -> bool:
    return len(answer.strip()) <= int(criterion['value'])


def _matcher_any_of(answer: str, criterion: dict) -> bool:
    lowered = answer.lower()
    return any(kw.lower() in lowered for kw in criterion['value'])


def _matcher_all_of(answer: str, criterion: dict) -> bool:
    lowered = answer.lower()
    return all(kw.lower() in lowered for kw in criterion['value'])


_MATCHERS: dict[str, _Matcher] = {
    'keyword': _matcher_keyword,
    'regex': _matcher_regex,
    'min_length': _matcher_min_length,
    'max_length': _matcher_max_length,
    'any_of': _matcher_any_of,
    'all_of': _matcher_all_of,
}


class RubricGrader:
    """Evaluate a learner answer against a rubric snapshot."""

    def grade(
        self,
        answer_text: str,
        rubric_snapshot: list,
        max_score: int,
    ) -> Tuple[int, list, str]:
        """Return (score, criterion_results, feedback).

        - `score` is clamped to `max_score` even if the rubric's points sum
          is misconfigured — defense in depth against authoring bugs.
        - `criterion_results` mirrors the rubric: one dict per criterion,
          shape `{index, type, matched, points_awarded, feedback}`.
        - `feedback` is a newline-joined summary of the per-criterion feedback
          strings (whichever applied for each criterion). Empty if no
          criterion produced text.
        """
        answer_text = answer_text or ''
        if not rubric_snapshot:
            return 0, [], ''

        results = []
        total = 0
        feedback_lines: list[str] = []
        for idx, criterion in enumerate(rubric_snapshot):
            ctype = criterion.get('type')
            matcher = _MATCHERS.get(ctype)
            # An unknown type slipped past authoring validation (or the rubric
            # was hand-edited in the DB). Treat as a miss with zero points;
            # never crash the grading task on a single bad criterion.
            if matcher is None:
                results.append({
                    'index': idx,
                    'type': ctype,
                    'matched': False,
                    'points_awarded': 0,
                    'feedback': f'Unknown criterion type {ctype!r}; treated as a miss.',
                })
                continue

            try:
                matched = matcher(answer_text, criterion)
            except Exception as exc:
                # A single malformed criterion (e.g. a regex value that
                # somehow slipped past validation) shouldn't tank the whole
                # submission — record the failure and move on.
                results.append({
                    'index': idx,
                    'type': ctype,
                    'matched': False,
                    'points_awarded': 0,
                    'feedback': f'Criterion evaluation failed: {exc}',
                })
                continue

            points = int(criterion.get('points', 0) or 0)
            points_awarded = points if matched else 0
            total += points_awarded

            feedback_key = 'feedback_on_match' if matched else 'feedback_on_miss'
            feedback_text = (criterion.get(feedback_key) or '').strip()
            if feedback_text:
                feedback_lines.append(feedback_text)

            results.append({
                'index': idx,
                'type': ctype,
                'matched': matched,
                'points_awarded': points_awarded,
                'feedback': feedback_text,
            })

        # Clamp to max_score so a rubric whose criteria-points sum exceeds
        # question.points can't award more than the question is worth.
        clamped = min(total, max_score)
        return clamped, results, '\n'.join(feedback_lines)

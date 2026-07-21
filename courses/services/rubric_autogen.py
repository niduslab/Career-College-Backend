"""Derive a deterministic rubric from a model answer.

Used when an instructor writes a Model Answer but leaves the rubric empty.
Instead of the question silently grading to 0 (see `assignment_grading.py`),
we extract the model answer's most significant words and split them into
several `all_of` groups, dividing the question's points across the groups. No
ML — pure frequency-based keyword extraction.

The output shape matches what `RubricGrader` and the authoring serializer's
`_validate_rubric_criteria` expect: a list of `all_of` criterion dicts whose
`points` sum to exactly `points` (fallback path) or are all 0 (manual-points
path, where the instructor assigns them in the UI).

Kept free of Django-model imports on purpose — trivially unit-testable with
plain strings/dicts.
"""

import re
from collections import Counter

# Minimal English stopword set. These carry no grading signal, so they are
# never turned into keyword criteria. Extend as needed.
_STOPWORDS = frozenset({
    'the', 'a', 'an', 'and', 'or', 'but', 'is', 'are', 'was', 'were', 'be',
    'been', 'being', 'to', 'of', 'in', 'on', 'at', 'for', 'with', 'as', 'by',
    'that', 'this', 'it', 'its', 'from', 'which', 'who', 'whom', 'what',
    'when', 'where', 'why', 'how', 'you', 'your', 'we', 'our', 'us', 'they',
    'them', 'their', 'i', 'he', 'she', 'his', 'her', 'not', 'no', 'can',
    'will', 'would', 'should', 'could', 'has', 'have', 'had', 'do', 'does',
    'did', 'so', 'if', 'then', 'than', 'such', 'these', 'those', 'there',
    'here', 'about', 'into', 'over', 'under', 'more', 'most', 'some', 'any',
    'all', 'each', 'both', 'other', 'also', 'because', 'while', 'during',
})

# A "word" is a letter-led token of length >= 2 (digits/apostrophes/hyphens
# allowed inside). The length floor is applied after this so tokens like "ai"
# survive but single letters do not.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]+")

# Default number of keyword criteria to emit. Tunable per call.
DEFAULT_MAX_TERMS = 5

# Words shorter than this are dropped as low-signal.
_MIN_WORD_LENGTH = 3


def generate_rubric_from_model_answer(
    model_answer: str,
    points: int,
    max_terms: int = DEFAULT_MAX_TERMS,
    split_points: bool = True,
) -> list:
    """Build a keyword rubric from a model answer.

    Args:
        model_answer: the instructor's ideal answer text.
        points: the question's total points.
        max_terms: cap on how many keyword criteria to emit.
        split_points: when True, `points` is split evenly across the criteria
            (remainder loaded onto the earliest ones) so the sum equals
            `points` exactly — used by the silent server-side fallback so a
            skipped rubric still grades. When False, every criterion is emitted
            with **0 points**, leaving the instructor to assign points manually
            in the UI (the authoring UI blocks Save until the sum matches the
            question's points).

    Returns:
        A list of criterion dicts (may be empty). Empty when the answer is
        blank, `points <= 0`, or no usable keyword survives filtering — in
        which case grading falls back to its existing 0-score behavior.
    """
    text = (model_answer or '').strip()
    if not text or points <= 0 or max_terms <= 0:
        return []

    words = [w.lower() for w in _WORD_RE.findall(text)]
    counts = Counter(
        w for w in words
        if len(w) >= _MIN_WORD_LENGTH and w not in _STOPWORDS
    )
    if not counts:
        return []

    # Deterministic ordering: most frequent first, ties broken alphabetically
    # so the same answer always yields the same rubric.
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    terms = [word for word, _ in ranked[:max_terms]]
    n = len(terms)

    # Split the keywords into several `all_of` groups and divide the points
    # across the groups. Group count is capped by both the points (so each
    # group can hold >= 1 point in the split path) and the keyword count (so no
    # group is empty): one point per group at most, one keyword per group at
    # least. `points` is always used to decide the grouping even in manual mode
    # (the preview endpoint passes the question's real points), so the shape is
    # identical whether or not the points are zeroed out.
    num_groups = max(1, min(points, n))
    kw_base, kw_rem = divmod(n, num_groups)
    pt_base, pt_rem = divmod(points, num_groups)

    rubric = []
    cursor = 0
    for g in range(num_groups):
        take = kw_base + (1 if g < kw_rem else 0)
        group_terms = terms[cursor:cursor + take]
        cursor += take
        # Manual mode zeroes the points; the instructor sets them in the UI.
        awarded = (pt_base + (1 if g < pt_rem else 0)) if split_points else 0
        joined = ', '.join(group_terms)
        rubric.append({
            'type': 'all_of',
            'value': group_terms,
            'points': awarded,
            'feedback_on_match': f'Good — your answer covered: {joined}.',
            'feedback_on_miss': f'Make sure your answer addresses: {joined}.',
        })
    return rubric

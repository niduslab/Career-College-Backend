from rest_framework import serializers

from courses.all_models.course_models import NidusCourse


class CourseOutlineRequestSerializer(serializers.Serializer):
    """Body for the AI outline-preview endpoint.

    Field-shape validation only — the request is forwarded to the AI service,
    nothing is looked up or written here. Fields mirror the `NidusCourse`
    columns the instructor has already filled in, except `category` (a
    free-text hint, deliberately not a `CourseCategory` id) and
    `extra_instructions` (a free-text steer, also what makes a regenerate
    produce something different).
    """

    title = serializers.CharField(max_length=255)
    description = serializers.CharField(max_length=8000)
    audience = serializers.CharField(max_length=2000)
    prerequisites = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=2000,
    )
    level = serializers.ChoiceField(
        choices=NidusCourse.CourseLevel.choices,
        required=False, allow_blank=True, default='',
    )
    language = serializers.CharField(required=False, default='English', max_length=50)
    duration_minutes = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=0,
    )
    category = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=120,
    )
    extra_instructions = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=2000,
    )


class ArticleLectureRequestSerializer(serializers.Serializer):
    """Body for the AI article-lecture preview endpoint.

    Field-shape validation only — the request is forwarded to the AI service,
    nothing is looked up or written here. `lecture_title` is the only required
    field; everything else is context the builder already has on screen (the
    course and module the lesson sits in, the audience declared on the course,
    and the points the outline generator suggested for this item).

    Deliberately **not** a lecture id: the endpoint drafts content for a lesson
    the instructor may not have created yet, and taking an id would imply the
    result is written to it. Nothing here touches the database.
    """

    lecture_title = serializers.CharField(max_length=255)
    course_title = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=255,
    )
    section_title = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=255,
    )
    description = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=2000,
    )
    key_points = serializers.ListField(
        child=serializers.CharField(max_length=300),
        required=False, default=list, max_length=12,
    )
    audience = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=2000,
    )
    level = serializers.ChoiceField(
        choices=NidusCourse.CourseLevel.choices,
        required=False, allow_blank=True, default='',
    )
    language = serializers.CharField(required=False, default='English', max_length=50)
    # Reading time, not video length. Capped at two hours — beyond that the
    # value is a typo, and the AI service clamps its word budget anyway.
    target_duration_minutes = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=0, max_value=120,
    )
    # Off by default: a code block in a non-programming lesson is worse than
    # none, and the model volunteers them freely.
    include_code_examples = serializers.BooleanField(required=False, default=False)
    extra_instructions = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=2000,
    )


class QuizQuestionsRequestSerializer(serializers.Serializer):
    """Body for the AI quiz-questions preview endpoint.

    Unlike its two siblings this takes a **resource id**: the grounding material
    and the questions already asked are server-side facts, and letting the
    browser send them would let it choose what the model sees. Denial is
    therefore a 404, per the project's identifier-type rule.

    `**validated_data` is *not* splatted into the service — `quiz_id` is not one
    of its arguments.
    """

    quiz_id = serializers.IntegerField(min_value=1)
    question_count = serializers.IntegerField(
        required=False, default=5, min_value=1, max_value=15,
    )
    # 2 gives true/false; the Django schema has one question type.
    options_per_question = serializers.IntegerField(
        required=False, default=4, min_value=2, max_value=5,
    )
    # What the question asks of the learner, independent of the course `level`.
    difficulty = serializers.ChoiceField(
        choices=['recall', 'understanding', 'application'],
        required=False, default='understanding',
    )
    topics = serializers.ListField(
        child=serializers.CharField(max_length=200),
        required=False, default=list, max_length=12,
    )
    # Unsaved drafts on screen; the server cannot see them, and a regenerate
    # would otherwise repeat them.
    avoid_questions = serializers.ListField(
        child=serializers.CharField(max_length=1000),
        required=False, default=list, max_length=30,
    )
    extra_instructions = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=2000,
    )

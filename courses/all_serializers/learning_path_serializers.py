from rest_framework import serializers

from courses.all_models.learning_path_models import LearningPath, LearningPathMilestone


class _MilestoneCourseBriefSerializer(serializers.Serializer):
    """Minimal course descriptor for a milestone card — a path list must not
    drag a full catalog card per milestone."""

    id = serializers.IntegerField()
    title = serializers.CharField()
    slug = serializers.CharField()
    thumbnail = serializers.SerializerMethodField()

    def get_thumbnail(self, obj):
        return obj.thumbnail.url if obj.thumbnail else None


class LearningPathMilestoneSerializer(serializers.ModelSerializer):
    """Public milestone shape, no progress — see LearningPathMilestoneProgressSerializer
    for the learner-facing version with derived status."""

    course = _MilestoneCourseBriefSerializer(read_only=True)
    title = serializers.SerializerMethodField()

    class Meta:
        model = LearningPathMilestone
        fields = ['id', 'position', 'title', 'course']

    def get_title(self, obj):
        return obj.title or obj.course.title


class LearningPathMilestoneProgressSerializer(serializers.Serializer):
    """One milestone plus the caller's derived status. Built from the dict
    rows returned by build_milestone_progress(), not a model instance."""

    id = serializers.IntegerField(source='milestone.id')
    position = serializers.IntegerField(source='milestone.position')
    title = serializers.SerializerMethodField()
    course = serializers.SerializerMethodField()
    status = serializers.CharField()

    def get_title(self, row):
        m = row['milestone']
        return m.title or m.course.title

    def get_course(self, row):
        return _MilestoneCourseBriefSerializer(row['milestone'].course).data


class LearningPathListSerializer(serializers.ModelSerializer):
    """Card shape for the public path list — milestone count + first few
    course thumbnails, not the full milestone list."""

    milestone_count = serializers.SerializerMethodField()

    class Meta:
        model = LearningPath
        fields = [
            'id',
            'title',
            'slug',
            'description',
            'career_goal',
            'skill_tags',
            'milestone_count',
            'created_at',
        ]

    def get_milestone_count(self, obj):
        return len(obj.milestones.all())


class LearningPathDetailSerializer(serializers.ModelSerializer):
    """Public path detail — milestones with no progress (guest/unauthenticated
    view). See LearningPathProgressSerializer for the learner-facing version."""

    milestones = LearningPathMilestoneSerializer(many=True, read_only=True)

    class Meta:
        model = LearningPath
        fields = [
            'id',
            'title',
            'slug',
            'description',
            'career_goal',
            'skill_tags',
            'milestones',
            'created_at',
        ]


class LearningPathProgressSerializer(serializers.ModelSerializer):
    """Path detail plus the caller's derived per-milestone status and overall
    progress percent. `milestones`, `progress_percent` and `is_enrolled` are
    injected by the view via serializer context — see LearningPathProgressView.

    `is_enrolled` is a real LearningPathEnrollment lookup, not derived from
    milestone status: a learner can complete a milestone's course entirely
    outside the path (independently, before ever joining it), so milestone #1
    reading "available" does NOT imply the learner has joined this path.
    """

    milestones = serializers.SerializerMethodField()
    progress_percent = serializers.SerializerMethodField()
    is_enrolled = serializers.SerializerMethodField()

    class Meta:
        model = LearningPath
        fields = [
            'id',
            'title',
            'slug',
            'description',
            'career_goal',
            'skill_tags',
            'milestones',
            'progress_percent',
            'is_enrolled',
            'created_at',
        ]

    def get_milestones(self, obj):
        rows = self.context['progress_rows']
        return LearningPathMilestoneProgressSerializer(rows, many=True).data

    def get_progress_percent(self, obj):
        return self.context['progress_percent']

    def get_is_enrolled(self, obj):
        return self.context['is_enrolled']


class LearningPathEnrollmentSerializer(serializers.Serializer):
    """One row of `my-learning-paths/` — the enrolled path plus derived progress."""

    id = serializers.IntegerField()
    path = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_path(self, obj):
        rows = self.context['progress_by_path_id'].get(obj.path_id, [])
        percent = self.context['percent_by_path_id'].get(obj.path_id, 0)
        return {
            'id': obj.path.id,
            'title': obj.path.title,
            'slug': obj.path.slug,
            'career_goal': obj.path.career_goal,
            'skill_tags': obj.path.skill_tags,
            'milestones': LearningPathMilestoneProgressSerializer(rows, many=True).data,
            'progress_percent': percent,
        }


# ---------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------

class LearningPathManageSerializer(serializers.ModelSerializer):
    """Create/update payload for an author's own path. `status` is a direct
    field set (draft/published/archived) — no state-machine complexity is
    needed for two meaningful transitions.

    `milestones` is read-only here (no progress — the author isn't enrolled
    as a learner) so the edit screen can render/reorder/remove them without a
    second request."""

    milestones = LearningPathMilestoneSerializer(many=True, read_only=True)

    class Meta:
        model = LearningPath
        fields = [
            'id',
            'title',
            'slug',
            'description',
            'career_goal',
            'skill_tags',
            'status',
            'milestones',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']


class MilestoneCreateSerializer(serializers.Serializer):
    """Payload for adding a milestone to an owned path."""

    course_id = serializers.IntegerField()
    title = serializers.CharField(max_length=200, allow_blank=True, required=False, default='')


class MilestoneReorderSerializer(serializers.Serializer):
    """Payload for reordering an owned path's milestones."""

    ordered_milestone_ids = serializers.ListField(child=serializers.IntegerField())

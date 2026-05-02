"""
Management command: populate_section_content

Backfills SectionContent rows for existing Lectures (and Quizzes) that
were created before the SectionContent model was introduced.

Safe to re-run: uses get_or_create-style duplicate protection.

Usage:
    python manage.py populate_section_content
    python manage.py populate_section_content --dry-run
"""

from django.contrib.contenttypes.models import ContentType as DjContentType
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Max

from courses.models import CourseSection, Lecture, Quiz, SectionContent


class Command(BaseCommand):
    help = 'Backfill SectionContent rows for legacy lectures and quizzes.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be created without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run: bool = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be saved.\n'))

        lecture_ct = DjContentType.objects.get_for_model(Lecture)
        quiz_ct = DjContentType.objects.get_for_model(Quiz)

        total_created = 0

        for section in CourseSection.objects.order_by('id').iterator():
            created = self._backfill_section(section, lecture_ct, quiz_ct, dry_run)
            total_created += created

        verb = 'Would create' if dry_run else 'Created'
        self.stdout.write(
            self.style.SUCCESS(f'\n{verb} {total_created} SectionContent row(s) across all sections.')
        )

    @transaction.atomic
    def _backfill_section(
        self,
        section: CourseSection,
        lecture_ct,
        quiz_ct,
        dry_run: bool,
    ) -> int:
        created_count = 0

        # IDs that already have a SectionContent slot (avoid duplicates).
        existing_lecture_ids = set(
            SectionContent.objects
            .filter(section=section, content_type=lecture_ct)
            .values_list('object_id', flat=True)
        )
        existing_quiz_ids = set(
            SectionContent.objects
            .filter(section=section, content_type=quiz_ct)
            .values_list('object_id', flat=True)
        )

        # Determine the starting position for new slots.
        result = SectionContent.objects.filter(section=section).aggregate(Max('position'))
        next_position = (result['position__max'] or 0) + 1

        # Lectures without a slot — order by id as stable fallback.
        unslotted_lectures = (
            Lecture.objects
            .filter(section=section)
            .exclude(id__in=existing_lecture_ids)
            .order_by('id')
        )
        for lecture in unslotted_lectures:
            self.stdout.write(
                f'  Section {section.id}: lecture {lecture.id} "{lecture.title}" → position {next_position}'
            )
            if not dry_run:
                SectionContent.objects.create(
                    section=section,
                    item_type=SectionContent.ItemType.LECTURE,
                    content_type=lecture_ct,
                    object_id=lecture.id,
                    position=next_position,
                )
            next_position += 1
            created_count += 1

        # Quizzes without a slot — order by id as stable fallback.
        unslotted_quizzes = (
            Quiz.objects
            .filter(section=section)
            .exclude(id__in=existing_quiz_ids)
            .order_by('id')
        )
        for quiz in unslotted_quizzes:
            self.stdout.write(
                f'  Section {section.id}: quiz {quiz.id} "{quiz.title}" → position {next_position}'
            )
            if not dry_run:
                SectionContent.objects.create(
                    section=section,
                    item_type=SectionContent.ItemType.QUIZ,
                    content_type=quiz_ct,
                    object_id=quiz.id,
                    position=next_position,
                )
            next_position += 1
            created_count += 1

        return created_count

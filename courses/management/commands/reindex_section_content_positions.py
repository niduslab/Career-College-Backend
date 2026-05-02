"""
Management command: reindex_section_content_positions

Reindexs SectionContent.position values to contiguous 1..n per section,
preserving current relative order by (position, id).

Usage:
    python manage.py reindex_section_content_positions
    python manage.py reindex_section_content_positions --section-id 1
    python manage.py reindex_section_content_positions --course-id 5
    python manage.py reindex_section_content_positions --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max

from courses.models import CourseSection, SectionContent


class Command(BaseCommand):
    help = 'Reindex SectionContent positions to contiguous values per section.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--section-id',
            type=int,
            help='Only reindex one section by id.',
        )
        parser.add_argument(
            '--course-id',
            type=int,
            help='Only reindex sections under this course id.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print planned changes without writing to the database.',
        )

    def handle(self, *args, **options):
        section_id = options.get('section_id')
        course_id = options.get('course_id')
        dry_run = options.get('dry_run', False)

        if section_id and course_id:
            raise CommandError('Use either --section-id or --course-id, not both.')

        sections = CourseSection.objects.order_by('id')
        if section_id:
            sections = sections.filter(id=section_id)
        elif course_id:
            sections = sections.filter(course_id=course_id)

        if not sections.exists():
            raise CommandError('No matching sections found.')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no database changes will be saved.\n'))

        total_sections_changed = 0
        total_rows_changed = 0

        for section in sections.iterator():
            changed_rows = self._reindex_section(section.id, dry_run=dry_run)
            if changed_rows > 0:
                total_sections_changed += 1
                total_rows_changed += changed_rows

        action_word = 'Would reindex' if dry_run else 'Reindexed'
        self.stdout.write(
            self.style.SUCCESS(
                f'\n{action_word} {total_rows_changed} row(s) across {total_sections_changed} section(s).'
            )
        )

    @transaction.atomic
    def _reindex_section(self, section_id: int, dry_run: bool) -> int:
        rows = list(
            SectionContent.objects
            .select_for_update()
            .filter(section_id=section_id)
            .order_by('position', 'id')
            .values('id', 'position')
        )
        if not rows:
            return 0

        planned_updates = []
        for idx, row in enumerate(rows, start=1):
            old_position = row['position']
            if old_position != idx:
                planned_updates.append((row['id'], old_position, idx))

        if not planned_updates:
            return 0

        self.stdout.write(f'Section {section_id}:')
        for row_id, old_position, new_position in planned_updates:
            self.stdout.write(f'  content {row_id}: {old_position} -> {new_position}')

        if dry_run:
            return len(planned_updates)

        max_position = (
            SectionContent.objects
            .filter(section_id=section_id)
            .aggregate(Max('position'))['position__max']
            or 0
        )
        temp_base = max_position + 1000

        # Phase 1: move impacted rows to a temporary non-overlapping range.
        for i, (row_id, _, _) in enumerate(planned_updates, start=1):
            SectionContent.objects.filter(pk=row_id).update(position=temp_base + i)

        # Phase 2: assign final contiguous positions.
        for row_id, _, new_position in planned_updates:
            SectionContent.objects.filter(pk=row_id).update(position=new_position)

        return len(planned_updates)

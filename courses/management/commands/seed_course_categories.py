"""
Management command: seed_course_categories

Seeds the CourseCategory table with a baseline set of marketplace categories.
Safe to re-run: uses update_or_create keyed on slug so existing rows are
refreshed in place rather than duplicated.

Usage:
    python manage.py seed_course_categories
    python manage.py seed_course_categories --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from courses.models import CourseCategory


CATEGORIES = [
    'Web Development',
    'Mobile Development',
    'Data Science',
    'Machine Learning',
    'Cloud Computing',
    'DevOps',
    'Cybersecurity',
    'Business & Entrepreneurship',
    'Design',
    'Marketing',
    'Personal Development',
    'Finance & Accounting',
]


class Command(BaseCommand):
    help = 'Seed the CourseCategory table with a baseline set of marketplace categories.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be created/updated without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run: bool = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be saved.\n'))

        created_count, updated_count = self._seed(dry_run)

        verb_create = 'Would create' if dry_run else 'Created'
        verb_update = 'Would update' if dry_run else 'Updated'
        self.stdout.write(
            self.style.SUCCESS(
                f'\n{verb_create} {created_count} and {verb_update} {updated_count} '
                f'CourseCategory row(s).'
            )
        )

    @transaction.atomic
    def _seed(self, dry_run: bool) -> tuple[int, int]:
        created_count = 0
        updated_count = 0

        for name in CATEGORIES:
            slug = slugify(name)

            existing = CourseCategory.objects.filter(slug=slug).first()
            if existing is None:
                self.stdout.write(f'  + {name} (slug={slug})')
                if not dry_run:
                    CourseCategory.objects.create(
                        name=name,
                        slug=slug,
                        is_active=True,
                    )
                created_count += 1
            else:
                self.stdout.write(f'  ~ {name} (slug={slug}) — refreshing')
                if not dry_run:
                    existing.name = name
                    existing.is_active = True
                    existing.save(update_fields=['name', 'is_active', 'updated_at'])
                updated_count += 1

        return created_count, updated_count

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
    {
        'name': 'Web Development',
        'description': 'Frontend, backend, and full-stack web engineering.',
    },
    {
        'name': 'Mobile Development',
        'description': 'iOS, Android, and cross-platform mobile app development.',
    },
    {
        'name': 'Data Science',
        'description': 'Data analysis, visualization, and statistical modeling.',
    },
    {
        'name': 'Machine Learning',
        'description': 'Supervised, unsupervised, and deep learning techniques.',
    },
    {
        'name': 'Cloud Computing',
        'description': 'AWS, Azure, GCP, and modern cloud-native architectures.',
    },
    {
        'name': 'DevOps',
        'description': 'CI/CD, infrastructure as code, containers, and observability.',
    },
    {
        'name': 'Cybersecurity',
        'description': 'Application security, network security, and ethical hacking.',
    },
    {
        'name': 'Business & Entrepreneurship',
        'description': 'Startups, product management, and business strategy.',
    },
    {
        'name': 'Design',
        'description': 'UI/UX, graphic design, and product design fundamentals.',
    },
    {
        'name': 'Marketing',
        'description': 'Digital marketing, SEO, content, and growth strategy.',
    },
    {
        'name': 'Personal Development',
        'description': 'Productivity, communication, and career skills.',
    },
    {
        'name': 'Finance & Accounting',
        'description': 'Personal finance, investing, and accounting fundamentals.',
    },
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

        for display_order, entry in enumerate(CATEGORIES, start=1):
            name = entry['name']
            slug = slugify(name)
            description = entry['description']

            existing = CourseCategory.objects.filter(slug=slug).first()
            if existing is None:
                self.stdout.write(f'  + {name} (slug={slug}, order={display_order})')
                if not dry_run:
                    CourseCategory.objects.create(
                        name=name,
                        slug=slug,
                        description=description,
                        display_order=display_order,
                        is_active=True,
                    )
                created_count += 1
            else:
                self.stdout.write(f'  ~ {name} (slug={slug}) — refreshing')
                if not dry_run:
                    existing.name = name
                    existing.description = description
                    existing.display_order = display_order
                    existing.is_active = True
                    existing.save(update_fields=['name', 'description', 'display_order', 'is_active', 'updated_at'])
                updated_count += 1

        return created_count, updated_count

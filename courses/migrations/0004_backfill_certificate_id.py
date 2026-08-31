import re

from django.db import migrations

_ID_PREFIX = 'CC'
_ID_ABBREV_FALLBACK = 'GEN'
_ID_ABBREV_MAX_LEN = 6
_ID_SEQUENCE_WIDTH = 6


def _slug_abbrev(slug):
    """Mirror of certificate_service._slug_abbrev — kept inline so the migration
    stays frozen even if the service's format is changed later."""
    for token in re.split(r'[^A-Za-z0-9]+', (slug or '').strip()):
        cleaned = token.upper()
        if cleaned:
            return cleaned[:_ID_ABBREV_MAX_LEN]
    return _ID_ABBREV_FALLBACK


def backfill_certificate_ids(apps, schema_editor):
    """Give every pre-existing certificate a human-readable credential ID.

    Deterministic: rows are numbered in issue order within each
    (year, course-abbrev) bucket, so a re-run on a copy of the same data
    produces the same IDs.

    Snapshot fields are deliberately left blank on these rows. They were issued
    before signatories existed, and filling them from today's profiles would
    fabricate a snapshot that was never true.
    """
    Certificate = apps.get_model('courses', 'Certificate')

    counters = {}
    rows = (
        Certificate.objects
        .filter(certificate_id__isnull=True)
        .select_related('enrollment__course')
        .order_by('issued_at', 'id')
    )
    for certificate in rows.iterator():
        course = certificate.enrollment.course
        prefix = f'{_ID_PREFIX}-{certificate.issued_at.year}-{_slug_abbrev(course.slug)}-'
        if prefix not in counters:
            counters[prefix] = Certificate.objects.filter(
                certificate_id__startswith=prefix
            ).count()
        counters[prefix] += 1
        certificate.certificate_id = f'{prefix}{counters[prefix]:0{_ID_SEQUENCE_WIDTH}d}'
        certificate.save(update_fields=['certificate_id'])


def noop_reverse(apps, schema_editor):
    """Backfilled IDs are permanent credentials — never stripped on reverse."""


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0003_certificate_authorized_signatory_designation_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_certificate_ids, noop_reverse),
    ]

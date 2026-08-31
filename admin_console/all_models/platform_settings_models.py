from django.db import models

from authentication.utils.upload_helpers import authorized_signature_path
from core.validators import validate_image_file, validate_signature_size


class PlatformSettings(models.Model):
    """
    Singleton (always pk=1) holding platform-wide branding and the default
    authorized signatory used on certificates.

    Certificates for institution-owned courses sign with that institution's own
    signatory; everything else (individual-instructor courses) falls back here.
    Read it with PlatformSettings.load(), never PlatformSettings.objects.get().
    """

    organization_name = models.CharField(
        max_length=200, default='Career College',
        help_text='Issuing organization printed on certificates.',
    )
    authorized_signatory_name = models.CharField(max_length=200, blank=True, default='')
    authorized_signatory_designation = models.CharField(max_length=200, blank=True, default='')
    authorized_signature = models.ImageField(
        upload_to=authorized_signature_path, blank=True, null=True,
        validators=[validate_image_file, validate_signature_size],
        help_text='Transparent PNG preferred. Copied onto certificates at issuance.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'platform_settings'
        verbose_name = 'Platform Settings'
        verbose_name_plural = 'Platform Settings'

    def __str__(self):
        return f'PlatformSettings({self.organization_name})'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """The singleton row is never deleted — clear its fields instead."""
        return

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

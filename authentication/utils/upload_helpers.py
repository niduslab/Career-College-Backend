import os
import uuid

from django.utils.text import slugify


def _slugify_upload(instance, filename, folder):
    """
    Generate a URL-friendly file path for uploads.

    Converts: "WhatsApp Image 2026-02-16 at 4.44.46 AM.jpeg"
    Into:     "<folder>/whatsapp-image-2026-02-16-at-44446-am_a1b2c3d4.jpeg"
    """
    name, ext = os.path.splitext(filename)
    slug = slugify(name) or 'upload'
    unique_suffix = uuid.uuid4().hex[:8]
    return f"{folder}/{slug}_{unique_suffix}{ext.lower()}"


def learner_photo_path(instance, filename):
    return _slugify_upload(instance, filename, 'learner_profiles/photos')


def instructor_photo_path(instance, filename):
    return _slugify_upload(instance, filename, 'instructor_profiles/photos')


def institution_logo_path(instance, filename):
    return _slugify_upload(instance, filename, 'partner_institutions/logos')


def institution_cover_path(instance, filename):
    return _slugify_upload(instance, filename, 'partner_institutions/covers')


def instructor_signature_path(instance, filename):
    return _slugify_upload(instance, filename, 'signatures/instructors')


def institution_signature_path(instance, filename):
    return _slugify_upload(instance, filename, 'signatures/authorized')


def authorized_signature_path(instance, filename):
    """Platform-wide authorized signatory (admin_console.PlatformSettings)."""
    return _slugify_upload(instance, filename, 'signatures/authorized')


def certificate_signature_path(instance, filename):
    """Per-certificate signature copies, frozen at issuance."""
    return _slugify_upload(instance, filename, 'certificates/signatures')

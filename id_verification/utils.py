import os
import uuid

from django.utils.text import slugify


def _slugify_upload(instance, filename, folder):
    name, ext = os.path.splitext(filename)
    slug = slugify(name) or 'upload'
    unique_suffix = uuid.uuid4().hex[:8]
    return f"{folder}/{slug}_{unique_suffix}{ext.lower()}"


def id_document_front_path(instance, filename):
    return _slugify_upload(instance, filename, 'id_verification/documents/front')


def id_document_back_path(instance, filename):
    return _slugify_upload(instance, filename, 'id_verification/documents/back')


def selfie_path(instance, filename):
    return _slugify_upload(instance, filename, 'id_verification/selfies')


def resume_path(instance, filename):
    return _slugify_upload(instance, filename, 'id_verification/resumes')


def institution_accreditation_path(instance, filename):
    return _slugify_upload(instance, filename, 'institution_verification/accreditation')


def institution_authorization_path(instance, filename):
    return _slugify_upload(instance, filename, 'institution_verification/authorization')

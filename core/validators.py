from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

validate_image_file = FileExtensionValidator(
    allowed_extensions=['jpg', 'jpeg', 'png', 'webp'],
    message='Upload a valid image. Allowed formats: JPG, JPEG, PNG, WEBP.',
)

validate_video_file = FileExtensionValidator(
    allowed_extensions=['mp4', 'mov', 'avi', 'mkv', 'webm'],
    message='Upload a valid video file. Allowed formats: MP4, MOV, AVI, MKV, WEBM.',
)


validate_document_file = FileExtensionValidator(
    allowed_extensions=['pdf', 'jpg', 'jpeg', 'png', 'webp'],
    message='Upload a valid document. Allowed formats: PDF, JPG, JPEG, PNG, WEBP.',
)


MAX_SIGNATURE_BYTES = 2 * 1024 * 1024


def validate_signature_size(value):
    """Cap signature uploads at 2 MB.

    Signatures are small transparent PNGs embedded into certificate PDFs, so a
    multi-megabyte upload is a mistake or an attempt to bloat storage.
    """
    try:
        size = value.size
    except (AttributeError, OSError):
        return  # size unavailable in some contexts (e.g. during migrations)
    if size > MAX_SIGNATURE_BYTES:
        raise ValidationError('Signature image must be 2 MB or smaller.')


def validate_pdf_file(value):
    FileExtensionValidator(
        allowed_extensions=['pdf'],
        message='Only PDF files are allowed.',
    )(value)
    # Verify PDF magic bytes (%PDF-) so a renamed non-PDF is rejected even if
    # the extension is correct.
    try:
        value.seek(0)
        header = value.read(5)
        value.seek(0)
        if header != b'%PDF-':
            raise ValidationError('File does not appear to be a valid PDF.')
    except (AttributeError, OSError):
        pass  # not seekable in all contexts (e.g. during migrations)

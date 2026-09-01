"""Direct-to-S3 multipart upload for lecture videos + CloudFront-signed
playback URLs.

Flow: initiate → (get part URL → PUT to S3) × N → complete. The client uploads
straight to S3 through presigned URLs; the backend never streams the bytes.

The transcoder (``courses.tasks.transcode_video_asset_task``) expects
``VideoAsset.video_file`` to resolve to the raw upload in default storage, so
``complete`` writes the storage-relative key onto the FileField before
enqueueing the task.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

import boto3
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from core.permissions import IsCourseCreator, IsEmailVerified
from core.validators import validate_video_file
from courses.all_models.content_models import Lecture, VideoAsset, VideoProcessingJob
from courses.utils import guard_editable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Throttles — brakes on uploads and stream-URL requests. Rates are env-driven
# so ops can tighten in production without a code change.
# ---------------------------------------------------------------------------

class VideoUploadInitiateThrottle(UserRateThrottle):
    scope = 'video_upload_initiate'
    rate = getattr(settings, 'VIDEO_UPLOAD_INITIATE_RATE_LIMIT', '30/hour')


class VideoStreamUrlThrottle(UserRateThrottle):
    scope = 'video_stream_url'
    rate = getattr(settings, 'VIDEO_STREAM_URL_RATE_LIMIT', '120/hour')


# ---------------------------------------------------------------------------
# S3 client — one per worker process, created lazily on first use.
# botocore.Client is thread-safe but not fork-safe; lazy init defers creation
# until after gunicorn forks so pre-fork imports don't share a client.
# ---------------------------------------------------------------------------

_s3_client_cached = None


def _s3_client():
    global _s3_client_cached
    if _s3_client_cached is None:
        region = getattr(settings, 'AWS_S3_REGION_NAME', '') or None
        _s3_client_cached = boto3.client('s3', region_name=region)
    return _s3_client_cached


class _UploadNotConfigured(Exception):
    """S3/CloudFront env vars aren't set. Surfaces as 503."""


def _bucket() -> str:
    bucket = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or ''
    if not bucket:
        raise _UploadNotConfigured('S3 storage is not configured.')
    return bucket


def _not_configured_response():
    return Response(
        {'success': False, 'message': 'Video upload is not configured for this environment.'},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _aws_location_prefix() -> str:
    loc = (getattr(settings, 'AWS_LOCATION', '') or '').strip('/')
    return f'{loc}/' if loc else ''


# ---------------------------------------------------------------------------
# Owner-scoped lookups — one query each. `Q(instructors=user) |
# Q(created_by=user)` matches roster instructors and partner-institution
# owners in the same shot. `.distinct()` because the M2M `instructors` join
# would otherwise duplicate the row.
# ---------------------------------------------------------------------------

def _get_owned_lecture(user, lecture_id) -> Optional[Lecture]:
    return (
        Lecture.objects
        .select_related('section__course')
        .filter(
            pk=lecture_id,
            lecture_type=Lecture.LectureType.VIDEO,
        )
        .filter(Q(section__course__instructors=user) | Q(section__course__created_by=user))
        .distinct()
        .first()
    )


def _get_owned_video_asset(user, video_asset_id) -> Optional[VideoAsset]:
    return (
        VideoAsset.objects
        .select_related('lecture__section__course')
        .filter(pk=video_asset_id)
        .filter(Q(lecture__section__course__instructors=user) | Q(lecture__section__course__created_by=user))
        .distinct()
        .first()
    )


def _lecture_not_found():
    return Response(
        {'success': False, 'message': 'Lecture not found.'},
        status=status.HTTP_404_NOT_FOUND,
    )


def _video_asset_not_found():
    return Response(
        {'success': False, 'message': 'Video asset not found.'},
        status=status.HTTP_404_NOT_FOUND,
    )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class LectureMultipartUploadInitiateView(APIView):
    """POST → { videoAssetId, uploadId, objectKey, partSize, maxParts }."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]
    throttle_classes = [VideoUploadInitiateThrottle]

    def post(self, request, lecture_id: int):
        lecture = _get_owned_lecture(request.user, lecture_id)
        if lecture is None:
            return _lecture_not_found()

        # Locked-course guard mirrors every other authoring endpoint. Pass
        # `section=` because we're modifying content that already exists
        # (this lecture) — a drip-released section blocks re-upload.
        locked = guard_editable(lecture.section.course, section=lecture.section)
        if locked is not None:
            return locked

        filename = (request.data.get('filename') or '').strip()
        content_type = (request.data.get('content_type') or 'video/mp4').strip()
        try:
            file_size = int(request.data.get('file_size') or 0)
        except (TypeError, ValueError):
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': {'file_size': 'Must be an integer.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not filename:
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': {'filename': 'This field is required.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Whitelist MIME type. Anything the client sends here is echoed back
        # by S3 as the object's Content-Type on future GETs, so a bogus
        # `text/html` could reflect XSS through a signed URL.
        if not content_type.lower().startswith('video/'):
            return Response(
                {
                    'success': False,
                    'message': 'Validation failed.',
                    'errors': {'content_type': 'Must be a video/* MIME type.'},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        ext = os.path.splitext(filename)[1].lower().lstrip('.') or 'mp4'
        allowed_exts = set(validate_video_file.allowed_extensions)
        if ext not in allowed_exts:
            return Response(
                {
                    'success': False,
                    'message': 'Validation failed.',
                    'errors': {'filename': f'Allowed formats: {", ".join(sorted(allowed_exts)).upper()}.'},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_size = int(getattr(settings, 'AWS_S3_MAX_UPLOAD_SIZE', 5 * 1024 * 1024 * 1024))
        if file_size <= 0 or file_size > max_size:
            return Response(
                {
                    'success': False,
                    'message': 'Validation failed.',
                    'errors': {'file_size': f'Must be between 1 byte and {max_size} bytes.'},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Config check BEFORE the DB write — no orphan VideoAsset if S3 isn't
        # configured yet.
        try:
            bucket = _bucket()
        except _UploadNotConfigured:
            return _not_configured_response()

        course_slug = lecture.section.course.slug
        unique_name = uuid.uuid4().hex
        storage_relative_key = f'courses/{course_slug}/lectures/{lecture.id}/raw/{unique_name}.{ext}'
        s3_key = f'{_aws_location_prefix()}{storage_relative_key}'

        # Create the row *inactive* — a concurrent initiate/complete on the
        # same lecture would otherwise hit uniq_active_videoasset_per_lecture.
        # It flips active on complete.
        video_asset = VideoAsset.objects.create(
            lecture=lecture,
            original_filename=filename,
            mime_type=content_type,
            file_size=file_size,
            is_active=False,
            status=VideoAsset.Status.UPLOADING,
        )

        try:
            resp = _s3_client().create_multipart_upload(
                Bucket=bucket,
                Key=s3_key,
                ContentType=content_type,
            )
        except Exception:
            logger.exception('S3 create_multipart_upload failed for lecture=%s', lecture.id)
            video_asset.status = VideoAsset.Status.FAILED
            video_asset.save(update_fields=['status', 'updated_at'])
            return Response(
                {'success': False, 'message': 'Could not initiate upload. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        part_size = int(getattr(settings, 'AWS_S3_MULTIPART_CHUNK_SIZE', 5 * 1024 * 1024))
        return Response(
            {
                'success': True,
                'message': 'Multipart upload initiated.',
                'data': {
                    'videoAssetId': video_asset.id,
                    'uploadId': resp['UploadId'],
                    'objectKey': storage_relative_key,
                    'partSize': part_size,
                    # S3 caps a multipart upload at 10 000 parts.
                    'maxParts': 10_000,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class LectureMultipartUploadPartUrlView(APIView):
    """POST → presigned PUT URL for one part of an in-flight upload."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]

    def post(self, request, video_asset_id: int):
        video_asset = _get_owned_video_asset(request.user, video_asset_id)
        if video_asset is None:
            return _video_asset_not_found()

        upload_id = (request.data.get('uploadId') or '').strip()
        object_key = (request.data.get('objectKey') or '').strip()
        try:
            part_number = int(request.data.get('partNumber') or 0)
        except (TypeError, ValueError):
            part_number = 0

        errors = {}
        if not upload_id:
            errors['uploadId'] = 'This field is required.'
        if not object_key:
            errors['objectKey'] = 'This field is required.'
        if not (1 <= part_number <= 10_000):
            errors['partNumber'] = 'Must be between 1 and 10000.'
        if errors:
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            bucket = _bucket()
        except _UploadNotConfigured:
            return _not_configured_response()

        try:
            presigned_url = _s3_client().generate_presigned_url(
                ClientMethod='upload_part',
                Params={
                    'Bucket': bucket,
                    'Key': f'{_aws_location_prefix()}{object_key}',
                    'UploadId': upload_id,
                    'PartNumber': part_number,
                },
                ExpiresIn=int(getattr(settings, 'AWS_S3_PRESIGNED_PART_URL_TTL_SECONDS', 3600)),
            )
        except Exception:
            logger.exception('S3 generate_presigned_url failed for asset=%s part=%s', video_asset_id, part_number)
            return Response(
                {'success': False, 'message': 'Could not generate part URL. Please retry.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Presigned part URL generated.',
                'data': {'presignedUrl': presigned_url, 'partNumber': part_number},
            },
            status=status.HTTP_200_OK,
        )


class LectureMultipartUploadCompleteView(APIView):
    """POST → finalises the S3 upload, activates the asset, enqueues transcode."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]

    def post(self, request, video_asset_id: int):
        video_asset = _get_owned_video_asset(request.user, video_asset_id)
        if video_asset is None:
            return _video_asset_not_found()

        upload_id = (request.data.get('uploadId') or '').strip()
        object_key = (request.data.get('objectKey') or '').strip()
        parts = request.data.get('parts') or []

        errors = {}
        if not upload_id:
            errors['uploadId'] = 'This field is required.'
        if not object_key:
            errors['objectKey'] = 'This field is required.'
        if not isinstance(parts, list) or not parts:
            errors['parts'] = 'Provide the list of uploaded parts with their ETags.'
        if errors:
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            formatted_parts = [
                {'PartNumber': int(p['partNumber']), 'ETag': str(p['etag'])}
                for p in parts
            ]
        except (KeyError, TypeError, ValueError):
            return Response(
                {
                    'success': False,
                    'message': 'Validation failed.',
                    'errors': {'parts': 'Each part must be {partNumber:int, etag:str}.'},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        formatted_parts.sort(key=lambda p: p['PartNumber'])

        try:
            bucket = _bucket()
        except _UploadNotConfigured:
            return _not_configured_response()

        try:
            _s3_client().complete_multipart_upload(
                Bucket=bucket,
                Key=f'{_aws_location_prefix()}{object_key}',
                UploadId=upload_id,
                MultipartUpload={'Parts': formatted_parts},
            )
        except Exception:
            logger.exception('S3 complete_multipart_upload failed for asset=%s', video_asset_id)
            video_asset.status = VideoAsset.Status.FAILED
            video_asset.save(update_fields=['status', 'updated_at'])
            return Response(
                {'success': False, 'message': 'Could not finalise upload. Please retry.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        with transaction.atomic():
            # Point the FileField at the raw upload so the transcoder can
            # open it via default_storage. Storage prepends AWS_LOCATION
            # itself, so we store the storage-relative key.
            video_asset.video_file.name = object_key
            # Deactivate any prior active asset for this lecture, then
            # activate this one. Ordering matters — the uniq constraint is
            # filtered on is_active=True.
            VideoAsset.objects.filter(
                lecture=video_asset.lecture, is_active=True
            ).exclude(pk=video_asset.pk).update(is_active=False)
            video_asset.is_active = True
            video_asset.status = VideoAsset.Status.PROCESSING
            video_asset.save(update_fields=['video_file', 'is_active', 'status', 'updated_at'])

            lecture = video_asset.lecture
            lecture.stream_master_playlist = ''
            lecture.stream_renditions = []
            lecture.transcoding_error = ''
            lecture.save(update_fields=['stream_master_playlist', 'stream_renditions', 'transcoding_error', 'updated_at'])

            job = VideoProcessingJob.objects.create(
                video_asset=video_asset,
                status=VideoProcessingJob.Status.PENDING,
                notes='Video uploaded and queued for transcoding.',
            )

        # Enqueue after commit so a rolled-back transaction can't leak a
        # phantom Celery task.
        _video_asset_id = video_asset.id
        _job_id = job.id

        def _enqueue():
            from courses.tasks import transcode_video_asset_task
            transcode_video_asset_task.delay(_video_asset_id, _job_id)

        transaction.on_commit(_enqueue)

        return Response(
            {
                'success': True,
                'message': 'Upload complete. Transcoding queued.',
                'data': {'videoAssetId': video_asset.id, 'jobId': job.id},
            },
            status=status.HTTP_200_OK,
        )


class LectureMultipartUploadAbortView(APIView):
    """POST → cancels an in-flight upload and marks the asset failed."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]

    def post(self, request, video_asset_id: int):
        video_asset = _get_owned_video_asset(request.user, video_asset_id)
        if video_asset is None:
            return _video_asset_not_found()

        upload_id = (request.data.get('uploadId') or '').strip()
        object_key = (request.data.get('objectKey') or '').strip()

        errors = {}
        if not upload_id:
            errors['uploadId'] = 'This field is required.'
        if not object_key:
            errors['objectKey'] = 'This field is required.'
        if errors:
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            bucket = _bucket()
        except _UploadNotConfigured:
            return _not_configured_response()

        try:
            _s3_client().abort_multipart_upload(
                Bucket=bucket,
                Key=f'{_aws_location_prefix()}{object_key}',
                UploadId=upload_id,
            )
        except Exception:
            # Even if S3 rejects the abort (already gone, wrong key), we
            # still want the DB row marked failed — S3's own lifecycle rule
            # will garbage-collect the dangling parts on its timeline.
            logger.exception('S3 abort_multipart_upload failed for asset=%s', video_asset_id)

        video_asset.status = VideoAsset.Status.FAILED
        video_asset.save(update_fields=['status', 'updated_at'])

        return Response(
            {'success': True, 'message': 'Upload aborted.'},
            status=status.HTTP_200_OK,
        )


class LectureStreamUrlView(APIView):
    """GET → CloudFront-signed HLS playback URL + Set-Cookie headers.

    Signed cookies (not signed URLs) — the browser attaches the cookies to
    every ``.m3u8`` / ``.ts`` fetch under the same host+path, so all HLS
    segments are authorized in one shot. The client must fetch this
    endpoint with ``credentials: 'include'`` and use the same for HLS
    playback (hls.js: ``xhrSetup: xhr => xhr.withCredentials = true``).

    Access mirrors ``LearnerLectureDetailView``: course instructors always
    pass, learners need an active enrollment or the lecture must be a
    preview. Numeric ID → 404 on no-access. Drip release (cohort start
    date / section ``unlocks_at``) → 422 with a timing message.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]
    throttle_classes = [VideoStreamUrlThrottle]

    def get(self, request, lecture_id: int):
        lecture = (
            Lecture.objects
            .select_related('section__course')
            .filter(pk=lecture_id, lecture_type=Lecture.LectureType.VIDEO)
            .first()
        )
        if lecture is None:
            return Response(
                {'success': False, 'message': 'Lecture not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Access + drip-release check first, so a locked section returns 422
        # even when CloudFront IS configured (the signer would happily sign
        # a URL for content the caller isn't allowed to play yet).
        from courses.services.learner_service import (
            ContentNotReleasedError,
            assert_content_released,
            resolve_course_access,
        )

        is_instructor, enrollment = resolve_course_access(request.user, lecture.section.course)
        if not is_instructor and enrollment is None and not lecture.is_preview:
            return Response(
                {'success': False, 'message': 'Lecture not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Instructor preview bypasses drip release; learners don't.
        if not is_instructor:
            try:
                assert_content_released(enrollment, lecture.section)
            except ContentNotReleasedError as exc:
                return Response(
                    {'success': False, 'message': exc.message},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

        stream = lecture.get_stream_context(request.user)
        if stream:
            response = Response(
                {
                    'success': True,
                    'message': 'Stream URL generated.',
                    'data': {'streamUrl': stream['playback_url']},
                },
                status=status.HTTP_200_OK,
            )
            ttl_seconds = int(getattr(settings, 'CLOUDFRONT_SIGNED_URL_TTL_SECONDS', 7200))
            for name, value in stream['cookies'].items():
                # HttpOnly so JS can't read them (blocks XSS exfiltration).
                # SameSite=None + Secure because the API and CloudFront live
                # on different subdomains and cross-site cookies need both.
                response.set_cookie(
                    name,
                    value,
                    max_age=ttl_seconds,
                    path=stream['cookie_path'] or '/',
                    domain=stream['cookie_domain'],
                    secure=True,
                    httponly=True,
                    samesite='None',
                )
            return response

        # No signed context — CloudFront isn't configured. Fall back to the
        # storage-relative URL so local dev plays without S3/CloudFront.
        if lecture.stream_master_playlist:
            from django.core.files.storage import default_storage
            fallback = default_storage.url(lecture.stream_master_playlist)
            return Response(
                {'success': True, 'message': 'Stream URL generated.', 'data': {'streamUrl': fallback}},
                status=status.HTTP_200_OK,
            )

        return Response(
            {'success': False, 'message': 'Video is not ready yet.'},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

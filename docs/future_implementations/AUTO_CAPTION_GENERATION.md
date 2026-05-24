# Automatic Caption Generation with Self-Hosted OpenAI Whisper

> **Status:** Future implementation  
> **Author:** Auto-generated design document  
> **Date:** 2026-05-23  
> **Depends on:** Video transcoding pipeline (`courses/transcoding.py`, `courses/tasks.py`)

---

## Table of Contents

1. [Overview](#1-overview)
2. [Why Self-Hosted Whisper](#2-why-self-hosted-whisper)
3. [Architecture Overview](#3-architecture-overview)
4. [Model Changes](#4-model-changes)
5. [Service Changes](#5-service-changes)
6. [Task Changes (Celery)](#6-task-changes-celery)
7. [Serializer Changes](#7-serializer-changes)
8. [View Changes](#8-view-changes)
9. [URL Changes](#9-url-changes)
10. [Transcoding Pipeline Integration](#10-transcoding-pipeline-integration)
11. [Hosting Whisper on the Django Server](#11-hosting-whisper-on-the-django-server)
12. [Environment Variables](#12-environment-variables)
13. [Dependencies](#13-dependencies)
14. [Migration Plan](#14-migration-plan)
15. [Manual Upload Fallback](#15-manual-upload-fallback)

---

## 1. Overview

This document describes how to add automatic caption/subtitle generation to the video lecture pipeline using the open-source OpenAI Whisper model, self-hosted on our own infrastructure. Captions will be generated as WebVTT (`.vtt`) files after video transcoding completes, stored alongside HLS output, and served to the learner's video player as a text track.

The system also supports manual caption upload (`.srt` / `.vtt`) as a fallback or override.

### Caption Flow Summary

```
Instructor uploads video
    → VideoAsset created (status: uploading)
    → transcode_video_asset_task runs FFmpeg → HLS output (status: ready)
    → generate_captions_task runs:
        1. Extract audio from raw video (FFmpeg → WAV)
        2. Run Whisper model on extracted audio
        3. Convert Whisper output → WebVTT (.vtt)
        4. Save .vtt file to media directory
        5. Update CaptionTrack record (status: ready)
    → Learner player receives captions_url in lecture detail response
```

---

## 2. Why Self-Hosted Whisper

| Factor | Self-Hosted Whisper | Whisper API (OpenAI) | Cloud STT (AWS/GCP) |
|--------|--------------------|-----------------------|----------------------|
| **Cost per minute** | ~$0 (infra only) | $0.006 | $0.006–$0.024 |
| **Cost at 10,000 hours** | Fixed server cost | ~$3,600 | $3,600–$14,400 |
| **Data privacy** | Audio never leaves your server | Sent to OpenAI | Sent to cloud provider |
| **Latency** | Depends on GPU | Network + processing | Network + processing |
| **Language support** | 97 languages | 57 languages | Varies |
| **Offline capable** | Yes | No | No |
| **Model control** | Full (swap models, fine-tune) | None | Limited |

**Recommendation:** Self-hosted Whisper is the best fit for a course platform because video content grows linearly with instructors, and per-minute API pricing becomes expensive at scale. Data stays on our servers, and we can swap between Whisper model sizes (tiny → large) based on the quality/speed tradeoff we want.

### Whisper Model Sizes

| Model | Parameters | English-only | Multilingual | Relative Speed | VRAM Required |
|-------|-----------|--------------|--------------|----------------|---------------|
| `tiny` | 39M | ✓ | ✓ | ~32x | ~1 GB |
| `base` | 74M | ✓ | ✓ | ~16x | ~1 GB |
| `small` | 244M | ✓ | ✓ | ~6x | ~2 GB |
| `medium` | 769M | ✓ | ✓ | ~2x | ~5 GB |
| `large-v3` | 1.55B | — | ✓ | 1x | ~10 GB |

For course content (clear speech, minimal background noise), the `small` or `medium` model offers the best accuracy-to-speed ratio. `large-v3` is recommended only if multilingual accuracy is critical.

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    Django Backend                         │
│                                                          │
│  ┌─────────────┐    ┌──────────────────┐                │
│  │ Lecture View │───▶│ CaptionTrack     │                │
│  │ (upload .vtt)│    │ Model            │                │
│  └─────────────┘    └────────┬─────────┘                │
│                              │                           │
│  ┌─────────────────┐   ┌────▼────────────────┐          │
│  │ transcode_video  │──▶│ generate_captions   │          │
│  │ _asset_task      │   │ _task               │          │
│  │ (Celery)         │   │ (Celery)            │          │
│  └─────────────────┘   └────┬────────────────┘          │
│                              │                           │
│                         ┌────▼────────────────┐          │
│                         │ caption_service.py   │          │
│                         │                      │          │
│                         │ 1. extract_audio()   │          │
│                         │    (FFmpeg → WAV)    │          │
│                         │                      │          │
│                         │ 2. transcribe()      │          │
│                         │    (Whisper model)   │          │
│                         │                      │          │
│                         │ 3. to_webvtt()       │          │
│                         │    (segments → .vtt) │          │
│                         └─────────────────────┘          │
│                                                          │
│  Output: media/courses/{slug}/lectures/{id}/             │
│          hls/{video_asset_id}/captions_{lang}.vtt        │
└──────────────────────────────────────────────────────────┘
```

---

## 4. Model Changes

### New Model: `CaptionTrack`

**File:** `courses/all_models/content_models.py`

```python
def caption_upload_path(instance, filename):
    """Manual uploads go to a dedicated captions directory."""
    lecture = instance.video_asset.lecture
    course_slug = lecture.section.course.slug
    ext = os.path.splitext(filename)[1]
    unique = uuid.uuid4().hex[:12]
    return (
        f"courses/{course_slug}/lectures/{lecture.id}/"
        f"captions/{instance.video_asset.id}/{unique}{ext}"
    )


class CaptionTrack(TimestampedModel):
    """
    A single caption/subtitle track for a VideoAsset.
    Supports both auto-generated (Whisper) and manually uploaded captions.
    """

    class Status(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        PROCESSING = 'processing', 'Processing'
        READY      = 'ready',      'Ready'
        FAILED     = 'failed',     'Failed'

    class Source(models.TextChoices):
        AUTO_GENERATED = 'auto_generated', 'Auto Generated'
        MANUAL_UPLOAD  = 'manual_upload',  'Manual Upload'

    video_asset = models.ForeignKey(
        'VideoAsset',
        on_delete=models.CASCADE,
        related_name='caption_tracks',
    )
    language = models.CharField(
        max_length=10,
        default='en',
        help_text="BCP-47 language tag, e.g. 'en', 'bn', 'es'",
    )
    label = models.CharField(
        max_length=100,
        default='English',
        help_text="Human-readable label shown in the player UI",
    )
    vtt_file = models.FileField(
        upload_to=caption_upload_path,
        blank=True,
        default='',
        help_text="WebVTT file (manual uploads). Auto-generated captions "
                  "use vtt_path instead.",
    )
    vtt_path = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text="MEDIA_ROOT-relative path to the .vtt file "
                  "(auto-generated captions, like master_playlist on VideoAsset)",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.AUTO_GENERATED,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'caption_tracks'
        constraints = [
            models.UniqueConstraint(
                fields=['video_asset', 'language', 'source'],
                name='uniq_caption_per_asset_lang_source',
            ),
        ]
        ordering = ['language']

    def __str__(self):
        return f"CaptionTrack({self.language}, {self.source}, {self.status})"

    @property
    def effective_vtt_path(self):
        """Return whichever path field is populated."""
        if self.vtt_file and self.vtt_file.name:
            return self.vtt_file.name
        return self.vtt_path
```

### Existing Model Additions

**`VideoAsset`** — add a convenience property (no schema change needed):

```python
# On VideoAsset class
@property
def active_caption_tracks(self):
    return self.caption_tracks.filter(status=CaptionTrack.Status.READY)
```

**`Lecture`** — add a denormalized field for fast reads (mirrors the `stream_master_playlist` pattern):

```python
# On Lecture class — optional denormalization
caption_tracks_data = models.JSONField(
    default=list,
    blank=True,
    help_text="Denormalized list of ready caption tracks: "
              "[{'language': 'en', 'label': 'English', 'vtt_url': '...'}]",
)
```

> **Note:** The denormalized field is optional. The alternative is to join through `VideoAsset` at query time. Given that the learner lecture detail view already fetches the active `VideoAsset`, a prefetch of `caption_tracks` is lightweight and may be preferable to maintaining denormalized JSON.

---

## 5. Service Changes

### New Service: `courses/services/caption_service.py`

```python
"""
Caption generation and management service.

Public API:
    extract_audio(video_asset: VideoAsset) -> Path
    transcribe_audio(audio_path: Path, language: str, model_size: str) -> list[dict]
    segments_to_webvtt(segments: list[dict]) -> str
    generate_caption_file(video_asset: VideoAsset, language: str) -> str
    validate_and_convert_upload(uploaded_file, video_asset, language, label) -> CaptionTrack
"""

import os
import subprocess
import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audio extraction (FFmpeg)
# ---------------------------------------------------------------------------

def extract_audio(video_asset) -> Path:
    """
    Extract audio from the raw video file as a 16kHz mono WAV
    (Whisper's expected input format).

    Returns the absolute path to the extracted .wav file.
    """
    input_path = video_asset.video_file.path
    output_dir = Path(settings.MEDIA_ROOT) / _caption_output_dir(video_asset)
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / 'audio_for_caption.wav'

    cmd = [
        settings.FFMPEG_BINARY_PATH,
        '-i', str(input_path),
        '-vn',                        # no video
        '-acodec', 'pcm_s16le',       # 16-bit PCM
        '-ar', '16000',               # 16 kHz (Whisper default)
        '-ac', '1',                   # mono
        '-y',                         # overwrite
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr[:500]}")

    return audio_path


# ---------------------------------------------------------------------------
# Whisper transcription
# ---------------------------------------------------------------------------

def transcribe_audio(audio_path: Path, language: str = 'en',
                     model_size: str = None) -> list[dict]:
    """
    Run Whisper on the extracted audio and return timestamped segments.

    Each segment: {'start': float, 'end': float, 'text': str}

    Uses faster-whisper (CTranslate2 backend) for 4x speed improvement
    over the original OpenAI Whisper implementation.
    """
    from faster_whisper import WhisperModel

    model_size = model_size or getattr(settings, 'WHISPER_MODEL_SIZE', 'small')
    device = getattr(settings, 'WHISPER_DEVICE', 'cpu')
    compute_type = getattr(settings, 'WHISPER_COMPUTE_TYPE', 'int8')

    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    segments_gen, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        word_timestamps=False,
        vad_filter=True,          # skip silence — faster processing
    )

    segments = []
    for seg in segments_gen:
        segments.append({
            'start': seg.start,
            'end': seg.end,
            'text': seg.text.strip(),
        })

    logger.info(
        f"Whisper transcription complete: {len(segments)} segments, "
        f"language={info.language}, probability={info.language_probability:.2f}"
    )
    return segments


# ---------------------------------------------------------------------------
# WebVTT generation
# ---------------------------------------------------------------------------

def segments_to_webvtt(segments: list[dict]) -> str:
    """Convert Whisper segments to a WebVTT formatted string."""
    lines = ['WEBVTT', '']
    for i, seg in enumerate(segments, start=1):
        start = _format_timestamp(seg['start'])
        end = _format_timestamp(seg['end'])
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(seg['text'])
        lines.append('')
    return '\n'.join(lines)


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to WebVTT timestamp: HH:MM:SS.mmm"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"


# ---------------------------------------------------------------------------
# End-to-end caption generation
# ---------------------------------------------------------------------------

def generate_caption_file(video_asset, language: str = 'en') -> str:
    """
    Full pipeline: extract audio → transcribe → write .vtt file.

    Returns the MEDIA_ROOT-relative path to the generated .vtt file.
    """
    audio_path = extract_audio(video_asset)

    try:
        segments = transcribe_audio(audio_path, language=language)
    finally:
        # Clean up the temporary audio file
        if audio_path.exists():
            audio_path.unlink()

    vtt_content = segments_to_webvtt(segments)

    output_dir = Path(settings.MEDIA_ROOT) / _caption_output_dir(video_asset)
    output_dir.mkdir(parents=True, exist_ok=True)
    vtt_filename = f"captions_{language}.vtt"
    vtt_absolute = output_dir / vtt_filename

    vtt_absolute.write_text(vtt_content, encoding='utf-8')

    # Return MEDIA_ROOT-relative path (same convention as master_playlist)
    relative = str(vtt_absolute.relative_to(settings.MEDIA_ROOT))
    return relative.replace('\\', '/')


# ---------------------------------------------------------------------------
# Manual upload validation
# ---------------------------------------------------------------------------

def validate_and_save_upload(uploaded_file, video_asset, language, label) -> 'CaptionTrack':
    """
    Validate an uploaded .srt or .vtt file, convert .srt → .vtt if needed,
    and create a CaptionTrack record.
    """
    from courses.models import CaptionTrack

    filename = uploaded_file.name.lower()
    if not filename.endswith(('.srt', '.vtt')):
        raise ValueError("Only .srt and .vtt files are accepted.")

    content = uploaded_file.read().decode('utf-8', errors='replace')

    if filename.endswith('.srt'):
        content = _srt_to_vtt(content)

    # Basic validation: must contain timestamps
    if '-->' not in content:
        raise ValueError("File does not appear to contain valid caption timestamps.")

    track, created = CaptionTrack.objects.update_or_create(
        video_asset=video_asset,
        language=language,
        source=CaptionTrack.Source.MANUAL_UPLOAD,
        defaults={
            'label': label,
            'status': CaptionTrack.Status.READY,
            'error_message': '',
        },
    )

    # Save the validated .vtt content
    from django.core.files.base import ContentFile
    vtt_filename = f"captions_{language}.vtt"
    track.vtt_file.save(vtt_filename, ContentFile(content.encode('utf-8')), save=True)

    return track


def _srt_to_vtt(srt_content: str) -> str:
    """
    Convert SRT format to WebVTT.
    Main differences: header line, comma → dot in timestamps.
    """
    vtt = 'WEBVTT\n\n'
    # Replace SRT timestamp separator (comma) with VTT separator (dot)
    converted = srt_content.replace(',', '.')
    vtt += converted
    return vtt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _caption_output_dir(video_asset) -> str:
    """
    Build the output directory for caption files, co-located with HLS output.
    Pattern: courses/{slug}/lectures/{id}/hls/{video_asset_id}/
    """
    lecture = video_asset.lecture
    course_slug = lecture.section.course.slug
    return f"courses/{course_slug}/lectures/{lecture.id}/hls/{video_asset.id}"
```

### Existing Service Updates

**`courses/services/__init__.py`** — add re-exports:

```python
from .caption_service import (
    generate_caption_file,
    validate_and_save_upload,
)
```

---

## 6. Task Changes (Celery)

### New Task: `generate_captions_task`

**File:** `courses/tasks.py`

```python
@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def generate_captions_task(self, video_asset_id: int, caption_track_id: int):
    """
    Generate captions for a video asset using Whisper.

    Chained after transcode_video_asset_task completes successfully.
    Follows the same status-transition pattern as the transcoding task.
    """
    from courses.models import VideoAsset, CaptionTrack
    from courses.services.caption_service import generate_caption_file

    try:
        caption_track = CaptionTrack.objects.select_related(
            'video_asset__lecture__section__course'
        ).get(pk=caption_track_id)
    except CaptionTrack.DoesNotExist:
        logger.error(f"CaptionTrack {caption_track_id} not found, aborting.")
        return

    # Terminal guard (same pattern as grade_assignment_submission_task)
    if caption_track.status in (CaptionTrack.Status.READY, CaptionTrack.Status.FAILED):
        logger.info(f"CaptionTrack {caption_track_id} already terminal, skipping.")
        return

    caption_track.status = CaptionTrack.Status.PROCESSING
    caption_track.save(update_fields=['status', 'updated_at'])

    try:
        vtt_relative_path = generate_caption_file(
            caption_track.video_asset,
            language=caption_track.language,
        )

        caption_track.vtt_path = vtt_relative_path
        caption_track.status = CaptionTrack.Status.READY
        caption_track.error_message = ''
        caption_track.save(update_fields=[
            'vtt_path', 'status', 'error_message', 'updated_at',
        ])

        logger.info(
            f"Captions generated for VideoAsset {video_asset_id}: "
            f"{vtt_relative_path}"
        )

    except Exception as exc:
        logger.error(
            f"Caption generation failed for VideoAsset {video_asset_id}: {exc}"
        )

        if self.request.retries >= self.max_retries:
            caption_track.status = CaptionTrack.Status.FAILED
            caption_track.error_message = str(exc)[:500]
            caption_track.save(update_fields=[
                'status', 'error_message', 'updated_at',
            ])

        raise  # Let Celery handle the retry
```

### Modify Existing Task: `transcode_video_asset_task`

Add caption task dispatch after successful transcoding via `transaction.on_commit` (same pattern used by `submit_assignment`):

```python
# Inside transcode_video_asset_task, after setting VideoAsset.status = READY:

from django.db import transaction

if getattr(settings, 'AUTO_GENERATE_CAPTIONS', True):
    caption_language = getattr(settings, 'WHISPER_DEFAULT_LANGUAGE', 'en')
    caption_track = CaptionTrack.objects.create(
        video_asset=video_asset,
        language=caption_language,
        label=settings.WHISPER_DEFAULT_LABEL or 'English',
        source=CaptionTrack.Source.AUTO_GENERATED,
        status=CaptionTrack.Status.PENDING,
    )
    transaction.on_commit(
        lambda: generate_captions_task.delay(
            video_asset.id, caption_track.id
        )
    )
```

---

## 7. Serializer Changes

### Learner Serializer Additions

**File:** `courses/all_serializers/learner_serializers.py`

Add a new serializer method field to `LearnerLectureDetailSerializer`:

```python
class LearnerLectureDetailSerializer(serializers.Serializer):
    # ... existing fields ...
    id = serializers.IntegerField(read_only=True)
    section_id = serializers.IntegerField(source='section.id', read_only=True)
    title = serializers.CharField(read_only=True)
    lecture_type = serializers.CharField(read_only=True)
    article_content = serializers.CharField(read_only=True)
    stream_master_playlist = serializers.SerializerMethodField()
    stream_renditions = serializers.SerializerMethodField()
    duration_seconds = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    # NEW FIELD
    caption_tracks = serializers.SerializerMethodField()

    def get_caption_tracks(self, lecture):
        """
        Return ready caption tracks for the active video asset.
        Passed via context from the view to avoid N+1 queries.
        """
        tracks = self.context.get('caption_tracks', [])
        return [
            {
                'language': t.language,
                'label': t.label,
                'vtt_url': _normalize_media_relative_path(t.effective_vtt_path),
            }
            for t in tracks
        ]
```

### Instructor Serializer Additions

**File:** `courses/all_serializers/content_serializers.py`

```python
class CaptionTrackSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    language = serializers.CharField(max_length=10)
    label = serializers.CharField(max_length=100)
    source = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    vtt_url = serializers.SerializerMethodField()
    error_message = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    def get_vtt_url(self, track):
        return _normalize_media_relative_path(track.effective_vtt_path)


class CaptionUploadSerializer(serializers.Serializer):
    """Validates manual caption file uploads."""
    caption_file = serializers.FileField()
    language = serializers.CharField(max_length=10, default='en')
    label = serializers.CharField(max_length=100, default='English')

    def validate_caption_file(self, value):
        filename = value.name.lower()
        if not filename.endswith(('.srt', '.vtt')):
            raise serializers.ValidationError(
                "Only .srt and .vtt files are accepted."
            )
        # 5 MB limit for caption files
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError(
                "Caption file must be under 5 MB."
            )
        return value
```

---

## 8. View Changes

### New Instructor View: Caption Upload

**File:** `courses/all_views/content_views.py`

```python
class LectureCaptionUploadView(APIView):
    """
    POST /api/v1/courses/lectures/<lecture_id>/captions/upload/

    Allows an instructor to manually upload a .srt or .vtt caption file
    for a lecture's active video asset.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]

    def post(self, request, lecture_id):
        try:
            lecture = Lecture.objects.select_related(
                'section__course'
            ).get(pk=lecture_id, section__course__instructors=request.user)
        except Lecture.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Lecture not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        course = lecture.section.course
        guard_editable(course)

        video_asset = lecture.video_assets.filter(
            is_active=True, status=VideoAsset.Status.READY
        ).first()
        if not video_asset:
            return Response(
                {'success': False, 'message': 'No ready video asset found for this lecture.'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        serializer = CaptionUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            track = validate_and_save_upload(
                uploaded_file=serializer.validated_data['caption_file'],
                video_asset=video_asset,
                language=serializer.validated_data['language'],
                label=serializer.validated_data['label'],
            )
        except ValueError as e:
            return Response(
                {'success': False, 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"Caption upload failed for lecture {lecture_id}: {e}")
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Caption uploaded successfully.',
                'data': CaptionTrackSerializer(track).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LectureCaptionListView(APIView):
    """
    GET /api/v1/courses/lectures/<lecture_id>/captions/

    List all caption tracks for a lecture's active video asset.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]

    def get(self, request, lecture_id):
        try:
            lecture = Lecture.objects.select_related(
                'section__course'
            ).get(pk=lecture_id, section__course__instructors=request.user)
        except Lecture.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Lecture not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        video_asset = lecture.video_assets.filter(is_active=True).first()
        if not video_asset:
            return Response(
                {'success': True, 'data': []},
                status=status.HTTP_200_OK,
            )

        tracks = video_asset.caption_tracks.all()
        return Response(
            {
                'success': True,
                'data': CaptionTrackSerializer(tracks, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class LectureCaptionDeleteView(APIView):
    """
    DELETE /api/v1/courses/lectures/<lecture_id>/captions/<caption_id>/

    Delete a specific caption track.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]

    def delete(self, request, lecture_id, caption_id):
        try:
            lecture = Lecture.objects.select_related(
                'section__course'
            ).get(pk=lecture_id, section__course__instructors=request.user)
        except Lecture.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Lecture not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        course = lecture.section.course
        guard_editable(course)

        try:
            track = CaptionTrack.objects.get(
                pk=caption_id,
                video_asset__lecture=lecture,
            )
        except CaptionTrack.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Caption track not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        track.delete()
        return Response(
            {'success': True, 'message': 'Caption track deleted.'},
            status=status.HTTP_200_OK,
        )
```

### Learner View Modification

**File:** `courses/all_views/learner_views.py`

Update `LearnerLectureDetailView.get()` to pass caption tracks via context:

```python
def get(self, request, lecture_id):
    try:
        lecture, course, is_instructor, watch_progress = (
            get_consumption_lecture(request.user, lecture_id)
        )
    except Lecture.DoesNotExist:
        return Response(
            {'success': False, 'message': 'Lecture not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    active_asset = lecture.video_assets.filter(
        is_active=True,
    ).order_by('-created_at').first()

    duration_seconds = None
    caption_tracks = []

    if active_asset:
        duration_seconds = active_asset.duration_seconds
        # Prefetch ready caption tracks
        caption_tracks = list(
            active_asset.caption_tracks.filter(
                status=CaptionTrack.Status.READY
            )
        )

    serializer = LearnerLectureDetailSerializer(
        lecture,
        context={
            'duration_seconds': duration_seconds,
            'watch_progress': watch_progress,
            'caption_tracks': caption_tracks,   # NEW
        },
    )

    return Response(
        {'success': True, 'data': serializer.data},
        status=status.HTTP_200_OK,
    )
```

---

## 9. URL Changes

**File:** `courses/urls.py`

```python
# Instructor caption management (numeric ID → 404 on no-access)
path(
    'lectures/<int:lecture_id>/captions/',
    LectureCaptionListView.as_view(),
    name='lecture-caption-list',
),
path(
    'lectures/<int:lecture_id>/captions/upload/',
    LectureCaptionUploadView.as_view(),
    name='lecture-caption-upload',
),
path(
    'lectures/<int:lecture_id>/captions/<int:caption_id>/',
    LectureCaptionDeleteView.as_view(),
    name='lecture-caption-delete',
),
```

No new learner URL is needed — caption data is served inline on the existing `GET /learn/lectures/<int:lecture_id>/` response.

---

## 10. Transcoding Pipeline Integration

The caption task chains naturally after the existing transcoding task. Here is the sequence:

```
1. Instructor uploads video via POST /lectures/<id>/video/
2. replace_lecture_video_and_enqueue_transcoding() in section_service.py:
   a. Creates VideoAsset (status: uploading)
   b. Creates VideoProcessingJob (status: pending)
   c. Dispatches transcode_video_asset_task.delay(video_asset.id, job.id)

3. transcode_video_asset_task runs:
   a. FFmpeg → 5 HLS renditions
   b. VideoAsset.status → ready
   c. IF AUTO_GENERATE_CAPTIONS is True:
      - Creates CaptionTrack (status: pending)
      - transaction.on_commit → generate_captions_task.delay(...)

4. generate_captions_task runs:
   a. FFmpeg extracts audio → 16kHz mono WAV
   b. Whisper model transcribes audio → segments
   c. Segments → WebVTT string → .vtt file
   d. CaptionTrack.status → ready
   e. Cleans up temporary audio file
```

**Key design decisions:**

- Caption generation is a **separate task**, not part of the transcoding task. This means a caption failure never blocks the video from being usable.
- The caption task uses `transaction.on_commit` dispatch (same pattern as `grade_assignment_submission_task`) so a rolled-back transaction cannot leak a phantom Celery task.
- The raw video file is used for audio extraction (not the HLS segments), because it is higher quality and avoids segment-boundary artifacts.

---

## 11. Hosting Whisper on the Django Server

### Can Whisper Run on the Same Server as Django?

**Yes, but with important caveats.** Here is an honest assessment:

### Option A: Same Server, Celery Worker (Simplest)

```
┌─────────────────────────────────────────┐
│           Single Server                  │
│                                          │
│  ┌──────────────┐  ┌──────────────────┐ │
│  │ Django/Gunicorn│  │ Celery Worker    │ │
│  │ (web requests) │  │ (transcoding +  │ │
│  │                │  │  Whisper)        │ │
│  └──────────────┘  └──────────────────┘ │
│                                          │
│  RAM: 8–16 GB     CPU: 4–8 cores        │
│  GPU: Optional (NVIDIA recommended)      │
└─────────────────────────────────────────┘
```

**How it works:** Whisper loads inside the Celery worker process. The `faster-whisper` library (CTranslate2 backend) loads the model into memory when the first caption task runs, and keeps it loaded for subsequent tasks (warm model). This is exactly how FFmpeg already runs in the worker — the caption task is just another subprocess-like operation.

**Pros:**
- Zero additional infrastructure
- No network latency for model inference
- Simplest deployment — just `pip install faster-whisper` and it works
- Model files are cached locally after first download (~500 MB for `small`)

**Cons:**
- CPU-only inference is slow: a 1-hour lecture takes ~15–30 minutes with `small` model on a 4-core CPU
- Whisper loads ~1–2 GB of RAM (model + inference buffers) on top of Django + Celery
- While Whisper is processing, the Celery worker is blocked from other tasks unless you run multiple worker processes or use a dedicated queue

**Recommendation for this option:** Use a dedicated Celery queue for caption tasks so they don't block video transcoding or assignment grading:

```python
# settings.py
CELERY_TASK_ROUTES = {
    'courses.tasks.generate_captions_task': {'queue': 'captions'},
    'courses.tasks.transcode_video_asset_task': {'queue': 'transcoding'},
    'courses.tasks.grade_assignment_submission_task': {'queue': 'grading'},
}
```

```bash
# Run separate workers per queue
celery -A career_college_backend worker -Q transcoding -l info --concurrency=1
celery -A career_college_backend worker -Q captions -l info --concurrency=1
celery -A career_college_backend worker -Q grading -l info --concurrency=2
```

### Option B: Same Server with GPU (Recommended for Production)

If the server has an NVIDIA GPU (even a modest one like a T4 or RTX 3060):

```python
# settings.py
WHISPER_DEVICE = 'cuda'           # Use GPU
WHISPER_COMPUTE_TYPE = 'float16'  # Half-precision (fast, accurate)
WHISPER_MODEL_SIZE = 'medium'     # Can afford a larger model with GPU
```

**Speed comparison for a 1-hour lecture:**

| Setup | Model | Time |
|-------|-------|------|
| CPU (4 cores) | `small` | ~20–30 min |
| CPU (8 cores) | `small` | ~12–18 min |
| GPU (T4) | `small` | ~1–2 min |
| GPU (T4) | `medium` | ~3–5 min |
| GPU (RTX 3060) | `large-v3` | ~5–8 min |

GPU inference is 10–30x faster. A $0.50/hr cloud GPU instance can process an entire day's worth of uploads in minutes.

### Option C: Separate Whisper Microservice (Scale)

For platforms with high volume (100+ hours of video per day), run Whisper as a standalone service:

```
┌─────────────┐         ┌──────────────────┐
│ Django +     │  HTTP   │ Whisper Service   │
│ Celery       │────────▶│ (FastAPI + GPU)   │
│              │         │                   │
│ Sends audio  │         │ Returns segments  │
│ file path    │         │ as JSON           │
└─────────────┘         └──────────────────┘
```

This is overkill for most course platforms but becomes relevant if you need horizontal scaling of transcription independently from the web tier.

### Recommendation

**Start with Option A** (same server, CPU, dedicated Celery queue). It works with zero additional infrastructure and handles the early stage where you have dozens of videos, not thousands. When processing time becomes a bottleneck, **upgrade to Option B** by adding a GPU to the server or spinning up a GPU-equipped Celery worker. The code change is a single settings variable (`WHISPER_DEVICE = 'cuda'`).

### Memory and Storage Requirements

| Component | Disk | RAM |
|-----------|------|-----|
| `faster-whisper` package | ~50 MB | — |
| Whisper `small` model (auto-downloaded) | ~500 MB | ~1 GB loaded |
| Whisper `medium` model | ~1.5 GB | ~3 GB loaded |
| Whisper `large-v3` model | ~3 GB | ~6 GB loaded |
| Temporary audio file (per task) | ~100 MB / hour of video | — |
| Output `.vtt` file | ~10–50 KB / hour of video | — |

Model files are cached in `~/.cache/huggingface/hub/` after the first download. The Celery worker loads the model once and keeps it in memory across tasks.

---

## 12. Environment Variables

Add to `.env` and `settings.py`:

```python
# settings.py — Caption generation settings

# Master toggle — set to False to disable auto-generation entirely
AUTO_GENERATE_CAPTIONS = os.getenv('AUTO_GENERATE_CAPTIONS', 'True').lower() == 'true'

# Whisper model configuration
WHISPER_MODEL_SIZE = os.getenv('WHISPER_MODEL_SIZE', 'small')
WHISPER_DEVICE = os.getenv('WHISPER_DEVICE', 'cpu')          # 'cpu' or 'cuda'
WHISPER_COMPUTE_TYPE = os.getenv('WHISPER_COMPUTE_TYPE', 'int8')  # 'int8', 'float16', 'float32'
WHISPER_DEFAULT_LANGUAGE = os.getenv('WHISPER_DEFAULT_LANGUAGE', 'en')
WHISPER_DEFAULT_LABEL = os.getenv('WHISPER_DEFAULT_LABEL', 'English')
```

**`.env.example` additions:**

```env
# Caption Generation (Whisper)
AUTO_GENERATE_CAPTIONS=True
WHISPER_MODEL_SIZE=small          # tiny | base | small | medium | large-v3
WHISPER_DEVICE=cpu                # cpu | cuda
WHISPER_COMPUTE_TYPE=int8         # int8 (CPU) | float16 (GPU) | float32
WHISPER_DEFAULT_LANGUAGE=en
WHISPER_DEFAULT_LABEL=English
```

---

## 13. Dependencies

Add to `requirements.txt`:

```
faster-whisper>=1.0,<2.0          # CTranslate2-based Whisper (4x faster than openai-whisper)
```

**Why `faster-whisper` instead of `openai-whisper`:**

| | `openai-whisper` | `faster-whisper` |
|---|---|---|
| Backend | PyTorch | CTranslate2 |
| Speed | 1x | 4x faster |
| Memory | High | ~50% less |
| INT8 quantization | No | Yes (great for CPU) |
| GPU support | Yes | Yes |
| Install size | ~2 GB (PyTorch) | ~200 MB |
| API compatibility | Original | Near-identical |

`faster-whisper` is the standard choice for production Whisper deployments. It uses the same model weights but runs inference through CTranslate2, which is optimized for both CPU and GPU.

**Optional (GPU only):**

```
nvidia-cublas-cu12                # Required if WHISPER_DEVICE=cuda
nvidia-cudnn-cu12
```

---

## 14. Migration Plan

### Phase 1: Manual Upload Only (Smallest Lift)

1. Create `CaptionTrack` model and migration
2. Add `CaptionUploadSerializer` and `CaptionTrackSerializer`
3. Add instructor upload/list/delete views
4. Add `caption_tracks` field to `LearnerLectureDetailSerializer`
5. Update `LearnerLectureDetailView` to pass caption tracks via context
6. Add URL patterns
7. No new dependencies required

**Estimated effort:** 1–2 days

### Phase 2: Auto-Generation

1. Add `faster-whisper` to requirements
2. Create `caption_service.py` with audio extraction + Whisper + VTT generation
3. Create `generate_captions_task` in tasks.py
4. Modify `transcode_video_asset_task` to chain caption generation on success
5. Add environment variables for Whisper configuration
6. Configure dedicated Celery queue for caption tasks
7. Test with CPU first, then GPU if available

**Estimated effort:** 2–3 days

### Phase 3: Instructor Review UI (Frontend)

1. Caption status indicator in the course builder (pending → processing → ready / failed)
2. Edit/replace auto-generated captions
3. Preview captions synced with video in the builder
4. Support for multiple languages per lecture

**Estimated effort:** Frontend team, 3–5 days

---

## 15. Manual Upload Fallback

Even with auto-generation enabled, instructors should always be able to:

1. **Upload a manual caption** that overrides the auto-generated one for the same language (the `UniqueConstraint` on `(video_asset, language, source)` keeps both; the frontend can prioritize `manual_upload` over `auto_generated` for the same language).

2. **Delete an auto-generated caption** and upload a corrected version.

3. **Add captions in additional languages** that Whisper didn't generate (e.g., upload a Bengali `.srt` when Whisper only generated English).

The `source` field (`auto_generated` vs `manual_upload`) makes this distinction clear in the data model, and the learner serializer serves all `READY` tracks regardless of source — the video player shows them all as selectable subtitle tracks.

---

## Appendix: Learner API Response Shape (After Implementation)

```json
{
  "success": true,
  "data": {
    "id": 42,
    "section_id": 7,
    "title": "Introduction to Machine Learning",
    "lecture_type": "video",
    "article_content": "",
    "stream_master_playlist": "courses/ml-101/lectures/42/hls/15/master.m3u8",
    "stream_renditions": [
      {"name": "720p", "playlist": "courses/ml-101/lectures/42/hls/15/720p.m3u8"},
      {"name": "480p", "playlist": "courses/ml-101/lectures/42/hls/15/480p.m3u8"}
    ],
    "duration_seconds": 1847,
    "caption_tracks": [
      {
        "language": "en",
        "label": "English",
        "vtt_url": "courses/ml-101/lectures/42/hls/15/captions_en.vtt"
      },
      {
        "language": "bn",
        "label": "Bengali (Uploaded)",
        "vtt_url": "courses/ml-101/lectures/42/captions/15/a1b2c3d4e5f6.vtt"
      }
    ],
    "progress": {
      "watched_seconds": 523,
      "is_completed": false,
      "last_watched_at": "2026-05-22T14:30:00Z"
    }
  }
}
```

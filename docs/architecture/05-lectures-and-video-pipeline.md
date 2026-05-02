# 05) Lectures And Video Pipeline

## Key files

- `courses/models.py`: `Lecture`, `VideoAsset`, `VideoProcessingJob`, `WatchProgress`
- `courses/all_views/content_views.py`: lecture create/list/detail endpoints
- `courses/serializers.py`: lecture serializers
- `courses/services.py`: video replace + enqueue logic
- `courses/transcoding.py`: transcoding routines/helpers
- `courses/tasks.py`: async processing tasks

## Models and fields

## `Lecture`

- Relation: `section` (FK)
- Content:
  - `title`
  - `content_type` (`video|article`)
  - `article_content`
  - streaming fields (`stream_master_playlist`, `stream_renditions`, `transcoding_error`)
- Note: lecture has no `position`; order is owned by `SectionContent`.

## `VideoAsset`

- Relation: `lecture` (FK)
- File and media metadata:
  - `video_file`, `original_filename`, `mime_type`, `file_size`
  - `duration_seconds`
  - `master_playlist`, `renditions`
  - `is_active`
  - `status` (`uploading|processing|ready|failed`)
- Constraint: only one active asset per lecture.

## `VideoProcessingJob`

- Relation: `video_asset` (FK)
- Fields:
  - `status` (`pending|processing|completed|failed`)
  - `notes`, `started_at`, `completed_at`

## `WatchProgress`

- Relations: `user`, `lecture`
- Fields: `watched_seconds`, `is_completed`, `last_watched_at`
- Constraint: one row per `(user, lecture)`

## Process: creating a lecture

1. Instructor creates a lecture (direct lecture endpoint or unified contents endpoint).
2. `Lecture` row is created.
3. `SectionContent` row is created to place it in section order.

## Process: uploading/replacing video

1. New upload marks old active asset inactive.
2. New `VideoAsset` is created as active.
3. New `VideoProcessingJob` is created.
4. Async worker transcodes and updates status/renditions fields.

Main entry point:
- `replace_lecture_video_and_enqueue_transcoding(...)` in `courses/services.py`

## Workflow

1. Lecture is authored as `article` or `video`.
2. For video, upload creates/activates a `VideoAsset`.
3. Processing job is queued and handled asynchronously.
4. Renditions and playback metadata are persisted back to lecture/video rows.
5. Learner consumption updates `WatchProgress`.

## System Explanation (Why This Design)

- Async transcoding keeps authoring endpoints responsive.
- Asset/job split gives observability into processing lifecycle.
- Single active-asset rule prevents ambiguous playback sources.

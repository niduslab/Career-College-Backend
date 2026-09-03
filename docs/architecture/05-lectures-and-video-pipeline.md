# 05) Lectures And Video Pipeline

## Key files

| File | Purpose |
|------|---------|
| `courses/all_models/content_models.py` | `Lecture`, `VideoAsset`, `VideoProcessingJob`, `WatchProgress` |
| `courses/all_views/content_views.py` | Lecture create/list/detail endpoints |
| `courses/services/section_service.py` | `replace_lecture_video_and_enqueue_transcoding()` — video upload entry point |
| `courses/services/learner_service.py` | `upsert_watch_progress()` — idempotent progress upsert |
| `courses/transcoding.py` | `transcode_video_asset()` — FFmpeg HLS encoding |
| `courses/tasks.py` | `transcode_video_asset_task` — Celery async task |
| `courses/signals.py` | WatchProgress signals that trigger progress recalculation |

---

## Models

### `Lecture`

The content item inside a section. Has no `position` field — order is owned by `SectionContent`.

| Field | Type | Notes |
|-------|------|-------|
| `section` | FK → `CourseSection` | Parent section |
| `title` | CharField(255) | |
| `lecture_type` | CharField | `video \| article` |
| `article_content` | TextField | Non-empty for article lectures, blank for video |
| `stream_master_playlist` | CharField(500) | Denormalized HLS master path (set after transcoding) |
| `stream_renditions` | JSONField | Denormalized rendition list (set after transcoding) |
| `transcoding_error` | TextField | Stores error message from failed transcoding |
| `is_preview` | BooleanField | If `True`, unenrolled catalog visitors can stream this video |

`stream_master_playlist` and `stream_renditions` are denormalized copies of the active
`VideoAsset` fields — they exist so learner playback queries never need to JOIN through
`VideoAsset`.

### `VideoAsset`

Tracks one physical video file and its transcoded HLS output.

| Field | Type | Notes |
|-------|------|-------|
| `lecture` | FK → `Lecture` | Parent lecture |
| `video_file` | FileField | Upload path: `courses/{course_slug}/lectures/{lecture_id}/raw/{uuid}.{ext}` |
| `original_filename` | CharField | Stored for display/audit |
| `mime_type` | CharField | e.g. `video/mp4` |
| `file_size` | BigIntegerField | Bytes |
| `duration_seconds` | PositiveIntegerField (null) | Probed by FFprobe after transcoding |
| `master_playlist` | CharField(500) | MEDIA_ROOT-relative path to HLS master.m3u8 |
| `renditions` | JSONField | Array: `[{"name":"720p","playlist":"...","resolution":"1280x720","bandwidth":2500000}]` |
| `is_active` | BooleanField | Only one active asset per lecture at any time |
| `status` | CharField | `uploading \| processing \| ready \| failed` |

**Unique constraint:** `(lecture, is_active=True)` — enforced at DB level. Prevents two active
assets for the same lecture.

### `VideoProcessingJob`

Tracks one Celery transcoding job's lifecycle for observability and debugging.

| Field | Type | Notes |
|-------|------|-------|
| `video_asset` | FK → `VideoAsset` | Cascade delete |
| `status` | CharField | `pending \| processing \| completed \| failed` |
| `notes` | TextField | Progress/error messages written by the task |
| `started_at` | DateTimeField (null) | Set when task begins processing |
| `completed_at` | DateTimeField (null) | Set on success or failure |

**Validation (`model.clean()`):** `completed_at` must not be before `started_at`.

### `WatchProgress`

Tracks a learner's playback position and completion status per lecture.

| Field | Type | Notes |
|-------|------|-------|
| `user` | FK → `User` | |
| `lecture` | FK → `Lecture` | |
| `watched_seconds` | PositiveIntegerField | Server-clamped playhead position |
| `is_completed` | BooleanField | True when learner has finished the lecture |
| `last_watched_at` | DateTimeField (auto_now) | Updated on every save |

**Unique constraint:** `(user, lecture)` — one row per learner per lecture.

---

## Upload: direct-to-S3 multipart

The browser PUTs the file straight to S3 through presigned part URLs. Django brokers signatures and
never streams the bytes, so request timeouts and proxy body limits stop being the real ceiling on
lecture size.

```
POST lectures/{id}/video/initiate-upload/   { filename, content_type, file_size }
  validates extension + video/* MIME + declared size
  builds the key:  courses/{slug}/lectures/{id}/raw/{uuid}.{ext}
  VideoAsset(video_file=<key>, is_active=False, status=uploading)
  S3 create_multipart_upload
  ← { videoAssetId, uploadId, objectKey, partSize, maxParts }
         │
         ▼  per part, 4 in parallel
POST video-assets/{id}/part-url/   { uploadId, partNumber }
  ← { presignedUrl }
PUT  <presignedUrl>   body = file.slice(...)          browser → S3, no Django
  ← 200, header  ETag: "d41d8c…"
         │
         ▼
POST video-assets/{id}/complete-upload/   { uploadId, parts: [{partNumber, etag}] }
  S3 complete_multipart_upload  →  head_object size check
  is_active=True, status=processing, VideoProcessingJob(pending)
  transaction.on_commit → transcode_video_asset_task.delay(...)
```

**The object key is never taken from the request.** `initiate` stamps it on `VideoAsset.video_file`
and every later step reads it back via `_object_key(video_asset)`; a client-supplied key is ignored.
Two reasons: an instructor holding two in-flight uploads could otherwise finalise asset X onto asset
Y's object (S3 accepts — the `uploadId` and key it was handed still match each other), and deriving
the key server-side means a forged `uploadId` can no longer be paired with the key it belongs to, so
S3 rejects it outright.

**`file_size` is a claim until it is measured.** The bytes bypass Django, so nothing enforces
`AWS_S3_MAX_UPLOAD_SIZE` at upload time — a client can declare 1 KB and push 50 GB. `complete` runs
`head_object` and compares `ContentLength` against the declared size; a mismatch deletes the object,
fails the asset and returns 422. A `head_object` that itself errors logs a warning and accepts, so a
transient S3 blip cannot discard an upload that genuinely succeeded.

**Dead upload sessions are 422, not 500.** `NoSuchUpload` / `InvalidPart` / `InvalidPartOrder` /
`EntityTooSmall` map to a 422 telling the client to restart the upload
(`_S3_CLIENT_ERROR_MESSAGES`); anything else stays a 500.

`reap_stuck_video_uploads_task` (hourly) flips assets stranded in `uploading` for over 24 h to
`failed`, so a browser that vanished mid-upload doesn't leave a lecture showing "uploading…" forever.
S3's own lifecycle rule reaps the orphaned parts.

**Required bucket CORS** — without `ExposeHeaders: ["ETag"]` the browser hides the header from JS,
the client has no ETags to send, and every upload fails at `complete`:

```json
[{
  "AllowedOrigins": ["https://<frontend-origin>"],
  "AllowedMethods": ["PUT", "GET", "HEAD"],
  "AllowedHeaders": ["*"],
  "ExposeHeaders": ["ETag"],
  "MaxAgeSeconds": 3000
}]
```

Tests: `courses/all_tests/test_video_upload.py`.

---

## Video upload and transcoding pipeline

> The multipart flow above is how the browser uploads today. The single-request path below is still
> wired (`PATCH lectures/{id}/` with a `video_file`) and remains the entry point for any non-browser
> client; both converge on the same `VideoAsset` row and the same transcoding task.

### Full flow diagram

```
Instructor uploads video file
         │
         ▼
PATCH /api/v1/courses/lectures/{lecture_id}/
  body: { video_file: <multipart> }
         │
         ▼
replace_lecture_video_and_enqueue_transcoding(lecture, uploaded_file)
  [courses/services/section_service.py]
         │
         ├─ 1. Validate: lecture.lecture_type must be 'video'
         │
         ├─ 2. Deactivate previous active asset:
         │      VideoAsset.objects.filter(lecture=lecture, is_active=True)
         │                        .update(is_active=False)
         │
         ├─ 3. Create new VideoAsset:
         │      status=UPLOADING, is_active=True
         │      Stores file to: courses/{slug}/lectures/{id}/raw/{uuid}.ext
         │
         ├─ 4. Create VideoProcessingJob:
         │      status=PENDING
         │      notes='Video uploaded and queued for transcoding.'
         │
         ├─ 5. Clear Lecture streaming fields:
         │      stream_master_playlist = ''
         │      stream_renditions = []
         │      transcoding_error = ''
         │
         └─ 6. Enqueue Celery task:
                transcode_video_asset_task.delay(video_asset.id, job.id)
         │
         ▼
Return VideoAsset to caller (status=PROCESSING at this point)

──────────────────────────────────────────────────────
Celery worker picks up the task
──────────────────────────────────────────────────────
         │
         ▼
transcode_video_asset_task(self, video_asset_id, job_id)
  [courses/tasks.py]
  Decorator: @shared_task(bind=True,
               autoretry_for=(Exception,),
               retry_backoff=True,
               retry_jitter=True,
               max_retries=3)
         │
         ├─ Load VideoAsset + VideoProcessingJob from DB
         ├─ Job.status → PROCESSING, job.started_at = now()
         ├─ VideoAsset.status → PROCESSING
         │
         ▼
transcode_video_asset(video_asset)
  [courses/transcoding.py]
         │
         ├─ Locate input file: video_asset.video_file.path
         ├─ ffprobe: probe duration_seconds from raw file
         ├─ Build output path:
         │    MEDIA_ROOT/courses/{slug}/lectures/{id}/hls/{asset_id}/
         │
         └─ For each rendition (5 total), run FFmpeg:
               ┌─────────────────────────────────────────────────────┐
               │  Rendition   Video bitrate  Audio bitrate  Height   │
               │  240p        400k           64k            240px    │
               │  360p        800k           96k            360px    │
               │  480p        1400k          128k           480px    │
               │  720p        2500k          128k           720px    │
               │  1080p       5000k          192k           1080px   │
               └─────────────────────────────────────────────────────┘
               FFmpeg settings per rendition:
               • Codec: H.264 (profile:main), CRF=20
               • Audio: AAC, 48kHz sample rate
               • Scale: -2:{height} (preserves aspect ratio)
               • Keyframe: 48-frame GOP, scene detection disabled
               • HLS: 6-second segments, VOD playlist type
               • Output: {name}.m3u8 + {name}_000.ts, {name}_001.ts, ...
         │
         ├─ ffprobe first segment: detect actual width×height
         ├─ Write master.m3u8 (EXT-X-STREAM-INF with bandwidth + resolution)
         ├─ Return (master_relative_path, renditions_list, duration_seconds)
         │
         ▼
Back in transcode_video_asset_task:
         │
         ┌──────────────┴──────────────┐
         │ SUCCESS                     │ FAILURE
         │                             │
         ▼                             ▼
  atomic transaction:           atomic transaction:
  VideoAsset.master_playlist     VideoAsset.status → FAILED
  VideoAsset.renditions          Lecture.transcoding_error = str(exc)
  VideoAsset.duration_seconds    Job.status → FAILED
  VideoAsset.status → READY      Job.notes = f'Transcoding failed: {exc}'
  Lecture.stream_master_playlist Job.completed_at = now()
  Lecture.stream_renditions                │
  Lecture.transcoding_error = ''           ▼
  Job.status → COMPLETED           raise exc
  Job.completed_at = now()         → Celery retries (up to 3x)
                                     exponential backoff + jitter
                                   → After 3rd failure: task stays failed
```

### HLS output structure

After successful transcoding, the following files exist on disk:

```
MEDIA_ROOT/
└── courses/{course_slug}/lectures/{lecture_id}/hls/{video_asset_id}/
    ├── master.m3u8          ← main playlist (referenced in stream_master_playlist)
    ├── 240p.m3u8            ← per-rendition playlist
    ├── 240p_000.ts
    ├── 240p_001.ts
    ├── ...
    ├── 360p.m3u8
    ├── 360p_000.ts
    ├── ...
    ├── 720p.m3u8
    ├── 720p_000.ts
    └── ...
```

The `master.m3u8` contains `#EXT-X-STREAM-INF` entries for each rendition:
```m3u8
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240
240p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360p.m3u8
...
```

HLS-compatible video players (hls.js, Video.js, AVPlayer) auto-select the appropriate rendition
based on the viewer's bandwidth and screen resolution.

### Task retry behavior

The Celery task uses:
- `autoretry_for=(Exception,)` — any exception triggers automatic retry
- `retry_backoff=True` — exponential backoff between retries (1s → 2s → 4s by default)
- `retry_jitter=True` — random jitter added to avoid thundering herd
- `max_retries=3` — after 3 failures, the exception is not caught and the task goes to `FAILURE`
  state in Celery; `VideoAsset.status` stays `FAILED`, `Lecture.transcoding_error` holds the
  error message

---

## WatchProgress: tracking learner playback

### `upsert_watch_progress()` — server-side clamping

`learner_service.upsert_watch_progress(user, lecture, watched_seconds, is_completed)`
in `courses/services/learner_service.py`.

HLS video players fire `timeupdate` and `ended` events; the client cannot be trusted to report
accurate playhead values. The server enforces two invariants:

```
1. Fetch active VideoAsset.duration_seconds for the lecture
         │
         ├─ If no duration (article lecture or no asset yet):
         │    → watched_seconds forced to 0 (meaningless for articles)
         │
         └─ If duration exists:
              watched_seconds = max(0, min(watched_seconds, duration))
              │
              └─ if clamped watched_seconds >= duration:
                   is_completed = True  ← forced (video has ended regardless of client value)
         │
         ▼
WatchProgress.objects.update_or_create(
    user=user, lecture=lecture,
    defaults={ watched_seconds=..., is_completed=... }
)
```

This is idempotent — calling it multiple times with the same values is safe. The `update_or_create`
returns the row; the calling view returns `200 OK`.

### WatchProgress signals and progress recalculation

Two signals in `courses/signals.py` fire on every `WatchProgress` save:

**`pre_save` — cache previous completion state:**
```python
# Caches instance._previous_is_completed before the save
# This is the ONLY way post_save knows whether is_completed actually changed
```

**`post_save` — conditional recalculation:**
```python
# Recalculates enrollment progress_percent ONLY when:
# • Row is newly created AND is_completed=True
# • Row is updated AND is_completed changed from previous value
#
# Does NOT recalculate on playhead-only updates (watched_seconds changes
# without toggling is_completed) — avoids expensive recalc on every tick
```

The recalculation signal calls `recalculate_progress(enrollment)` in
`courses/services/enrollment_service.py`, which recomputes `progress_percent` across all
content types (lectures, quizzes, assignments, coding exercises).

---

## Learner lecture consumption

```
GET /api/v1/courses/learn/lectures/{lecture_id}/
  Permission: enrolled learner OR course's own instructor
  Returns 404 (not 403) for unenrolled — lecture IDs are not public
         │
         ▼
get_consumption_lecture(user, lecture_id)  ← learner_service.py
  • Verifies access: enrollment OR instructor
  • Raises Lecture.DoesNotExist → view returns 404
         │
         ▼
LearnerLectureDetailSerializer:
  • VIDEO: stream_master_playlist (storage key — NOT playable, see
           "Playback: CloudFront-signed HLS" below), stream_renditions
  • ARTICLE: article_content
  • progress: { watched_seconds, is_completed, last_watched_at }
  • duration_seconds (from active VideoAsset)
  • Omitted: transcoding_error, raw VideoAsset details
```

```
POST /api/v1/courses/learn/lectures/{lecture_id}/progress/
  Permission: IsLearnerUser (instructors get 403 — preview must not pollute history)
  body: { watched_seconds: <int>, is_completed: <bool> }
         │
         ▼
upsert_watch_progress(user, lecture, watched_seconds, is_completed)
  (with server-side clamping as described above)
         │
         ▼
WatchProgress post_save signal → recalculate_progress if is_completed changed
         │
         ▼
200 OK — { success: true, data: { watched_seconds, is_completed, last_watched_at } }
```

---

## Playback: CloudFront-signed HLS

`stream_master_playlist` is a **storage-relative key, not a playable URL**. Behind CloudFront the
object is private; requesting it unsigned returns `403`, which the browser reports to the client as a
CORS error (an error response carries no `Access-Control-Allow-Origin`). Clients must never build a
playback URL from that field.

```
GET /api/v1/courses/lectures/{lecture_id}/stream/     ← LectureStreamUrlView
  Permission: IsAuthenticated + IsEmailVerified, then object-level:
    course instructor / platform admin  → always
    learner                             → active enrollment, or lecture.is_preview
  404 on no-access (numeric ID). 422 while the video is still transcoding.
  Drip release (cohort start / section unlocks_at) → 422; instructors and
  admins bypass it.
         │
         ▼
Lecture.get_stream_context(user) → build_signed_hls_cookies(master_playlist)
  [courses/cloudfront_signer.py]
         │
         ▼
200 OK  body:    { data: { streamUrl: "https://<cdn>/media/.../master.m3u8" } }
        headers: Set-Cookie × 3 — CloudFront-Policy / -Signature / -Key-Pair-Id
                 Path=/media/courses/<slug>/lectures/<id>/hls/<asset>/
                 Domain=CLOUDFRONT_COOKIE_DOMAIN; Secure; HttpOnly; SameSite=None
```

Signed **cookies**, not signed URLs: one policy authorizes the master playlist, every rendition
playlist and every `.ts` segment under the same directory. A signed URL would only cover the master
playlist. The policy is a wildcard over that directory, so the cookie set for one video cannot
authorize another lecture.

### Client contract — all three parts are required

1. Fetch `/stream/` with credentials (`fetch(..., { credentials: "include" })`), or the browser
   discards the `Set-Cookie` headers.
2. Send the cookies back on every segment request. With hls.js that is
   `new Hls({ xhrSetup: (xhr) => { xhr.withCredentials = true; } })`; for Safari's native HLS path,
   `<video crossOrigin="use-credentials">`.
3. Re-fetch before `CLOUDFRONT_SIGNED_URL_TTL_SECONDS` (default 2 h) elapses — the response re-sets
   the cookies, which is what keeps a long viewing session alive. The URL is stable, so re-fetching
   does not restart playback.

Because the cookies travel cross-site, CloudFront must return `Access-Control-Allow-Origin` set to
the **exact** frontend origin (`*` is illegal with credentials) plus
`Access-Control-Allow-Credentials: true`, and must include `Origin` in the cache key — otherwise a
response cached without those headers is served to every viewer. `CLOUDFRONT_DOMAIN` has to be a
CNAME under `CLOUDFRONT_COOKIE_DOMAIN`; cookies scoped to the API's parent domain are never sent to
`*.cloudfront.net`.

**Local dev fallback:** with `CLOUDFRONT_DOMAIN` unset the signer returns `None` and the view falls
back to `default_storage.url(...)` — a root-relative `/media/...` path, no cookies. Clients resolve
it against the API origin, since relative to the page it would point at the frontend.

---

## Why this design

- **Async transcoding** keeps the upload endpoint fast — the instructor receives a `200` immediately
  after the file is stored, without waiting minutes for FFmpeg to finish.
- **Historical `VideoAsset` rows retained** — old assets are deactivated, not deleted. If a
  replacement upload fails transcoding, the previous working version can be reactivated. Only the
  active asset is used for playback.
- **`VideoProcessingJob` as observability record** — job status, start/end timestamps, and notes
  allow admins and support staff to monitor the transcoding queue and debug failures without
  reading Celery logs directly.
- **Denormalized `stream_*` fields on `Lecture`** — learner playback queries look up `Lecture`
  directly without joining through `VideoAsset`, keeping the most common read path simple and fast.
- **Single-active-asset constraint at DB level** — the unique partial index `(lecture, is_active=True)`
  enforces the invariant even if a bug bypasses the service layer.
- **Server-side `watched_seconds` clamping** — HLS players legitimately overshoot `duration` by
  a fraction when the `ended` event fires, so we cap rather than reject. Forces `is_completed=True`
  if the cursor reaches the end, regardless of what the client declared.
- **Signal-based progress recalculation** — `WatchProgress` post_save triggers the recalc only
  when `is_completed` changes (not on every playhead update), preventing expensive recalculations
  on every progress tick.

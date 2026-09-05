# 36) Video Upload And Playback — Plain-Language Walkthrough

What happens between an instructor picking a video file and a learner watching it.
Written for anyone on the team, not only backend engineers. The formal reference is
[`05-lectures-and-video-pipeline.md`](05-lectures-and-video-pipeline.md); this doc
explains the *why*.

---

## The short version

An instructor uploads one video file. The server does not serve that file. Instead a
background worker re-encodes it into several smaller versions at different qualities,
chops each one into 6-second chunks, and writes an index listing them. The learner's
player reads that index and picks whichever quality their connection can handle,
switching mid-video as the connection changes.

That format is called **HLS** (HTTP Live Streaming). It is why a video starts almost
instantly instead of buffering a whole file, and why quality drops to keep playing
instead of freezing when the network gets slow.

---

## The four records involved

| Record | Plain meaning |
|---|---|
| `Lecture` | A slot in the curriculum. Holds either a video or an article, never both. |
| `VideoAsset` | One uploaded file and everything produced from it. Re-uploading makes a **new** asset; the old one is kept but marked inactive. Only one may be active per lecture. |
| `VideoProcessingJob` | The receipt for one conversion attempt — when it started, whether it worked, what went wrong. |
| `WatchProgress` | How far one learner got in one lecture. |

`Lecture` also carries **copies** of the finished playlist path and rendition list
(`stream_master_playlist`, `stream_renditions`). Duplicated on purpose: playback reads
them constantly, and copying them onto the lecture avoids a database join on every
request.

---

## Part 1 — Upload

The instructor sends the file to the backend as an ordinary form upload, attached to
the lecture itself:

```
POST /api/v1/courses/sections/<section_id>/lectures/    (new lecture with video)
PATCH /api/v1/courses/lectures/<lecture_id>/            (replace an existing video)
    body: multipart form, field `video_file`
```

The file travels **through Django** — it is not uploaded straight to S3 from the
browser. On a large file this ties up a web worker for the whole transfer.

The serializer hands the file to `replace_lecture_video_and_enqueue_transcoding()`
([`section_service.py:138`](../../courses/services/section_service.py#L138)), which:

1. Refuses if the lecture is an article lecture.
2. Marks every existing active asset for that lecture inactive.
3. Creates a new `VideoAsset` and saves the raw file to
   `courses/{course-slug}/lectures/{lecture-id}/raw/{uuid}.mp4`. The UUID means two
   uploads named `lecture.mp4` never collide.
4. Creates a `VideoProcessingJob`, sets the asset to `processing`, and clears the
   lecture's old playlist fields so the player stops serving stale video immediately.
5. Puts a message on the Redis queue for a background worker.

The API responds here. The instructor sees "processing" — nothing is watchable yet.

> **Known bug (audit CRS-H1, open):** step 5 fires the queue message directly instead
> of waiting for the database transaction to commit. A worker can pick the job up
> before the row is visible, or the transaction can roll back and leave a job pointing
> at a record that no longer exists. Every other queue dispatch in this codebase uses
> `transaction.on_commit`. This one should too.

---

## Part 2 — Conversion

A Celery worker runs `transcode_video_asset_task`
([`tasks.py:43`](../../courses/tasks.py#L43)). If the job is already finished it stops
immediately — the queue can deliver the same message twice, and re-encoding a finished
video would be pure waste.

### Reading the source

`ffprobe` runs **once** and reports width, height, length, frame rate, and whether
there is an audio track. If that fails the job fails. It does not guess: the audio
answer decides whether audio gets encoded at all, so a wrong guess would ship a silent
lecture that still reports success.

When the file lives on S3, it is pulled to local disk first — ffmpeg cannot read from
object storage directly.

### Choosing the qualities

| Rendition | Height | Bitrate ceiling |
|---|---|---|
| 360p | 360px | 800 kbps |
| 480p | 480px | 1400 kbps |
| 720p | 720px | 2500 kbps |
| 1080p | 1080px | 5000 kbps |

**The list stops at the source's own height.** A 480p recording produces 360p and 480p
only. Making a 1080p version of a 480p video cannot add detail that was never
captured — it just costs the slowest encode and the largest files to produce a blurry
result nobody benefits from. A source below 360p gets a single version at its own size.

### The encode

One ffmpeg command produces the whole set. It reads the video **once**, copies the
decoded frames in memory as many ways as there are qualities, and encodes them all
together. Running ffmpeg once per quality — which is what this used to do — meant
reading a 10-minute video four times over for no gain.

Each quality is written as a `.m3u8` playlist plus a numbered run of 6-second `.ts`
chunks. A keyframe is forced at the start of every chunk, identically across all
qualities, so a player can jump between them without a visible gap.

### Fixing the advertised bandwidth

The master index tells the player how much bandwidth each quality needs, and the player
picks based on those numbers. ffmpeg fills them in from the *ceiling* we set, but the
encoder rarely reaches that ceiling on lecture footage — slides and a talking head
compress very well. Left alone, the player would read an inflated number, decide it
cannot afford good quality, and serve a worse stream than the connection allows.

So after encoding, `_rewrite_master_playlist()` measures the chunks that were actually
produced and writes the real figures in. The same measured number is stored on the
`VideoAsset`, so the database and the playlist always agree.

### Uploading

Every generated file goes to storage — local disk in development, S3 in production.
A ladder is hundreds of small files, so they upload 8 at a time
(`HLS_UPLOAD_CONCURRENCY`). One at a time meant waiting for a network round trip per
chunk.

### Finishing

On success, inside one transaction: the asset gets its playlist path, rendition list,
duration and `ready` status; the lecture gets its copies; the job is marked complete.
A `VIDEO_READY` notification goes to the course's instructors after the transaction
commits.

On failure the asset becomes `failed`, the error text is stored on
`Lecture.transcoding_error` for the instructor to read, and Celery retries up to three
times with growing gaps. After the third failure a `VIDEO_FAILED` notification is sent.

A single run is capped at `FFMPEG_TIMEOUT_SECONDS` (3 hours). Without that cap a stuck
ffmpeg would hold a worker forever.

---

## Part 3 — What ends up in storage

```
courses/{course-slug}/lectures/{lecture-id}/
├── raw/
│   └── {uuid}.mp4              ← the original upload, kept
└── hls/{video-asset-id}/
    ├── master.m3u8             ← the index the player opens first
    ├── 360p.m3u8               ← chunk list for one quality
    ├── 360p_000.ts  360p_001.ts  …
    ├── 480p.m3u8
    └── 480p_000.ts  …
```

`master.m3u8` looks like this — one entry per quality, with the measured bandwidth,
the real pixel size, and the codecs:

```m3u8
#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=654240,AVERAGE-BANDWIDTH=641803,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2"
360p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1324272,AVERAGE-BANDWIDTH=1298110,RESOLUTION=854x480,CODECS="avc1.4d401e,mp4a.40.2"
480p.m3u8
```

Re-uploading a video writes a **new** `hls/{video-asset-id}/` folder. The old one stays
in the bucket. Nothing cleans it up.

---

## Part 4 — Playback

### Getting the address

```
GET /api/v1/courses/learn/lectures/<lecture_id>/
```

Returns `stream_master_playlist` (the path) and `stream_renditions` (the quality list),
plus the learner's saved position so the player can resume.

Who is allowed through:

- an enrolled learner, or the course's own instructor previewing;
- unenrolled visitors only for lectures flagged `is_preview`, and only via the catalog;
- content still locked by a cohort start date or a section's `unlocks_at` is refused
  with a **422** and a plain message — it is a timing problem, not a permission one.

A lecture the caller may not see returns **404**, never 403. Lecture IDs are sequential,
so confirming one exists would let someone map the platform by counting upward.

### Building the URL

The API returns a storage-relative path, not a full URL. The frontend prepends the
media root in `hlsAssetUrl()`
([`shared.ts:77`](../../../Career-College-Frontend/src/lib/course-api/shared.ts#L77)):

```ts
`${apiOrigin}/media/${path}`
```

### Playing it

`hls.js` in the browser opens `master.m3u8`, reads the list, measures the connection,
and requests chunks from whichever quality fits. It re-measures as it goes and switches
mid-playback. Safari plays HLS natively and skips the library. The player also exposes
a manual quality picker, which just pins hls.js to one entry.

---

## Part 5 — Progress

```
POST /api/v1/courses/learn/lectures/<lecture_id>/progress/
    { "watched_seconds": 143, "is_completed": false }
```

The player posts this every few seconds. The server does not take it at face value:

- `watched_seconds` is **capped** at the video's real duration. Players routinely
  overshoot by a fraction of a second; capping is kinder than rejecting.
- If the capped position lands at the end, `is_completed` is forced true regardless of
  what the client claimed. The video has finished; arguing about it helps nobody.
- Article lectures have no duration, so their `watched_seconds` is forced to 0.

Saving progress fires a signal that recalculates the enrollment's overall percentage,
which is what drives the dashboard and, at 100%, issues a certificate.

---

## When things go wrong

| Symptom | Likely cause |
|---|---|
| Stuck on "processing" forever | No Celery worker running. The API queues the job and returns success either way. |
| `transcoding_error` mentions ffmpeg | Corrupt upload, or an unsupported container. The stored message includes ffmpeg's own last words. |
| Video plays but is silent | The source genuinely had no audio track. A probe failure now fails the job instead of silently dropping audio. |
| Player picks a low quality on a fast connection | Was caused by inflated bandwidth figures in the master index; fixed by the measured-bandwidth rewrite. If it reappears, check what `_rewrite_master_playlist` wrote. |
| Old lecture only offers 240p–1080p | Encoded before the ladder changed. Existing renditions are untouched; only a re-upload picks up the current ladder. |

---

## Things to know before changing this

**The media files themselves are not access-controlled.** The API decides who learns
the *path*, but `/media/...` serves whatever is asked for. Anyone who obtains or
guesses a path can fetch the chunks without logging in. Paths contain a UUID and an
asset id, so they are not guessable in practice — but this is obscurity, not
enforcement. Signed URLs or signed cookies would be the real fix.

**`/media/` is only wired up when `DEBUG=True`.** In production something else has to
serve it — nginx, or S3/CloudFront. Note that the frontend builds media URLs from the
**API origin**, so pointing storage at a CloudFront domain also requires changing
`hlsAssetUrl()` or proxying `/media/` through the API host.

**Uploads occupy a web worker for the whole transfer.** A 2 GB lecture over a slow
connection holds one Django worker the entire time. Direct browser-to-S3 upload would
remove that, and would also skip the download the transcoder currently performs.

**Nothing deletes old files.** Inactive assets keep both their raw upload and their
full HLS output indefinitely.

**One ffmpeg run now uses every core.** Run video Celery workers at
`--concurrency=1`; two parallel transcodes just fight each other for CPU.

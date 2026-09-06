import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage

from courses.models import VideoAsset

logger = logging.getLogger(__name__)

SEGMENT_SECONDS = 6

DEFAULT_RENDITIONS = [
    {'name': '360p', 'height': 360, 'maxrate': 800, 'audio_bitrate': '96k'},
    {'name': '480p', 'height': 480, 'maxrate': 1400, 'audio_bitrate': '128k'},
    {'name': '720p', 'height': 720, 'maxrate': 2500, 'audio_bitrate': '128k'},
    {'name': '1080p', 'height': 1080, 'maxrate': 5000, 'audio_bitrate': '192k'},
]

# Per-encoder quality/GOP flags, selected by settings.VIDEO_ENCODER. Scaling
# stays on the CPU in every case: encoding is the expensive part, and keeping
# the filter graph identical avoids hwupload/format juggling.
#
# The hardware profiles are opt-in and untested on the current CPU-only
# deployment. A GPU encoder opens one session per rendition, and consumer
# NVENC cards cap concurrent sessions (3-8 depending on driver) -- verify the
# full ladder encodes before switching a fleet over.
ENCODER_PROFILES = {
    'libx264': {
        'codec': 'libx264',
        'preset_flag': '-preset',
        'preset': 'veryfast',
        # 21, not the x264 default of 23: `veryfast` already trades quality
        # for speed, and lecture slides show banding before the video does.
        'quality': ['-crf', '21'],
        'gop': lambda gop: ['-g', str(gop), '-keyint_min', str(gop), '-sc_threshold', '0'],
        'extra': [],
    },
    'h264_nvenc': {
        'codec': 'h264_nvenc',
        'preset_flag': '-preset',
        'preset': 'p4',
        'quality': ['-rc', 'vbr', '-cq', '21'],
        'gop': lambda gop: ['-g', str(gop), '-no-scenecut', '1'],
        'extra': ['-tune', 'hq'],
    },
    'h264_qsv': {
        'codec': 'h264_qsv',
        'preset_flag': '-preset',
        'preset': 'faster',
        'quality': ['-global_quality', '21'],
        'gop': lambda gop: ['-g', str(gop)],
        'extra': [],
    },
}


_STREAM_INF_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_STREAM_INF_PREFIX = '#EXT-X-STREAM-INF:'


def _ffmpeg_binary() -> str:
    return getattr(settings, 'FFMPEG_BINARY_PATH', 'ffmpeg')


def _ffprobe_binary() -> str:
    configured = getattr(settings, 'FFPROBE_BINARY_PATH', '').strip()
    if configured:
        return configured

    ffmpeg_bin = Path(_ffmpeg_binary())
    if ffmpeg_bin.name.lower().startswith('ffmpeg'):
        sibling = ffmpeg_bin.with_name('ffprobe.exe' if ffmpeg_bin.suffix.lower() == '.exe' else 'ffprobe')
        return str(sibling)
    return 'ffprobe'


def _encoder_profile() -> dict:
    name = getattr(settings, 'VIDEO_ENCODER', 'libx264')
    profile = ENCODER_PROFILES.get(name)
    if profile is None:
        logger.warning('Unknown VIDEO_ENCODER=%s, falling back to libx264', name)
        return ENCODER_PROFILES['libx264']
    return profile


def _build_output_relative_root(video_asset: VideoAsset) -> str:
    lecture = video_asset.lecture
    course_slug = lecture.section.course.slug
    return f'courses/{course_slug}/lectures/{lecture.id}/hls/{video_asset.id}'


def _upload_output_dir(local_root: Path, relative_root: str) -> None:
    """
    Push every generated HLS file to the configured storage backend. A ladder
    is hundreds of small files, so serial uploads are dominated by round-trip
    latency -- fan them out. S3Boto3Storage keeps its boto3 connection in a
    threading.local(), so one storage instance is safe across the pool.
    """
    files = [p for p in sorted(local_root.rglob('*')) if p.is_file()]

    def _upload(local_file: Path) -> None:
        relative_name = f'{relative_root}/{local_file.relative_to(local_root).as_posix()}'
        # FileSystemStorage renames on collision instead of overwriting, so
        # delete first. Deleting a missing key is a no-op on both backends,
        # which is why there is no exists() probe here.
        with contextlib.suppress(Exception):
            default_storage.delete(relative_name)
        with local_file.open('rb') as fh:
            default_storage.save(relative_name, File(fh))

    workers = getattr(settings, 'HLS_UPLOAD_CONCURRENCY', 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_upload, files))


def _run_ffmpeg_command(command: list[str]) -> None:
    """
    Run one ffmpeg invocation. A hung ffmpeg would otherwise pin a Celery
    worker forever (acks_late only redelivers once the worker dies), hence the
    timeout; the stderr tail is folded into the raised message because
    CalledProcessError.__str__ drops it and the task stores str(exc) on
    Lecture.transcoding_error.
    """
    timeout = getattr(settings, 'FFMPEG_TIMEOUT_SECONDS', 10800)
    # DEBUG, not INFO: a four-rung ladder is ~1200 characters of argv and
    # drowns the worker log. transcode_video_asset logs the readable summary.
    logger.debug('FFmpeg argv: %s', ' '.join(command))
    started = time.monotonic()
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error('FFmpeg timed out after %ss', timeout)
        raise RuntimeError(f'FFmpeg timed out after {timeout}s.') from None
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or '').strip()
        logger.error(
            'FFmpeg failed after %.1fs (rc=%s): %s',
            time.monotonic() - started, exc.returncode, stderr[-4000:],
        )
        raise RuntimeError(f'FFmpeg failed (rc={exc.returncode}): {stderr[-1000:]}') from None


def _probe_source(video_file: Path) -> dict:
    """
    One ffprobe call for everything we need: dimensions, duration, frame rate
    and whether an audio track exists.

    Raises rather than degrading: has_audio drives the -map arguments, so a
    swallowed probe failure would silently produce a video-only ladder and
    still report success.
    """
    command = [
        _ffprobe_binary(),
        '-v', 'error',
        '-show_entries', 'stream=index,codec_type,width,height,avg_frame_rate:format=duration',
        '-of', 'json',
        str(video_file),
    ]

    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        payload = json.loads(result.stdout or '{}')
    except Exception as exc:
        logger.error('Failed to probe source file=%s', video_file, exc_info=True)
        raise RuntimeError(f'Could not read the uploaded video: {exc}') from None

    info = {'width': None, 'height': None, 'duration': None, 'fps': None, 'has_audio': False}
    for stream in payload.get('streams', []):
        kind = stream.get('codec_type')
        if kind == 'video' and info['width'] is None:
            info['width'] = stream.get('width')
            info['height'] = stream.get('height')
            rate = stream.get('avg_frame_rate') or '0/0'
            with contextlib.suppress(Exception):
                num, _, den = rate.partition('/')
                if float(den or 0) > 0:
                    info['fps'] = float(num) / float(den)
        elif kind == 'audio':
            info['has_audio'] = True

    if not info['width'] or not info['height']:
        raise RuntimeError('The uploaded file contains no readable video stream.')

    with contextlib.suppress(Exception):
        duration = float(payload.get('format', {}).get('duration'))
        if duration > 0:
            # Ensure DB validation (> 0) always passes for short clips.
            info['duration'] = max(1, int(round(duration)))

    return info


def _copy_remote_to(video_file, destination: Path) -> None:
    """
    Copy a source video out of remote storage to a local path.

    The S3 backend's .open('rb') pulls the whole object into a
    SpooledTemporaryFile of its own (max_memory_size defaults to 0, so it hits
    disk immediately) -- copying that out again writes the source to disk
    twice and reads it back once. When the backend exposes the underlying
    boto3 object, hand the destination straight to the managed transfer
    instead: one multipart download, one write. Any other backend falls back
    to a buffered copy.
    """
    storage = video_file.storage
    remote = storage.open(video_file.name, 'rb')
    try:
        s3_object = getattr(remote, 'obj', None)
        if s3_object is not None:
            s3_object.download_file(
                str(destination),
                Config=getattr(storage, 'transfer_config', None),
            )
            return
        with destination.open('wb') as local_file:
            shutil.copyfileobj(remote, local_file, 8 * 1024 * 1024)
    finally:
        remote.close()


@contextlib.contextmanager
def _local_input_path(video_asset: VideoAsset):
    """
    Yield a local filesystem path for the video file. Storage backends that
    don't support absolute paths (e.g. object storage) raise NotImplementedError
    on .path() -- fall back to pulling the file into a temp copy.
    """
    try:
        local_path = Path(video_asset.video_file.path)
    except NotImplementedError:
        local_path = None

    if local_path is not None:
        if not local_path.exists():
            raise FileNotFoundError(f'Video file not found: {local_path}')
        yield local_path
        return

    # mkstemp, not NamedTemporaryFile: the handle is closed before the
    # download so the transfer owns the path outright.
    handle, raw_path = tempfile.mkstemp(suffix=Path(video_asset.video_file.name).suffix)
    os.close(handle)
    tmp_path = Path(raw_path)
    try:
        _copy_remote_to(video_asset.video_file, tmp_path)
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


def _select_ladder(source_height: int) -> list[dict]:
    """
    Never upscale. A 480p lecture recording gains nothing from 720p/1080p
    renditions except encode time and storage.

    A source shorter than the lowest rung gets one rendition at its own height
    rather than a blown-up 360p -- the ladder must never come back empty.
    """
    ladder = [r for r in DEFAULT_RENDITIONS if r['height'] <= source_height]
    if ladder:
        return ladder

    floor = DEFAULT_RENDITIONS[0]
    height = max(2, source_height - source_height % 2)  # yuv420p needs even
    return [{**floor, 'name': f'{height}p', 'height': height}]


def _stream_scoped(options: list[str], index: int) -> list[str]:
    """
    Append a per-stream specifier to each flag in a flag/value list, so
    ['-crf', '21'] becomes ['-crf:v:2', '21']. Values never start with '-'
    in these profiles, which is what makes the flag test safe.
    """
    return [f'{token}:v:{index}' if token.startswith('-') else token for token in options]


def _build_ladder_command(input_path: Path, output_root: Path, ladder: list[dict], probe: dict) -> list[str]:
    """
    Build ONE ffmpeg invocation that decodes the source a single time, splits
    the decoded stream N ways, and writes the full HLS ladder plus the master
    playlist. This replaces N full decode+scale+encode passes.
    """
    profile = _encoder_profile()
    count = len(ladder)
    # One keyframe per segment. Every rendition shares this GOP and the same
    # input timeline, so segment boundaries line up across the ladder and a
    # player can switch quality mid-stream without a gap.
    gop = max(1, int(round((probe['fps'] or 24) * SEGMENT_SECONDS)))

    # [0:v]split=3[v0][v1][v2];[v0]scale=-2:240[v0out];...
    split_labels = ''.join(f'[v{i}]' for i in range(count))
    filter_parts = [f'[0:v]split={count}{split_labels}']
    for index, rendition in enumerate(ladder):
        filter_parts.append(f"[v{index}]scale=-2:{rendition['height']}[v{index}out]")

    command = [
        _ffmpeg_binary(),
        '-nostdin', '-hide_banner', '-nostats', '-loglevel', 'error', '-y',
        '-i', str(input_path),
        '-filter_complex', ';'.join(filter_parts),
    ]

    for index, rendition in enumerate(ladder):
        maxrate = rendition['maxrate']
        command += ['-map', f'[v{index}out]', f'-c:v:{index}', profile['codec']]
        command += [f"{profile['preset_flag']}:v:{index}", profile['preset']]
        command += _stream_scoped(profile['quality'], index)
        command += _stream_scoped(profile['extra'], index)
        command += _stream_scoped(profile['gop'](gop), index)
        command += [f'-profile:v:{index}', 'high' if rendition['height'] >= 720 else 'main']
        command += [f'-maxrate:v:{index}', f'{maxrate}k', f'-bufsize:v:{index}', f'{maxrate * 2}k']

    if probe['has_audio']:
        for index, rendition in enumerate(ladder):
            command += ['-map', 'a:0', f'-c:a:{index}', 'aac', f'-b:a:{index}', rendition['audio_bitrate']]
        command += ['-ar', '48000', '-ac', '2']
        stream_map = ' '.join(f"v:{i},a:{i},name:{r['name']}" for i, r in enumerate(ladder))
    else:
        stream_map = ' '.join(f"v:{i},name:{r['name']}" for i, r in enumerate(ladder))

    command += [
        '-f', 'hls',
        '-hls_time', str(SEGMENT_SECONDS),
        '-hls_playlist_type', 'vod',
        '-hls_flags', 'independent_segments',
        '-hls_segment_filename', str(output_root / '%v_%03d.ts'),
        '-master_pl_name', 'master.m3u8',
        '-var_stream_map', stream_map,
        str(output_root / '%v.m3u8'),
    ]
    return command


def _measure_playlist_bitrate(playlist_path: Path) -> tuple[int, int]:
    """Return (peak, average) bits/s measured from a variant's own segments."""
    peak = 0
    total_bytes = 0
    total_seconds = 0.0
    duration = None

    for line in playlist_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('#EXTINF:'):
            duration = None
            with contextlib.suppress(ValueError):
                duration = float(line[len('#EXTINF:'):].split(',')[0])
            continue
        if not line or line.startswith('#'):
            continue
        if duration and duration > 0:
            segment = playlist_path.parent / line
            if segment.is_file():
                size = segment.stat().st_size
                total_bytes += size
                total_seconds += duration
                peak = max(peak, int(size * 8 / duration))
        duration = None

    average = int(total_bytes * 8 / total_seconds) if total_seconds else 0
    return peak or average, average


def _rewrite_master_playlist(output_root: Path, ladder: list[dict]) -> list[dict]:
    """
    Replace ffmpeg's declared BANDWIDTH with the measured one and return the
    rendition rows for the DB.

    ffmpeg derives BANDWIDTH from the VBV ceiling, but the ladder runs capped
    CRF -- the cap only binds on complex scenes, so a lecture recording lands
    far below it. Players choose renditions from these numbers, so an inflated
    ceiling makes them start too low and stay there. The measured peak is what
    the segments actually cost; AVERAGE-BANDWIDTH carries the mean alongside.
    RESOLUTION and CODECS are ffmpeg's own values, kept as written.
    """
    master_path = output_root / 'master.m3u8'
    by_name = {r['name']: r for r in ladder}
    rows: list[dict] = []
    out: list[str] = []
    pending = None

    for line in master_path.read_text(encoding='utf-8').splitlines():
        if line.startswith(_STREAM_INF_PREFIX):
            pending = line
            continue
        if pending is None:
            out.append(line)
            continue

        uri = line.strip()
        name = Path(uri).stem
        peak, average = _measure_playlist_bitrate(output_root / uri)

        attrs = []
        for key, value in _STREAM_INF_ATTR.findall(pending[len(_STREAM_INF_PREFIX):]):
            if key == 'BANDWIDTH':
                attrs.append((key, str(peak)))
            elif key == 'AVERAGE-BANDWIDTH':
                attrs.append((key, str(average)))
            else:
                attrs.append((key, value))
        if average and not any(k == 'AVERAGE-BANDWIDTH' for k, _ in attrs):
            position = next((i for i, (k, _) in enumerate(attrs) if k == 'BANDWIDTH'), -1)
            attrs.insert(position + 1, ('AVERAGE-BANDWIDTH', str(average)))

        out.append(_STREAM_INF_PREFIX + ','.join(f'{k}={v}' for k, v in attrs))
        out.append(uri)

        resolution = next((v for k, v in attrs if k == 'RESOLUTION'), '')
        rows.append({
            'name': name,
            'playlist': uri,
            'resolution': resolution or f"0x{by_name.get(name, {}).get('height', 0)}",
            'bandwidth': peak,
        })
        pending = None

    master_path.write_text('\n'.join(out) + '\n', encoding='utf-8')

    order = {r['name']: i for i, r in enumerate(ladder)}
    rows.sort(key=lambda row: order.get(row['name'], len(order)))
    return rows


def transcode_video_asset(video_asset: VideoAsset) -> tuple[str, list[dict], int | None]:
    """
    Transcode one raw video into HLS renditions and return:
    - master playlist relative path (storage-relative, e.g. under AWS_LOCATION)
    - list of rendition metadata dicts
    - duration_seconds (if the source declares one)
    """
    relative_root = _build_output_relative_root(video_asset)
    started = time.monotonic()

    with tempfile.TemporaryDirectory() as tmp_dir, _local_input_path(video_asset) as input_path:
        output_root = Path(tmp_dir)
        probe = _probe_source(input_path)
        ladder = _select_ladder(probe['height'])

        logger.info(
            'Transcode start asset=%s source=%sx%s @%.3gfps %ss audio=%s ladder=%s encoder=%s',
            video_asset.id, probe['width'], probe['height'], probe['fps'] or 0,
            probe['duration'], 'yes' if probe['has_audio'] else 'NO',
            ','.join(r['name'] for r in ladder), _encoder_profile()['codec'],
        )

        _run_ffmpeg_command(_build_ladder_command(input_path, output_root, ladder, probe))
        variant_rows = _rewrite_master_playlist(output_root, ladder)
        segments = sum(1 for p in output_root.rglob('*.ts'))
        _upload_output_dir(output_root, relative_root)

    logger.info(
        'Transcode done asset=%s ladder=%s segments=%s in %.1fs',
        video_asset.id, len(variant_rows), segments, time.monotonic() - started,
    )

    renditions = [
        {
            'name': row['name'],
            'playlist': f"{relative_root}/{row['playlist']}",
            'resolution': row['resolution'],
            'bandwidth': row['bandwidth'],
        }
        for row in variant_rows
    ]
    return f'{relative_root}/master.m3u8', renditions, probe['duration']

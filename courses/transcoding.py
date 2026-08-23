import contextlib
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage

from courses.models import VideoAsset

logger = logging.getLogger(__name__)

DEFAULT_RENDITIONS = [
    {'name': '240p', 'height': 240, 'video_bitrate': '400k', 'audio_bitrate': '64k'},
    {'name': '360p', 'height': 360, 'video_bitrate': '800k', 'audio_bitrate': '96k'},
    {'name': '480p', 'height': 480, 'video_bitrate': '1400k', 'audio_bitrate': '128k'},
    {'name': '720p', 'height': 720, 'video_bitrate': '2500k', 'audio_bitrate': '128k'},
    {'name': '1080p', 'height': 1080, 'video_bitrate': '5000k', 'audio_bitrate': '192k'},
]


def _ffmpeg_binary() -> str:
    return getattr(settings, 'FFMPEG_BINARY_PATH', 'ffmpeg')


def _ffprobe_binary() -> str:
    configured = getattr(settings, 'FFPROBE_BINARY_PATH', '').strip()
    if configured:
        return configured

    ffmpeg_path = _ffmpeg_binary()
    ffmpeg_bin = Path(ffmpeg_path)
    if ffmpeg_bin.name.lower().startswith('ffmpeg'):
        sibling = ffmpeg_bin.with_name('ffprobe.exe' if ffmpeg_bin.suffix.lower() == '.exe' else 'ffprobe')
        return str(sibling)
    return 'ffprobe'


def _build_output_relative_root(video_asset: VideoAsset) -> str:
    lecture = video_asset.lecture
    course_slug = lecture.section.course.slug
    return f'courses/{course_slug}/lectures/{lecture.id}/hls/{video_asset.id}'


def _upload_output_dir(local_root: Path, relative_root: str) -> None:
    """
    Push every generated HLS file (playlists + segments) to the configured
    storage backend (S3 in production) under relative_root. ffmpeg can only
    write to a real local path, so generation happens in a temp dir first.
    """
    for local_file in sorted(local_root.rglob('*')):
        if not local_file.is_file():
            continue
        relative_name = f'{relative_root}/{local_file.relative_to(local_root).as_posix()}'
        if default_storage.exists(relative_name):
            default_storage.delete(relative_name)
        with local_file.open('rb') as fh:
            default_storage.save(relative_name, File(fh))


def _run_ffmpeg_command(command: list[str]) -> None:
    logger.info('Running FFmpeg command: %s', ' '.join(command))
    subprocess.run(command, check=True, capture_output=True, text=True)


def _probe_video_resolution(video_file: Path) -> tuple[int, int] | None:
    command = [
        _ffprobe_binary(),
        '-v',
        'error',
        '-select_streams',
        'v:0',
        '-show_entries',
        'stream=width,height',
        '-of',
        'csv=p=0:s=x',
        str(video_file),
    ]

    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        raw = (result.stdout or '').strip()
        if not raw:
            return None

        # ffprobe can return multiple lines for TS files; pick first valid WxH.
        matches = re.findall(r'(\d+)x(\d+)', raw)
        if not matches:
            return None
        width_str, height_str = matches[0]
        return int(width_str), int(height_str)
    except Exception:
        logger.warning('Failed to probe output resolution for file=%s', video_file, exc_info=True)
        return None


def _probe_video_duration_seconds(video_file: Path) -> int | None:
    command = [
        _ffprobe_binary(),
        '-v',
        'error',
        '-show_entries',
        'format=duration',
        '-of',
        'default=noprint_wrappers=1:nokey=1',
        str(video_file),
    ]

    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        raw = (result.stdout or '').strip()
        if not raw:
            return None
        duration = float(raw)
        if duration <= 0:
            return None
        # Ensure DB validation (> 0) always passes for short clips.
        return max(1, int(round(duration)))
    except Exception:
        logger.warning('Failed to probe video duration for file=%s', video_file, exc_info=True)
        return None


@contextlib.contextmanager
def _local_input_path(video_asset: VideoAsset):
    """
    Yield a local filesystem path for the video file. Storage backends that
    don't support absolute paths (e.g. object storage) raise NotImplementedError
    on .path() — fall back to streaming the file into a temp copy.
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

    suffix = Path(video_asset.video_file.name).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        with video_asset.video_file.open('rb') as remote_file:
            shutil.copyfileobj(remote_file, tmp)

    try:
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


def _write_master_playlist(output_root: Path, variant_rows: list[dict]) -> Path:
    master_path = output_root / 'master.m3u8'
    lines = ['#EXTM3U', '#EXT-X-VERSION:3']
    for row in variant_rows:
        lines.append(f"#EXT-X-STREAM-INF:BANDWIDTH={row['bandwidth']},RESOLUTION={row['resolution']}")
        lines.append(row['playlist_name'])
    master_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return master_path


def transcode_video_asset(video_asset: VideoAsset) -> tuple[str, list[dict], int | None]:
    """
    Transcode one raw video into HLS renditions and return:
    - master playlist relative path (storage-relative, e.g. under AWS_LOCATION)
    - list of rendition metadata dicts
    - duration_seconds (if probe succeeds)
    """
    ffmpeg_bin = _ffmpeg_binary()
    relative_root = _build_output_relative_root(video_asset)

    variant_rows = []
    with tempfile.TemporaryDirectory() as tmp_dir, _local_input_path(video_asset) as input_path:
        output_root = Path(tmp_dir)
        duration_seconds = _probe_video_duration_seconds(input_path)

        for rendition in DEFAULT_RENDITIONS:
            playlist_name = f"{rendition['name']}.m3u8"
            segment_pattern = output_root / f"{rendition['name']}_%03d.ts"
            playlist_path = output_root / playlist_name

            command = [
                ffmpeg_bin,
                '-y',
                '-i',
                str(input_path),
                '-vf',
                f"scale=-2:{rendition['height']}",
                '-c:a',
                'aac',
                '-ar',
                '48000',
                '-b:a',
                rendition['audio_bitrate'],
                '-c:v',
                'h264',
                '-profile:v',
                'main',
                '-crf',
                '20',
                '-g',
                '48',
                '-keyint_min',
                '48',
                '-sc_threshold',
                '0',
                '-b:v',
                rendition['video_bitrate'],
                '-maxrate',
                rendition['video_bitrate'],
                '-bufsize',
                str(int(rendition['video_bitrate'].replace('k', '')) * 2) + 'k',
                '-hls_time',
                '6',
                '-hls_playlist_type',
                'vod',
                '-hls_segment_filename',
                str(segment_pattern),
                str(playlist_path),
            ]
            _run_ffmpeg_command(command)

            bitrate_value = int(rendition['video_bitrate'].replace('k', '')) * 1000
            first_segment_path = output_root / f"{rendition['name']}_000.ts"
            probed_resolution = _probe_video_resolution(first_segment_path)
            if probed_resolution:
                resolution = f'{probed_resolution[0]}x{probed_resolution[1]}'
            else:
                resolution = f"0x{rendition['height']}"

            variant_rows.append(
                {
                    'name': rendition['name'],
                    'playlist_name': playlist_name,
                    'resolution': resolution,
                    'bandwidth': bitrate_value,
                }
            )

        _write_master_playlist(output_root, variant_rows)
        _upload_output_dir(output_root, relative_root)

    master_relative = f'{relative_root}/master.m3u8'
    renditions = [
        {
            'name': row['name'],
            'playlist': f"{relative_root}/{row['playlist_name']}",
            'resolution': row['resolution'],
            'bandwidth': row['bandwidth'],
        }
        for row in variant_rows
    ]
    return master_relative, renditions, duration_seconds

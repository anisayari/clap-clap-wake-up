from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .sound_library import get_media_library_dir

YOUTUBE_HOSTS = {
    "youtu.be",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
}


class YouTubeCacheError(RuntimeError):
    pass


def is_youtube_url(url: str | None) -> bool:
    return extract_youtube_video_id(url) is not None


def extract_youtube_video_id(url: str | None) -> str | None:
    text = str(url or "").strip()
    if not text:
        return None

    try:
        parsed = urlparse(text)
    except ValueError:
        return None

    host = parsed.netloc.casefold()
    if host.startswith("www."):
        short_host = host[4:]
    else:
        short_host = host

    candidate: str | None = None
    path_parts = [part for part in parsed.path.split("/") if part]
    if short_host == "youtu.be":
        candidate = path_parts[0] if path_parts else None
    elif short_host == "youtube.com" or host in YOUTUBE_HOSTS:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        elif path_parts and path_parts[0] in {"embed", "shorts", "live"}:
            candidate = path_parts[1] if len(path_parts) > 1 else None

    if not candidate:
        return None
    candidate = candidate.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if len(candidate) < 6 or any(ch not in allowed for ch in candidate):
        return None
    return candidate


def canonical_youtube_url(url: str | None) -> str | None:
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return None
    return f"https://www.youtube.com/watch?v={video_id}"


def get_youtube_cache_dir() -> Path:
    return get_media_library_dir() / "youtube-cache"


def cached_youtube_mp3_path(url: str | None) -> Path | None:
    cache_key = youtube_cache_key(url)
    if cache_key is None:
        return None
    return get_youtube_cache_dir() / f"{cache_key}.mp3"


def youtube_cache_key(url: str | None) -> str | None:
    canonical_url = canonical_youtube_url(url)
    if canonical_url is None:
        return None
    video_id = extract_youtube_video_id(canonical_url)
    if video_id:
        return f"youtube-{video_id}"
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
    return f"youtube-{digest}"


def ensure_youtube_audio_cached(url: str) -> Path:
    canonical_url = canonical_youtube_url(url)
    if canonical_url is None:
        raise YouTubeCacheError("URL is not a supported YouTube video link.")

    cache_dir = get_youtube_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    mp3_path = cached_youtube_mp3_path(canonical_url)
    if mp3_path is None:
        raise YouTubeCacheError("Unable to determine the YouTube cache path.")
    if mp3_path.exists() and mp3_path.stat().st_size > 0:
        return mp3_path

    ffmpeg_location = resolve_ffmpeg_location()

    try:
        yt_dlp = importlib.import_module("yt_dlp")
    except ImportError as exc:
        raise YouTubeCacheError("yt-dlp is not installed.") from exc

    cache_key = youtube_cache_key(canonical_url)
    if cache_key is None:
        raise YouTubeCacheError("Unable to determine the YouTube cache key.")
    _cleanup_stale_cache_files(cache_dir, cache_key)

    output_template = str(cache_dir / f"{cache_key}.%(ext)s")
    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
        "outtmpl": output_template,
        "prefer_ffmpeg": True,
        "ffmpeg_location": ffmpeg_location,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(canonical_url, download=True)
    except Exception as exc:
        raise YouTubeCacheError(f"Unable to cache YouTube audio: {exc}") from exc

    resolved_path = _resolve_downloaded_mp3(cache_dir, cache_key)
    if resolved_path is None:
        raise YouTubeCacheError("yt-dlp completed without producing a cached MP3.")

    metadata_path = cache_dir / f"{cache_key}.json"
    payload = {
        "source_url": url,
        "canonical_url": canonical_url,
        "video_id": extract_youtube_video_id(canonical_url),
        "cached_path": str(resolved_path),
        "title": (info or {}).get("title"),
        "webpage_url": (info or {}).get("webpage_url") or canonical_url,
        "downloaded_at": time.time(),
    }
    metadata_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return resolved_path


def resolve_ffmpeg_location() -> str:
    try:
        static_ffmpeg_run = importlib.import_module("static_ffmpeg.run")
    except ImportError:
        static_ffmpeg_run = None

    if static_ffmpeg_run is not None:
        try:
            ffmpeg_path, _ffprobe_path = static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
        except Exception as exc:
            raise YouTubeCacheError(f"Unable to provision bundled ffmpeg: {exc}") from exc
        return str(Path(ffmpeg_path).parent)

    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if ffmpeg_path and ffprobe_path:
        ffmpeg_parent = Path(ffmpeg_path).parent
        ffprobe_parent = Path(ffprobe_path).parent
        if ffmpeg_parent == ffprobe_parent:
            return str(ffmpeg_parent)
        return ffmpeg_path

    raise YouTubeCacheError("No ffmpeg/ffprobe runtime is available for YouTube MP3 extraction.")


def _cleanup_stale_cache_files(cache_dir: Path, cache_key: str) -> None:
    for path in cache_dir.glob(f"{cache_key}.*"):
        if path.is_file():
            path.unlink(missing_ok=True)


def _resolve_downloaded_mp3(cache_dir: Path, cache_key: str) -> Path | None:
    exact = cache_dir / f"{cache_key}.mp3"
    if exact.exists() and exact.stat().st_size > 0:
        return exact

    for path in sorted(cache_dir.glob(f"{cache_key}.*")):
        if path.suffix.casefold() == ".mp3" and path.exists() and path.stat().st_size > 0:
            return path
    return None

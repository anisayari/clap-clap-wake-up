from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import clap_wake.youtube_cache as youtube_cache_module
from clap_wake.youtube_cache import (
    YouTubeCacheError,
    cached_youtube_mp3_path,
    canonical_youtube_url,
    ensure_youtube_audio_cached,
    extract_youtube_video_id,
    is_youtube_url,
    resolve_ffmpeg_location,
)


class YouTubeCacheTests(unittest.TestCase):
    def test_extract_youtube_video_id_supports_common_url_shapes(self) -> None:
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/watch?v=abc123XYZ_0"),
            "abc123XYZ_0",
        )
        self.assertEqual(
            extract_youtube_video_id("https://youtu.be/abc123XYZ_0?t=15"),
            "abc123XYZ_0",
        )
        self.assertEqual(
            extract_youtube_video_id("https://music.youtube.com/watch?v=abc123XYZ_0&feature=share"),
            "abc123XYZ_0",
        )

    def test_is_youtube_url_rejects_non_youtube_hosts(self) -> None:
        self.assertFalse(is_youtube_url("https://example.com/watch?v=abc123XYZ_0"))
        self.assertIsNone(canonical_youtube_url("https://example.com/watch?v=abc123XYZ_0"))

    def test_cached_youtube_mp3_path_uses_video_id(self) -> None:
        with patch("clap_wake.youtube_cache.get_media_library_dir", return_value=Path("/tmp/media")):
            path = cached_youtube_mp3_path("https://youtu.be/abc123XYZ_0")

        self.assertEqual(path, Path("/tmp/media/youtube-cache/youtube-abc123XYZ_0.mp3"))

    def test_ensure_youtube_audio_cached_reuses_existing_mp3(self) -> None:
        with TemporaryDirectory() as temp_dir:
            media_dir = Path(temp_dir) / "media"
            mp3_path = media_dir / "youtube-cache" / "youtube-abc123XYZ_0.mp3"
            mp3_path.parent.mkdir(parents=True, exist_ok=True)
            mp3_path.write_bytes(b"cached-audio")

            with patch("clap_wake.youtube_cache.get_media_library_dir", return_value=media_dir):
                with patch(
                    "clap_wake.youtube_cache.importlib.import_module",
                    side_effect=AssertionError("yt-dlp should not be imported when cache exists"),
                ):
                    result = ensure_youtube_audio_cached("https://youtu.be/abc123XYZ_0")

        self.assertEqual(result, mp3_path)

    def test_ensure_youtube_audio_cached_requires_ffmpeg_for_new_downloads(self) -> None:
        with patch.object(youtube_cache_module.importlib, "import_module", side_effect=ImportError):
            with patch.object(youtube_cache_module.shutil, "which", return_value=None):
                with self.assertRaises(YouTubeCacheError):
                    resolve_ffmpeg_location()

    def test_resolve_ffmpeg_location_prefers_static_ffmpeg_package(self) -> None:
        fake_run_module = SimpleNamespace(
            get_or_fetch_platform_executables_else_raise=lambda: (
                "C:/tools/static-ffmpeg/bin/ffmpeg.exe",
                "C:/tools/static-ffmpeg/bin/ffprobe.exe",
            )
        )

        def import_module(name: str):
            if name == "static_ffmpeg.run":
                return fake_run_module
            raise AssertionError(f"Unexpected import: {name}")

        with patch("clap_wake.youtube_cache.importlib.import_module", side_effect=import_module):
            location = resolve_ffmpeg_location()

        self.assertEqual(Path(location), Path("C:/tools/static-ffmpeg/bin"))

    def test_ensure_youtube_audio_cached_downloads_mp3_and_writes_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            media_dir = Path(temp_dir) / "media"

            class FakeYoutubeDL:
                def __init__(self, options) -> None:
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

                def extract_info(self, url: str, download: bool = True) -> dict:
                    self.url = url
                    self.download = download
                    output_path = Path(self.options["outtmpl"].replace("%(ext)s", "mp3"))
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(b"downloaded-audio")
                    return {
                        "title": "Demo Track",
                        "webpage_url": url,
                    }

            fake_module = SimpleNamespace(YoutubeDL=FakeYoutubeDL)

            with patch("clap_wake.youtube_cache.get_media_library_dir", return_value=media_dir):
                with patch("clap_wake.youtube_cache.resolve_ffmpeg_location", return_value="/tmp/static-ffmpeg/bin"):
                    with patch("clap_wake.youtube_cache.importlib.import_module", return_value=fake_module):
                        result = ensure_youtube_audio_cached("https://youtu.be/abc123XYZ_0")

            metadata_path = media_dir / "youtube-cache" / "youtube-abc123XYZ_0.json"
            self.assertEqual(result, media_dir / "youtube-cache" / "youtube-abc123XYZ_0.mp3")
            self.assertTrue(result.exists())
            self.assertTrue(metadata_path.exists())


if __name__ == "__main__":
    unittest.main()

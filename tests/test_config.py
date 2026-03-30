from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from clap_wake.config import (
    DEFAULT_CONFIG,
    build_clap_config,
    default_media_choice,
    get_default_assets_audio_dir,
    get_default_workspace_dir,
    media_selection_is_ready,
    parse_selection,
    prompt_for_custom_targets,
    prompt_for_custom_target,
    prompt_for_targets_selection,
    seed_default_media_selection,
)
from clap_wake.sound_library import copy_audio_to_library, normalize_user_path


class ConfigParsingTests(unittest.TestCase):
    def test_parse_selection_ignores_empty_chunks(self) -> None:
        self.assertEqual(parse_selection("1, , 4,5", 5), [1, 4, 5])

    def test_parse_selection_supports_semicolons(self) -> None:
        self.assertEqual(parse_selection("2;3", 5), [2, 3])

    def test_parse_selection_supports_spaces_without_commas(self) -> None:
        self.assertEqual(parse_selection("1, 4 , 5 6", 6), [1, 4, 5, 6])

    def test_parse_selection_rejects_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            parse_selection("7", 5)

    def test_normalize_user_path_handles_drag_and_drop_style_quotes(self) -> None:
        self.assertEqual(
            normalize_user_path('"/tmp/test file.mp3"'),
            Path("/tmp/test file.mp3"),
        )

    def test_copy_audio_to_library_returns_distinct_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "demo.mp3"
            source.write_bytes(b"audio")
            library = Path(temp_dir) / "library"
            with patch("clap_wake.sound_library.get_media_library_dir", return_value=library):
                first = copy_audio_to_library(source)
                second = copy_audio_to_library(source)

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertNotEqual(first, second)

    def test_prompt_for_custom_target_url(self) -> None:
        answers = iter(["1", "Mon lien", "https://example.com"])
        with patch("builtins.input", side_effect=lambda prompt="": next(answers)):
            target = prompt_for_custom_target(1)

        self.assertEqual(target["id"], "custom_url")
        self.assertEqual(target["url"], "https://example.com")

    def test_prompt_for_custom_target_path_uses_drag_drop_path(self) -> None:
        answers = iter(["2", "Mon dossier", '"/tmp/demo folder"'])
        with patch("builtins.input", side_effect=lambda prompt="": next(answers)):
            target = prompt_for_custom_target(1)

        self.assertEqual(target["id"], "custom_path")
        self.assertEqual(target["path"], "/tmp/demo folder")

    def test_prompt_for_targets_selection_uses_inline_selector_when_available(self) -> None:
        with patch("clap_wake.config.terminal_ui_available", return_value=True):
            with patch("clap_wake.config.inline_multi_select", return_value=[2, 5]):
                selected = prompt_for_targets_selection("fr", {})

        self.assertEqual(selected, [2, 5])

    def test_prompt_for_targets_selection_reuses_existing_defaults_in_text_mode(self) -> None:
        existing_config = {
            "selected_targets": [
                {"id": "codex_desktop"},
                {"id": "chatgpt_web"},
            ]
        }
        with patch("clap_wake.config.terminal_ui_available", return_value=False):
            with patch("builtins.input", return_value=""):
                selected = prompt_for_targets_selection("fr", {}, existing_config=existing_config)

        self.assertEqual(selected, [1, 5])

    def test_build_clap_config_derives_cooldown_from_profile(self) -> None:
        microphone = dict(DEFAULT_CONFIG["microphone"])
        microphone["profile"] = {
            "pair_count": 4,
            "average_peak": 0.8,
            "average_rms": 0.2,
            "average_transient": 0.7,
            "average_score": 0.85,
            "average_shape_ratio": 0.9,
            "average_gap": 0.42,
            "gap_tolerance": 0.18,
            "match_tolerance": 0.55,
            "minimum_score": 0.3,
            "minimum_transient": 0.2,
        }
        microphone["trigger_cooldown_seconds"] = 9.0

        clap_config = build_clap_config(microphone)

        self.assertLess(clap_config.trigger_cooldown_seconds, 9.0)
        self.assertAlmostEqual(clap_config.trigger_cooldown_seconds, 1.38, places=2)

    def test_default_workspace_dir_uses_dedicated_subfolder(self) -> None:
        self.assertEqual(
            get_default_workspace_dir(Path("/tmp/demo")),
            Path("/tmp/demo/working-directory-start-up"),
        )

    def test_default_media_choice_reuses_existing_single_file_selection(self) -> None:
        media = dict(DEFAULT_CONFIG["media"])
        media["mode"] = "single_file"
        media["selected_sound_path"] = "/tmp/song.mp3"
        self.assertEqual(default_media_choice(media), "1")
        self.assertTrue(media_selection_is_ready(media, "1"))

    def test_seed_default_media_selection_prefers_local_highway_mp3(self) -> None:
        media = dict(DEFAULT_CONFIG["media"])
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "highway.mp3"
            path.write_bytes(b"")
            with patch("clap_wake.config.find_highway_mp3", return_value=path):
                seed_default_media_selection(media)

        self.assertEqual(media["mode"], "single_file")
        self.assertEqual(media["selected_sound_path"], str(path))

    def test_seed_default_media_selection_defaults_to_assets_folder_when_highway_missing(self) -> None:
        media = dict(DEFAULT_CONFIG["media"])
        with patch("clap_wake.config.find_highway_mp3", return_value=None):
            seed_default_media_selection(media)

        self.assertEqual(media["mode"], "auto_downloads")
        self.assertEqual(media["selected_folder_path"], str(get_default_assets_audio_dir()))

    def test_prompt_for_custom_targets_keeps_existing_targets_by_default(self) -> None:
        existing = [{"id": "custom_url", "label": "Docs", "url": "https://example.com"}]
        answers = iter(["", ""])
        with patch("builtins.input", side_effect=lambda prompt="": next(answers)):
            targets = prompt_for_custom_targets("fr", existing)

        self.assertEqual(targets, existing)


if __name__ == "__main__":
    unittest.main()

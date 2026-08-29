import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from clap_wake.config import DEFAULT_CONFIG
from clap_wake.realtime_localhost import (
    build_triggered_welcome_url,
    ensure_realtime_server,
    request_path,
    RealtimeWelcomeServer,
    build_app_js,
    build_index_html,
    mint_ephemeral_token,
    stop_realtime_server,
)


class RealtimeLocalhostTests(unittest.TestCase):
    def build_config(self) -> dict:
        return {
            "workspace_dir": "/tmp",
            "realtime": dict(DEFAULT_CONFIG["realtime"]),
        }

    def test_missing_api_key_raises(self) -> None:
        config = self.build_config()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                mint_ephemeral_token(config)

    def test_uses_env_api_key(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"value": "ephemeral"}).encode("utf-8")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            with patch("clap_wake.realtime_localhost.urlopen", return_value=FakeResponse()) as urlopen_mock:
                payload = mint_ephemeral_token(self.build_config())

        self.assertEqual(payload["value"], "ephemeral")
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer sk-test")

    def test_uses_workspace_dotenv_key(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"value": "ephemeral"}).encode("utf-8")

        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, ".env").write_text('OPENAI_API_KEY="sk-dotenv"\n', encoding="utf-8")
            config = self.build_config()
            config["workspace_dir"] = temp_dir
            with patch.dict(os.environ, {}, clear=True):
                with patch("clap_wake.realtime_localhost.urlopen", return_value=FakeResponse()) as urlopen_mock:
                    payload = mint_ephemeral_token(config)

        self.assertEqual(payload["value"], "ephemeral")
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer sk-dotenv")

    def test_app_js_uses_audio_turn_detection_and_audio_only_output(self) -> None:
        server = RealtimeWelcomeServer(config=self.build_config(), port=8767)
        script = build_app_js(server)

        self.assertIn('turn_detection', script)
        self.assertIn('input: {', script)
        self.assertIn('output_modalities: ["audio"]', script)
        self.assertNotIn('output_modalities: ["audio", "text"]', script)
        self.assertNotIn('semantic_vad', script)

    def test_realtime_page_and_script_include_streaming_transcript_and_bounded_log(self) -> None:
        server = RealtimeWelcomeServer(config=self.build_config(), port=8767)
        html = build_index_html(server.public_config())
        script = build_app_js(server)

        self.assertIn('id="liveTranscript"', html)
        self.assertIn("MAX_LOG_LINES = 160", script)
        self.assertIn("appendTranscript(event.delta)", script)

    def test_build_triggered_welcome_url_adds_autostart_query(self) -> None:
        url = build_triggered_welcome_url("http://127.0.0.1:8766/")

        self.assertIn("/welcome/", url)
        self.assertIn("autostart=1", url)
        self.assertIn("source=clap", url)

    def test_build_triggered_welcome_url_keeps_existing_welcome_route(self) -> None:
        url = build_triggered_welcome_url("http://127.0.0.1:8766/welcome/")

        self.assertEqual(url.count("/welcome/"), 1)
        self.assertIn("autostart=1", url)

    def test_request_path_strips_query_string(self) -> None:
        self.assertEqual(request_path("/welcome/?autostart=1&source=clap"), "/welcome/")

    def test_standalone_server_serves_welcome_route(self) -> None:
        url = ensure_realtime_server(self.build_config())
        try:
            with urlopen(f"{url.rstrip('/')}/welcome/?autostart=1&source=clap", timeout=2) as response:
                body = response.read().decode("utf-8")
        finally:
            stop_realtime_server()

        self.assertIn("Clap Wake Up Voice", body)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from clap_wake.media import find_highway_mp3


class MediaLookupTests(unittest.TestCase):
    def test_prefers_highway_to_hell_match(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            (base / "random.mp3").write_bytes(b"")
            expected = base / "ACDC - Highway To Hell.mp3"
            expected.write_bytes(b"")

            match = find_highway_mp3(base)

            self.assertEqual(match, expected)

    def test_searches_recursively(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            nested = base / "nested"
            nested.mkdir()
            expected = nested / "highway_acdc_live.mp3"
            expected.write_bytes(b"")

            match = find_highway_mp3(base)

            self.assertEqual(match, expected)


if __name__ == "__main__":
    unittest.main()

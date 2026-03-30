import unittest

import numpy as np

from clap_wake.audio import (
    ClapFeatures,
    DoubleClapSample,
    build_double_clap_profile,
    extract_clap_features,
    matches_double_clap,
    matches_single_clap,
    profile_from_dict,
    profile_to_dict,
)


class AudioProfileTests(unittest.TestCase):
    def test_extract_clap_features_captures_transient(self) -> None:
        frame = np.array([0.0, 0.05, 0.92, -0.08, 0.01], dtype=np.float32)
        features = extract_clap_features(frame)

        self.assertGreater(features.peak, 0.8)
        self.assertGreater(features.transient, 0.8)
        self.assertGreater(features.score, 0.8)

    def test_profile_round_trip(self) -> None:
        samples = [
            DoubleClapSample(
                first=ClapFeatures(peak=0.8, rms=0.22, transient=0.85, score=1.1, shape_ratio=1.06),
                second=ClapFeatures(peak=0.78, rms=0.2, transient=0.81, score=1.05, shape_ratio=1.03),
                gap=0.31,
            ),
            DoubleClapSample(
                first=ClapFeatures(peak=0.83, rms=0.24, transient=0.88, score=1.12, shape_ratio=1.05),
                second=ClapFeatures(peak=0.81, rms=0.21, transient=0.84, score=1.08, shape_ratio=1.04),
                gap=0.28,
            ),
            DoubleClapSample(
                first=ClapFeatures(peak=0.79, rms=0.2, transient=0.83, score=1.03, shape_ratio=1.04),
                second=ClapFeatures(peak=0.82, rms=0.23, transient=0.87, score=1.11, shape_ratio=1.06),
                gap=0.33,
            ),
        ]
        profile = build_double_clap_profile(samples)
        payload = profile_to_dict(profile)
        restored = profile_from_dict(payload)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.pair_count, 3)
        self.assertAlmostEqual(restored.average_score, profile.average_score)

    def test_profile_match_accepts_similar_clap(self) -> None:
        samples = [
            DoubleClapSample(
                first=ClapFeatures(peak=0.8, rms=0.22, transient=0.85, score=1.1, shape_ratio=1.06),
                second=ClapFeatures(peak=0.78, rms=0.2, transient=0.81, score=1.05, shape_ratio=1.03),
                gap=0.31,
            ),
            DoubleClapSample(
                first=ClapFeatures(peak=0.83, rms=0.24, transient=0.88, score=1.12, shape_ratio=1.05),
                second=ClapFeatures(peak=0.81, rms=0.21, transient=0.84, score=1.08, shape_ratio=1.04),
                gap=0.28,
            ),
            DoubleClapSample(
                first=ClapFeatures(peak=0.79, rms=0.2, transient=0.83, score=1.03, shape_ratio=1.04),
                second=ClapFeatures(peak=0.82, rms=0.23, transient=0.87, score=1.11, shape_ratio=1.06),
                gap=0.33,
            ),
        ]
        profile = build_double_clap_profile(samples)

        similar_first = ClapFeatures(peak=0.82, rms=0.215, transient=0.86, score=1.09, shape_ratio=1.04)
        similar_second = ClapFeatures(peak=0.8, rms=0.205, transient=0.83, score=1.06, shape_ratio=1.03)
        unrelated = ClapFeatures(peak=0.35, rms=0.3, transient=0.15, score=0.54, shape_ratio=0.42)

        self.assertTrue(matches_single_clap(similar_first, profile))
        self.assertTrue(matches_double_clap(similar_first, similar_second, 0.3, profile))
        self.assertFalse(matches_single_clap(unrelated, profile))


if __name__ == "__main__":
    unittest.main()

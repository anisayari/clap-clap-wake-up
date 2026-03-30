from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from threading import Event
from typing import Callable

import numpy as np


@dataclass
class ClapFeatures:
    peak: float
    rms: float
    transient: float
    score: float
    shape_ratio: float


@dataclass
class DoubleClapProfile:
    pair_count: int
    average_peak: float
    average_rms: float
    average_transient: float
    average_score: float
    average_shape_ratio: float
    average_gap: float
    gap_tolerance: float
    match_tolerance: float
    minimum_score: float
    minimum_transient: float


@dataclass
class DoubleClapSample:
    first: ClapFeatures
    second: ClapFeatures
    gap: float


@dataclass
class ClapConfig:
    sample_rate: int
    blocksize: int
    absolute_peak_threshold: float
    relative_peak_multiplier: float
    minimum_clap_gap_seconds: float
    double_clap_max_gap_seconds: float
    trigger_cooldown_seconds: float
    profile: DoubleClapProfile | None = None


def recommended_trigger_cooldown_seconds(
    profile: DoubleClapProfile | None,
    double_clap_max_gap_seconds: float,
    fallback: float = 2.0,
) -> float:
    if profile is None:
        return fallback

    average_gap = max(profile.average_gap, 0.18)
    spread = max(profile.gap_tolerance, 0.14)
    cooldown = max(
        1.10,
        (average_gap * 2.6) + spread,
        double_clap_max_gap_seconds + spread + 0.35,
    )
    return round(min(cooldown, 4.0), 2)


class DoubleClapDetector:
    def __init__(self, config: ClapConfig, on_trigger: Callable[[], None]) -> None:
        self.config = config
        self.on_trigger = on_trigger
        self.noise_floor = 0.015
        self.pending_clap_at = 0.0
        self.pending_features: ClapFeatures | None = None
        self.last_peak_at = 0.0
        self.last_trigger_at = 0.0

    def process(self, frame: np.ndarray, now: float) -> bool:
        features = extract_clap_features(frame)
        score = features.score

        quiet_threshold = max(self.noise_floor * 2.0, self.config.absolute_peak_threshold * 0.65)
        if score < quiet_threshold:
            self.noise_floor = (self.noise_floor * 0.98) + (score * 0.02)

        dynamic_threshold = max(
            self.config.absolute_peak_threshold,
            self.noise_floor * self.config.relative_peak_multiplier,
        )

        if score < dynamic_threshold:
            self._expire_pending_clap(now)
            return False

        if now - self.last_peak_at < self.config.minimum_clap_gap_seconds:
            return False
        self.last_peak_at = now

        if now - self.last_trigger_at < self.config.trigger_cooldown_seconds:
            return False

        if self.config.profile and not matches_single_clap(features, self.config.profile):
            self._expire_pending_clap(now)
            return False

        if self.pending_clap_at and self.pending_features is not None:
            gap = now - self.pending_clap_at
            if gap > self.config.double_clap_max_gap_seconds:
                self.pending_clap_at = now
                self.pending_features = features
                return False

            if gap >= self.config.minimum_clap_gap_seconds:
                if not self.config.profile or matches_double_clap(
                    self.pending_features,
                    features,
                    gap,
                    self.config.profile,
                ):
                    self.pending_clap_at = 0.0
                    self.pending_features = None
                    self.last_trigger_at = now
                    return True

        self.pending_clap_at = now
        self.pending_features = features
        return False

    def _expire_pending_clap(self, now: float) -> None:
        if self.pending_clap_at and now - self.pending_clap_at > self.config.double_clap_max_gap_seconds:
            self.pending_clap_at = 0.0
            self.pending_features = None


def extract_clap_features(frame: np.ndarray) -> ClapFeatures:
    mono = frame.reshape(-1)
    peak = float(np.max(np.abs(mono)))
    rms = float(math.sqrt(float(np.mean(np.square(mono)))))
    transient = float(np.max(np.abs(np.diff(mono, prepend=mono[:1]))))
    score = max(peak, rms * 1.8, transient * 1.5)
    shape_ratio = transient / max(peak, 1e-6)
    return ClapFeatures(
        peak=peak,
        rms=rms,
        transient=transient,
        score=score,
        shape_ratio=shape_ratio,
    )


def matches_single_clap(features: ClapFeatures, profile: DoubleClapProfile) -> bool:
    if features.score < profile.minimum_score:
        return False
    if features.transient < profile.minimum_transient:
        return False
    return normalized_feature_distance(features, profile) <= profile.match_tolerance


def matches_double_clap(
    first: ClapFeatures,
    second: ClapFeatures,
    gap: float,
    profile: DoubleClapProfile,
) -> bool:
    if not matches_single_clap(first, profile):
        return False
    if not matches_single_clap(second, profile):
        return False
    gap_delta = abs(gap - profile.average_gap)
    return gap_delta <= profile.gap_tolerance


def normalized_feature_distance(features: ClapFeatures, profile: DoubleClapProfile) -> float:
    peak_gap = abs(features.peak - profile.average_peak) / max(profile.average_peak, 1e-6)
    rms_gap = abs(features.rms - profile.average_rms) / max(profile.average_rms, 1e-6)
    transient_gap = abs(features.transient - profile.average_transient) / max(
        profile.average_transient, 1e-6
    )
    shape_gap = abs(features.shape_ratio - profile.average_shape_ratio) / max(
        profile.average_shape_ratio, 1e-6
    )
    return (peak_gap * 0.22) + (rms_gap * 0.14) + (transient_gap * 0.42) + (shape_gap * 0.22)


def build_double_clap_profile(samples: list[DoubleClapSample]) -> DoubleClapProfile:
    if not samples:
        raise ValueError("No double clap samples were captured.")

    clap_samples = [sample.first for sample in samples] + [sample.second for sample in samples]
    peaks = np.array([sample.peak for sample in clap_samples], dtype=np.float32)
    rms_values = np.array([sample.rms for sample in clap_samples], dtype=np.float32)
    transients = np.array([sample.transient for sample in clap_samples], dtype=np.float32)
    scores = np.array([sample.score for sample in clap_samples], dtype=np.float32)
    shape_ratios = np.array([sample.shape_ratio for sample in clap_samples], dtype=np.float32)
    gaps = np.array([sample.gap for sample in samples], dtype=np.float32)

    return DoubleClapProfile(
        pair_count=len(samples),
        average_peak=float(np.mean(peaks)),
        average_rms=float(np.mean(rms_values)),
        average_transient=float(np.mean(transients)),
        average_score=float(np.mean(scores)),
        average_shape_ratio=float(np.mean(shape_ratios)),
        average_gap=float(np.mean(gaps)),
        gap_tolerance=max(0.14, float(np.std(gaps)) * 2.6),
        match_tolerance=max(0.52, float(np.std(scores)) * 2.4),
        minimum_score=max(0.12, float(np.percentile(scores, 15)) * 0.82),
        minimum_transient=max(0.08, float(np.percentile(transients, 15)) * 0.82),
    )


def profile_to_dict(profile: DoubleClapProfile) -> dict:
    return {
        "pair_count": profile.pair_count,
        "average_peak": profile.average_peak,
        "average_rms": profile.average_rms,
        "average_transient": profile.average_transient,
        "average_score": profile.average_score,
        "average_shape_ratio": profile.average_shape_ratio,
        "average_gap": profile.average_gap,
        "gap_tolerance": profile.gap_tolerance,
        "match_tolerance": profile.match_tolerance,
        "minimum_score": profile.minimum_score,
        "minimum_transient": profile.minimum_transient,
    }


def profile_from_dict(payload: dict | None) -> DoubleClapProfile | None:
    if not payload:
        return None
    pair_count = int(payload.get("pair_count", payload.get("sample_count", 0)))
    return DoubleClapProfile(
        pair_count=pair_count,
        average_peak=float(payload["average_peak"]),
        average_rms=float(payload["average_rms"]),
        average_transient=float(payload["average_transient"]),
        average_score=float(payload["average_score"]),
        average_shape_ratio=float(payload["average_shape_ratio"]),
        average_gap=float(payload.get("average_gap", 0.32)),
        gap_tolerance=float(payload.get("gap_tolerance", 0.18)),
        match_tolerance=float(payload["match_tolerance"]),
        minimum_score=float(payload["minimum_score"]),
        minimum_transient=float(payload["minimum_transient"]),
    )


def calibrate_double_clap_profile(
    config: ClapConfig,
    target_pairs: int = 4,
    timeout_seconds: float = 20.0,
    on_progress: Callable[[int, int], None] | None = None,
) -> DoubleClapProfile:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice is required for double clap calibration. Install project dependencies first."
        ) from exc

    captured: list[DoubleClapSample] = []
    done = Event()
    start_time = time.monotonic()
    last_peak_at = 0.0
    noise_floor = 0.015
    pending_at = 0.0
    pending_features: ClapFeatures | None = None

    def callback(indata, frames, time_info, status) -> None:
        nonlocal last_peak_at, noise_floor, pending_at, pending_features
        del frames, time_info
        if status or done.is_set():
            return

        frame = np.copy(indata[:, 0])
        now = time.monotonic()
        features = extract_clap_features(frame)
        quiet_threshold = max(noise_floor * 2.0, config.absolute_peak_threshold * 0.65)
        if features.score < quiet_threshold:
            noise_floor = (noise_floor * 0.98) + (features.score * 0.02)
            if pending_at and now - pending_at > config.double_clap_max_gap_seconds:
                pending_at = 0.0
                pending_features = None
            return

        dynamic_threshold = max(
            config.absolute_peak_threshold,
            noise_floor * config.relative_peak_multiplier,
        )

        if features.score < dynamic_threshold:
            return
        if now - last_peak_at < config.minimum_clap_gap_seconds:
            return
        last_peak_at = now

        if pending_at and pending_features is not None:
            gap = now - pending_at
            if config.minimum_clap_gap_seconds <= gap <= config.double_clap_max_gap_seconds:
                captured.append(DoubleClapSample(first=pending_features, second=features, gap=gap))
                pending_at = 0.0
                pending_features = None
                if on_progress:
                    on_progress(len(captured), target_pairs)
                if len(captured) >= target_pairs:
                    done.set()
                return

        pending_at = now
        pending_features = features

    with sd.InputStream(
        samplerate=config.sample_rate,
        channels=1,
        dtype="float32",
        blocksize=config.blocksize,
        callback=callback,
    ):
        while not done.is_set():
            if time.monotonic() - start_time > timeout_seconds:
                break
            time.sleep(0.05)

    if len(captured) < max(2, min(target_pairs, 3)):
        raise RuntimeError(
            f"Calibration incomplete: {len(captured)} double clap(s) captures, au moins 3 necessaires."
        )
    return build_double_clap_profile(captured)


def run_microphone_loop(
    config: ClapConfig,
    on_trigger: Callable[[], None],
    stop_event: Event | None = None,
) -> None:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice is required for clap detection. Install project dependencies first."
        ) from exc

    detector = DoubleClapDetector(config=config, on_trigger=on_trigger)

    def callback(indata, frames, time_info, status) -> None:
        del frames, time_info
        if status:
            return

        frame = np.copy(indata[:, 0])
        now = time.monotonic()
        if detector.process(frame, now):
            thread = threading.Thread(target=on_trigger, daemon=True)
            thread.start()

    with sd.InputStream(
        samplerate=config.sample_rate,
        channels=1,
        dtype="float32",
        blocksize=config.blocksize,
        callback=callback,
    ):
        while stop_event is None or not stop_event.is_set():
            time.sleep(0.25)

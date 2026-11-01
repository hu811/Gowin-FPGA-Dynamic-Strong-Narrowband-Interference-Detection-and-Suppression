"""Frozen digital test sources. Labels are used only for offline evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile

FS = 32000
SEED = 20261019
SCENARIOS = ("dynamic", "fixed", "offbin", "quiet", "quiet_peak020", "quiet_peak035", "weak")


def make_fixture(capacity: int, scenario: str = "dynamic", seed: int = SEED,
                 seconds: float = 2.0) -> tuple[np.ndarray, dict, dict]:
    if capacity not in (3, 4) or scenario not in SCENARIOS:
        raise ValueError("Unsupported capacity/scenario")
    if seconds < 1.5:
        raise ValueError("Fixtures need at least 1.5 seconds")
    count = int(round(seconds * FS))
    t = np.arange(count) / FS
    rng = np.random.default_rng(seed)
    # Smooth, spread random spectrum; no legal strong target tone is assumed.
    target = signal.sosfilt(signal.butter(6, (600, 7400), btype="bandpass", fs=FS,
                                        output="sos"), rng.standard_normal(count))
    target *= 0.01 / np.sqrt(np.mean(target**2))
    if scenario in ("quiet_peak020", "quiet_peak035"):
        target *= (0.20 if scenario.endswith("020") else 0.35) / np.max(np.abs(target))
    centers = [2800.0, 2925.0, 1750.0] if capacity == 3 else [2800.0, 2925.0, 5025.0, 5150.0]
    if scenario == "offbin":
        centers = [f + offset for f, offset in zip(centers, (1.7, 1.7, -2.3, -2.3))]
    mids = [(centers[0] + centers[1]) / 2]
    if capacity == 4:
        mids.append((centers[2] + centers[3]) / 2)
    components = {"target": target.copy()}
    # Probe RMS is 20 dB below the random target RMS; it is in the actual input.
    probes = np.zeros(count)
    if not scenario.startswith("quiet"):
        for i, frequency in enumerate(mids):
            probe = np.sqrt(2) * 0.001 * np.sin(2*np.pi*frequency*t + 0.37 + i)
            components[f"probe_{i}"] = probe
            probes += probe
    clean = target + probes
    clean_power = float(np.mean(clean**2))
    js_db = -6.0 if scenario == "weak" else 20.0
    amplitude = np.sqrt(2 * clean_power * 10**(js_db / 10))
    events = []
    for i, frequency in enumerate(centers):
        if scenario.startswith("quiet"):
            start, end = seconds, seconds
        elif scenario == "fixed":
            start, end = 0.2, seconds - 0.2
        elif i < 2:
            start, end = (0.2 + (0.008 if i == 1 else 0)), seconds * 0.65
        else:
            start, end = seconds * 0.4 + (0.008 if i == 3 else 0), seconds - 0.2
        gate = (t >= start) & (t < end)
        phase = float(rng.uniform(-np.pi, np.pi))
        wave = amplitude * np.sin(2*np.pi*frequency*t + phase) * gate
        components[f"interference_{i}"] = wave
        events.append({"center": i, "frequency_hz": frequency, "start_s": start,
                       "end_s": end, "phase_rad": phase,
                       "nominal_active_js_db_vs_clean_fullband": js_db})
    mixed = clean + sum((components[f"interference_{i}"] for i in range(capacity)),
                        np.zeros(count))
    # A single front-end gain applies to all components, preserving J/S.
    scale = min(1.0, 0.85 / max(float(np.max(np.abs(mixed))), 1e-12))
    mixed *= scale
    for name in components:
        components[name] *= scale
    samples = np.rint(mixed * 32767).astype(np.int16)
    metadata = {
        "schema": "v19-fixture-1", "capacity": capacity, "scenario": scenario,
        "seed": seed, "sample_rate_hz": FS, "samples": count, "seconds": count / FS,
        "frequencies_hz": centers, "probe_frequencies_hz": mids,
        "events": events, "front_end_scale": scale,
        "target_rms_after_scale": float(np.sqrt(np.mean(components["target"]**2))),
        "target_peak_after_scale": float(np.max(np.abs(components["target"]))),
        "mixed_max_abs_int16": int(np.max(np.abs(samples.astype(np.int32)))),
        "input_sha256": hashlib.sha256(samples.astype("<i2").tobytes()).hexdigest(),
        "online_truth_access": False,
        "notes": [
            "Frequency/time/component labels are exclusively source/offline evaluation data.",
            "No labels, detector estimates, coefficients, or clean target are sent to the FPGA.",
            "quiet_peak020/035 mean target PEAK 0.20/0.35 FS, not historical amplitude/RMS definitions.",
            "Band-limited random input is a controlled applicability test, not a communication waveform/BER test.",
            "Weak case is a negative control; 20 dB suppression is not promised.",
        ],
    }
    for event in events:
        i = event["center"]
        event["tone_peak_after_scale"] = float(amplitude * scale)
        segment = slice(int(round(event["start_s"] * FS)), int(round(event["end_s"] * FS)))
        if segment.stop > segment.start:
            power = np.mean(components[f"interference_{i}"][segment]**2)
            target_power = np.mean((components["target"] + probes * scale)[segment]**2)
            event["actual_active_js_db"] = float(10*np.log10(power / target_power))
        else:
            event["actual_active_js_db"] = None
    return samples, metadata, components


def save_fixture(directory: Path, capacity: int, scenario: str, seed: int = SEED,
                 seconds: float = 2.0) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    samples, metadata, components = make_fixture(capacity, scenario, seed, seconds)
    stem = directory / f"c{capacity}_{scenario}_seed{seed}"
    np.save(stem.with_suffix(".npy"), samples)
    wavfile.write(stem.with_suffix(".wav"), FS, samples)
    stem.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    np.savez_compressed(str(stem) + "_components.npz", **components)
    return stem.with_suffix(".npy")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int, choices=(3, 4), required=True)
    parser.add_argument("--scenario", choices=(*SCENARIOS, "all"), default="all")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()
    for scenario in SCENARIOS if args.scenario == "all" else (args.scenario,):
        print(save_fixture(args.output, args.capacity, scenario, args.seed, args.seconds))


if __name__ == "__main__":
    main()

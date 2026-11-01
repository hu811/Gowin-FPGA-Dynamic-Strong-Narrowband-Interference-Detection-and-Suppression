"""Offline, reproducible int16 scene generation for the V31 digital input link.

No serial communication or detection/filtering is performed here. ``target`` is
the complete clean reference (including optional weak probes). Only the returned
int16 input is intended for the board; all component labels are offline data.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
from numbers import Integral, Real
from pathlib import Path
import wave

import numpy as np

FS = 32_000
MAX_SAMPLES = 1_048_575
HEADROOM = 0.95
DETECTOR_RANGE_HZ = (1_000.0, 7_000.0)
GENERATOR_VERSION = "v31-scene-1"


def default_config(capacity: int) -> dict:
    """Return a fresh C3 one-pair+single or C4 two-pair editable configuration."""
    if isinstance(capacity, bool) or not isinstance(capacity, Integral) or capacity not in (3, 4):
        raise ValueError("capacity must be 3 or 4")
    frequencies = (2311.3, 2436.3, 4972.7, 5097.7)
    starts = (0.25, 0.251, 0.258, 0.259)
    stops = (1.30, 1.40, 1.55, 1.65)
    phases = (-0.3, 0.8, 1.2, -1.0)
    events = []
    for i in range(4):
        events.append(dict(enabled=i < int(capacity), frequency_hz=frequencies[i],
            end_frequency_hz=frequencies[i], mode="fixed", start_s=starts[i],
            change_s=(starts[i] + stops[i]) / 2, end_s=stops[i], js_db=20.0,
            phase_rad=phases[i]))
    return dict(capacity=int(capacity), seconds=2.0, seed=26102131,
        target_low_hz=600.0, target_high_hz=7400.0, target_rms=0.01,
        noise_rms=0.001, events=events, use_probes=False)


def _number(value, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name}: must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name}: NaN and infinity are not allowed")
    return value


def _validated(config: dict) -> tuple[dict, int]:
    if not isinstance(config, dict):
        raise ValueError("config must be a dictionary")
    required = {"capacity", "seconds", "seed", "target_low_hz", "target_high_hz",
                "target_rms", "noise_rms", "events"}
    missing = required - set(config)
    if missing:
        raise ValueError("missing config fields: " + ", ".join(sorted(missing)))
    unknown = set(config) - required - {"use_probes"}
    if unknown:
        raise ValueError("unknown config fields: " + ", ".join(sorted(unknown)))
    capacity = config["capacity"]
    if isinstance(capacity, (bool, np.bool_)) or not isinstance(capacity, Integral) or capacity not in (3, 4):
        raise ValueError("capacity must be 3 or 4")
    seconds = _number(config["seconds"], "seconds")
    if not 0 < seconds <= MAX_SAMPLES / FS:
        raise ValueError(f"seconds must be >0 and <= {MAX_SAMPLES / FS:.8f}")
    samples = int(round(seconds * FS))
    if not 1 <= samples <= MAX_SAMPLES:
        raise ValueError(f"rounded sample count must be 1..{MAX_SAMPLES}")
    seed = config["seed"]
    if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, Integral) or not 0 <= seed < 2**64:
        raise ValueError("seed must be an integer in 0..2^64-1")
    low = _number(config["target_low_hz"], "target_low_hz")
    high = _number(config["target_high_hz"], "target_high_hz")
    if not 0 <= low < high <= FS / 2:
        raise ValueError("target band must satisfy 0 <= low < high <= 16000 Hz")
    target_rms = _number(config["target_rms"], "target_rms")
    noise_rms = _number(config["noise_rms"], "noise_rms")
    if target_rms < 0 or noise_rms < 0:
        raise ValueError("target_rms and noise_rms must be nonnegative")
    probes = config.get("use_probes", False)
    if not isinstance(probes, (bool, np.bool_)):
        raise ValueError("use_probes must be boolean")
    events = config["events"]
    if not isinstance(events, list) or len(events) != 4:
        raise ValueError("events must contain exactly four dictionaries")
    numeric_names = ("frequency_hz", "end_frequency_hz", "start_s", "change_s", "end_s", "js_db", "phase_rad")
    names = set(numeric_names) | {"enabled", "mode"}
    normalized = []
    for i, source in enumerate(events):
        prefix = f"events[{i}]"
        if not isinstance(source, dict) or set(source) != names:
            raise ValueError(f"{prefix}: fields must be exactly {sorted(names)}")
        if not isinstance(source["enabled"], (bool, np.bool_)):
            raise ValueError(f"{prefix}.enabled must be boolean")
        if source["mode"] not in ("fixed", "step", "linear"):
            raise ValueError(f"{prefix}.mode must be fixed, step or linear")
        e = {key: _number(source[key], prefix + "." + key) for key in numeric_names}
        e.update(enabled=bool(source["enabled"]), mode=source["mode"])
        if not 0 < e["frequency_hz"] < FS / 2 or not 0 < e["end_frequency_hz"] < FS / 2:
            raise ValueError(f"{prefix}: frequencies must be strictly between 0 and 16000 Hz")
        if not 0 <= e["start_s"] < e["end_s"]:
            raise ValueError(f"{prefix}: require 0 <= start_s < end_s")
        if e["mode"] == "step" and not e["start_s"] < e["change_s"] < e["end_s"]:
            raise ValueError(f"{prefix}: step change_s must lie strictly inside the event")
        if e["enabled"]:
            if e["end_s"] > seconds:
                raise ValueError(f"{prefix}: enabled event end_s must be <= seconds")
            start, stop = (int(round(e[k] * FS)) for k in ("start_s", "end_s"))
            if not 0 <= start < stop <= samples:
                raise ValueError(f"{prefix}: rounded event interval must contain at least one sample")
            if e["mode"] == "step" and not start < int(round(e["change_s"] * FS)) < stop:
                raise ValueError(f"{prefix}: step change must leave samples on both sides")
        if e["enabled"] and target_rms == 0:
            raise ValueError(f"{prefix}: J/S is undefined when clean target RMS is zero")
        normalized.append(e)
    return dict(capacity=int(capacity), seconds=seconds, seed=int(seed), target_low_hz=low,
        target_high_hz=high, target_rms=target_rms, noise_rms=noise_rms,
        events=normalized, use_probes=bool(probes)), samples


def _rms(values: np.ndarray) -> float:
    peak = float(np.max(np.abs(values))) if values.size else 0.0
    if peak == 0:
        return 0.0
    if not math.isfinite(peak):
        raise ValueError("generated component is not finite; reduce requested level")
    return peak * float(np.sqrt(np.mean((values / peak) ** 2)))


def _at_rms(values: np.ndarray, requested: float, name: str) -> np.ndarray:
    if requested == 0:
        return np.zeros(values.size, dtype=np.float64)
    actual = _rms(values)
    if actual == 0:
        raise ValueError(f"{name}: cannot normalize an empty/zero waveform")
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        result = (values / actual) * requested
    if not np.all(np.isfinite(result)) or _rms(result) == 0:
        raise ValueError(f"{name}: requested amplitude is not representable")
    return result


def _tone(event: dict, count: int) -> tuple[np.ndarray, dict]:
    start, stop = (int(round(event[k] * FS)) for k in ("start_s", "end_s"))
    change = int(round(event["change_s"] * FS)) if event["mode"] == "step" else None
    t = np.arange(stop - start, dtype=np.float64) / FS
    f0, f1 = event["frequency_hz"], event["end_frequency_hz"]
    phase = event["phase_rad"] % (2 * np.pi)
    if event["mode"] == "step":
        t_change = (change - start) / FS
        cycles = f0 * np.minimum(t, t_change) + f1 * np.maximum(t - t_change, 0)
    elif event["mode"] == "linear":
        slope = (f1 - f0) / ((stop - start) / FS)
        cycles = f0 * t + 0.5 * slope * t**2
    else:
        cycles = f0 * t
    wave = np.zeros(count, dtype=np.float64)
    wave[start:stop] = np.sin(phase + 2 * np.pi * cycles)
    return wave, dict(start_sample=start, change_sample=change, stop_sample_exclusive=stop,
        actual_start_s=start / FS, actual_change_s=change / FS if change is not None else None, actual_end_s=stop / FS,
        phase_wrapped_rad=float(phase))


def generate_scene(config: dict) -> tuple[np.ndarray, dict, dict[str, np.ndarray]]:
    """Generate input, deterministic metadata, and normalized offline components.

    Per-source J/S refers to that source's active-interval RMS divided by the
    complete clean target's whole-scene RMS. Finite sine segments are explicitly
    normalized to this RMS, not assumed to have exactly 1/sqrt(2) RMS. A single
    common scale is applied after mixing; no source or sample is clipped.
    """
    c, count = _validated(config)
    rng = np.random.default_rng(c["seed"])
    rolloff = min(200.0, (c["target_high_hz"] - c["target_low_hz"]) / 5)
    if c["target_rms"]:
        frequencies = np.fft.rfftfreq(count, 1 / FS)
        low, high = c["target_low_hz"], c["target_high_hz"]
        envelope = np.zeros(frequencies.size, dtype=np.float64)
        inside = (frequencies > low) & (frequencies < high)
        envelope[inside] = 1.0
        rising = inside & (frequencies < low + rolloff)
        falling = inside & (frequencies > high - rolloff)
        envelope[rising] = 0.5 - 0.5 * np.cos(np.pi * (frequencies[rising] - low) / rolloff)
        envelope[falling] = 0.5 - 0.5 * np.cos(np.pi * (high - frequencies[falling]) / rolloff)
        if np.count_nonzero(envelope) < 2:
            raise ValueError("target band/time length cannot represent a dispersed signal (fewer than two FFT bins)")
        shaped = np.fft.irfft(np.fft.rfft(rng.standard_normal(count)) * envelope, n=count)
        target = _at_rms(shaped, c["target_rms"], "target")
        target_bins = int(np.count_nonzero(envelope))
    else:
        target = np.zeros(count, dtype=np.float64)
        target_bins = 0
    # Always draw noise after target generation; events do not alter either RNG stream.
    noise = _at_rms(rng.standard_normal(count), c["noise_rms"], "noise")
    probe_components, probe_info = {}, []
    if c["use_probes"] and c["target_rms"]:
        time = np.arange(count, dtype=np.float64) / FS
        for a, b in ((0, 1), (2, 3)):
            if c["events"][a]["enabled"] and c["events"][b]["enabled"]:
                hz = (c["events"][a]["frequency_hz"] + c["events"][b]["frequency_hz"]) / 2
                probe = _at_rms(np.sin(2 * np.pi * hz * time), 0.1 * c["target_rms"], "probe")
                name = f"probe_{len(probe_info) + 1}"
                probe_components[name] = probe
                probe_info.append(dict(name=name, frequency_hz=hz, definition="static midpoint of starting frequencies", source_pair=[a + 1, b + 1]))
        if probe_components:
            combined = target.copy()
            for probe in probe_components.values():
                combined += probe
            clean_factor = c["target_rms"] / _rms(combined)
            target = combined * clean_factor
            probe_components = {name: x * clean_factor for name, x in probe_components.items()}
    clean_rms = _rms(target)
    components = dict(target=target, noise=noise)
    event_metadata = []
    for i, event in enumerate(c["events"]):
        if event["enabled"]:
            raw, interval = _tone(event, count)
        else:
            raw = np.zeros(count, dtype=np.float64)
            interval = dict(start_sample=None, change_sample=None, stop_sample_exclusive=None,
                actual_start_s=None, actual_change_s=None, actual_end_s=None,
                phase_wrapped_rad=float(event["phase_rad"] % (2 * np.pi)))
        start, stop = interval["start_sample"], interval["stop_sample_exclusive"]
        unit_rms = _rms(raw[start:stop]) if event["enabled"] else 0.0
        desired = 0.0
        if event["enabled"]:
            try:
                desired = clean_rms * 10.0 ** (event["js_db"] / 20)
            except OverflowError as exc:
                raise ValueError(f"events[{i}].js_db: amplitude overflows") from exc
            if not math.isfinite(desired) or desired == 0:
                raise ValueError(f"events[{i}].js_db: amplitude is not representable")
            active = _at_rms(raw[start:stop], desired, f"jam_{i + 1}")
            raw = np.zeros(count, dtype=np.float64)
            raw[start:stop] = active
        else:
            raw.fill(0)
        name = f"jam_{i + 1}"
        components[name] = raw
        multiplier = desired / unit_rms if event["enabled"] and unit_rms else 0.0
        if not math.isfinite(multiplier):
            raise ValueError(f"events[{i}]: normalization multiplier overflows")
        used = [event["frequency_hz"]] if event["mode"] == "fixed" else [event["frequency_hz"], event["end_frequency_hz"]]
        event_metadata.append(dict(name=name, **deepcopy(event), **interval,
            unit_active_rms=unit_rms, requested_active_rms=desired,
            generation_amplitude_multiplier=multiplier,
            generation_normalization="unit active segment explicitly normalized to requested J/S RMS",
            used_frequency_hz=used, in_detector_range=all(DETECTOR_RANGE_HZ[0] <= f <= DETECTOR_RANGE_HZ[1] for f in used)))
    mixture_names = list(components)
    mixture = np.zeros(count, dtype=np.float64)
    for name in mixture_names:
        with np.errstate(over="ignore", invalid="ignore"):
            mixture += components[name]
    pre_peak = float(np.max(np.abs(mixture)))
    if not math.isfinite(pre_peak):
        raise ValueError("mixed waveform overflows; reduce requested levels")
    scale = min(1.0, HEADROOM / pre_peak) if pre_peak else 1.0
    before_rms = {name: _rms(x) for name, x in components.items()}
    components = {name: x * scale for name, x in components.items()}
    mixture.fill(0)
    for name in mixture_names:
        mixture += components[name]
    rounded = np.rint(mixture * 32768.0)
    if not np.all(np.isfinite(rounded)) or np.any(rounded < -32768) or np.any(rounded > 32767):
        raise ValueError("internal range error: int16 would overflow; samples were not clipped")
    samples = rounded.astype("<i2")
    components.update({name: x * scale for name, x in probe_components.items()})
    components["quantization_error"] = samples.astype(np.float64) / 32768 - mixture
    after_rms = {name: _rms(x) for name, x in components.items()}
    for event in event_metadata:
        x = components[event["name"]][event["start_sample"]:event["stop_sample_exclusive"]]
        actual = _rms(x)
        event.update(active_rms=actual, full_scene_rms=after_rms[event["name"]],
            actual_js_db=20 * math.log10(actual / after_rms["target"]) if event["enabled"] and actual and after_rms["target"] else None)
    activity = {}
    for event in event_metadata:
        if event["enabled"]:
            for sample, delta in ((event["start_sample"], 1), (event["stop_sample_exclusive"], -1)):
                activity[sample] = activity.get(sample, 0) + delta
    active = maximum_active = 0
    for sample in sorted(activity):
        active += activity[sample]
        maximum_active = max(maximum_active, active)
    metadata = dict(generator_version=GENERATOR_VERSION, config=c, fs_hz=FS, sample_count=count,
        requested_seconds=c["seconds"], actual_seconds=count / FS, sample_time_quantization="round to nearest sample, ties to even; stop is exclusive",
        scale=scale, scale_policy="one common multiplicative front-end gain, never per-sample clipping", headroom=HEADROOM,
        pre_scale_max_abs=pre_peak, unquantized_max_abs=float(np.max(np.abs(mixture))),
        actual_max_abs_sample=int(np.max(np.abs(samples.astype(np.int32)))),
        actual_min_sample=int(samples.min()), actual_max_sample=int(samples.max()),
        input_sha256=sha256(samples.tobytes(order="C")).hexdigest(), input_sha256_format="C-order little-endian signed int16 payload bytes (no file header)",
        target_definition="smooth raised-cosine PSD edges, Gaussian bandpass random signal; optional weak static probes included in complete target",
        target_edge_rolloff_hz=rolloff, target_nonzero_fft_bins=target_bins,
        js_definition="20log10(jam active-interval RMS / complete clean target whole-scene RMS), before int16 quantization",
        pre_scale_component_rms=before_rms, component_rms=after_rms,
        mixture_component_names=mixture_names, diagnostic_component_names=list(probe_components),
        quantization_error_definition="input/32768 minus ordered sum of mixture_component_names; offline only",
        events=event_metadata, probes=probe_info, enabled_sources=sum(e["enabled"] for e in c["events"]),
        enabled_sources_exceed_capacity=sum(e["enabled"] for e in c["events"]) > c["capacity"],
        max_simultaneous_sources=maximum_active, over_capacity=maximum_active > c["capacity"],
        source_concurrency_definition="Half-open quantized source intervals only; not actual FPGA candidate/tracker/group allocation or retirement state",
        supported_detection_hz=list(DETECTOR_RANGE_HZ),
        applicability="Generation permits any nonaliased input. V31 main detector search is 1–7kHz; no online protection band or labels are added.",
        numpy_version=np.__version__, random_bit_generator=type(rng.bit_generator).__name__)
    return samples, metadata, components


def save_scene(directory: Path, config: dict) -> Path:
    """Save the five scene files and return the resolved directory path.

    Only these known filenames are replaced. ``components.npz`` and JSON labels
    are offline reference material and must not be transmitted to the FPGA.
    """
    samples, metadata, components = generate_scene(config)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    np.save(directory / "input.npy", samples, allow_pickle=False)
    with wave.open(str(directory / "input.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(FS)
        wav.writeframes(samples.tobytes(order="C"))
    np.savez_compressed(directory / "components.npz", **components)
    (directory / "scene.json").write_text(json.dumps(metadata["config"], ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")
    metadata["input_npy_sha256"] = sha256((directory / "input.npy").read_bytes()).hexdigest()
    metadata["input_wav_sha256"] = sha256((directory / "input.wav").read_bytes()).hexdigest()
    metadata["components_npz_sha256"] = sha256((directory / "components.npz").read_bytes()).hexdigest()
    metadata["generator_source_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
    (directory / "source_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")
    return directory

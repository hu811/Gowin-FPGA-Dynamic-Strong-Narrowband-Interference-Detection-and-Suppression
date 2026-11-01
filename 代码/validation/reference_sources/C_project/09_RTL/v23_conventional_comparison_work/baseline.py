"""Causal conventional raw-spectrum peak tracking; no component labels enter here.

This is a floating-point software comparator, not synthesized baseline RTL.
It deliberately assumes zero FFT/estimation/configuration service time. Results
become usable at the next input sample after a complete observation window.
"""
from dataclasses import asdict, dataclass
from functools import lru_cache
import json
from pathlib import Path
import numpy as np


@dataclass(frozen=True)
class Config:
    window: int = 512
    hop: int = 64
    fs: int = 32000
    threshold_db: float = 21.0
    estimator: str = "dtft_grid"
    minimum_hz: float = 1000.0
    maximum_hz: float = 7000.0
    grid_hz: float = 3.90625
    max_centers: int = 4
    match_hz: float = 93.75
    minimum_separation_hz: float = 46.875
    missing_exit_observations: int = 2
    fade_samples: int = 32


def configuration(value=None):
    if value is None:
        return Config()
    if isinstance(value, Config):
        return value
    return Config(**value)


def _raw_strength(power, center, cfg):
    # 512: offsets3..12, exactly the current strong_gate.v raw-bin references.
    # 1024: offsets6..24, a declared same-physical-neighborhood extension.
    first = int(np.ceil(187.5 * cfg.window / cfg.fs))
    last = int(np.floor(750.0 * cfg.window / cfg.fs))
    offsets = np.arange(first, last + 1)
    left = center - offsets
    right = center + offsets
    if len(offsets) < 10 or left.min() < 1 or right.max() >= len(power) - 1:
        return None
    # FFT normalization is4/N, matching the RTL's Q29-complex/Q58-power scale.
    # In this floating comparator the native29Q58 background floor is29/2**58.
    background = max(float(np.median(power[left])), float(np.median(power[right])), 29 / 2**58)
    peak = max(float(power[center]), 29 / 2**58)
    ratio = peak / background
    if not np.isfinite(ratio):
        return None
    return dict(raw_peak_power=peak, raw_background=background,
                prominence_db=float(10 * np.log10(ratio)), reference_per_side=len(offsets))


@lru_cache(maxsize=512)
def _dtft_phase(window, fs, native_bin, minimum_hz, maximum_hz, grid_hz):
    center = native_bin * fs / window
    lo = max(minimum_hz, center-.75*fs/window)
    hi = min(maximum_hz, center+.75*fs/window)
    frequencies = np.arange(np.ceil(lo/grid_hz),np.floor(hi/grid_hz)+1)*grid_hz
    return frequencies, np.exp(-2j*np.pi*np.outer(frequencies/fs,np.arange(window)))


def _refine(frame, spectrum, native_bin, cfg):
    if cfg.estimator == "log_parabolic":
        p = np.maximum(abs(spectrum[native_bin - 1:native_bin + 2])**2, 1e-30)
        a, b, c = np.log(p)
        denominator = a - 2*b + c
        offset = 0.0 if abs(denominator) < 1e-30 else float(np.clip(.5*(a-c)/denominator, -.75, .75))
        frequency = (native_bin + offset) * cfg.fs / cfg.window
    elif cfg.estimator == "dtft_grid":
        center = native_bin * cfg.fs / cfg.window
        frequencies, phase = _dtft_phase(cfg.window,cfg.fs,native_bin,cfg.minimum_hz,cfg.maximum_hz,cfg.grid_hz)
        if not len(frequencies):
            return center
        # Independent single-tone periodogram maximization, no subtraction,
        # joint fitting, or use of the other candidates' true frequencies.
        score = abs(phase @ frame)**2
        frequency = frequencies[int(np.argmax(score))]
    else:
        raise ValueError(cfg.estimator)
    return float(np.clip(np.rint(frequency/cfg.grid_hz)*cfg.grid_hz,
                         cfg.minimum_hz, cfg.maximum_hz))


def detect_updates(samples, config=None):
    """Accept only mixed signed-int16 samples; return causal center updates/logs."""
    cfg = configuration(config)
    values = np.asarray(samples)
    if values.ndim != 1 or values.dtype.kind not in "iu" or np.max(abs(values.astype(np.int64)), initial=0) > 32768:
        raise ValueError("Expected one-dimensional signed-int16 sample integers")
    if cfg.window not in (512, 1024):
        raise ValueError("The frozen comparison supports512/1024 windows only")
    signal = values.astype(float) / 32768.0
    n = np.arange(cfg.window)
    window = .5-.5*np.cos(2*np.pi*n/cfg.window)
    tracks = []
    next_id = 0
    observations, updates = [], []
    previous = ()
    for observation, end in enumerate(range(cfg.window, len(values)+1, cfg.hop)):
        frame = signal[end-cfg.window:end] * window
        spectrum = np.fft.rfft(frame) * (4/cfg.window)
        power = abs(spectrum)**2
        lo = int(np.ceil(cfg.minimum_hz*cfg.window/cfg.fs))
        hi = int(np.floor(cfg.maximum_hz*cfg.window/cfg.fs))
        possible = []
        for k in range(lo, hi+1):
            if not(power[k] >= power[k-1] and power[k] > power[k+1]):
                continue
            strength = _raw_strength(power, k, cfg)
            if strength is None or strength['prominence_db'] < cfg.threshold_db:
                continue
            frequency = _refine(frame, spectrum, k, cfg)
            possible.append(dict(frequency_hz=frequency, native_bin=k, **strength))
        # Conventional capacity policy: strongest raw peaks first; a reported
        # overflow is not a claim that all interference has been suppressed.
        possible.sort(key=lambda c: (-c['raw_peak_power'], c['frequency_hz']))
        selected = []
        for candidate in possible:
            if all(abs(candidate['frequency_hz']-p['frequency_hz']) > cfg.minimum_separation_hz for p in selected):
                selected.append(candidate)
        overflow = len(selected) > cfg.max_centers
        selected = selected[:cfg.max_centers]
        # Match to currently active tracks; each track/candidate used at most once.
        edges = sorted((abs(t['frequency_hz']-c['frequency_hz']), i, j)
                       for i, t in enumerate(tracks) for j, c in enumerate(selected)
                       if abs(t['frequency_hz']-c['frequency_hz']) <= cfg.match_hz)
        used_t, used_c = set(), set()
        for distance, i, j in edges:
            if i in used_t or j in used_c:
                continue
            tracks[i].update(selected[j], missing=0, observation=observation)
            used_t.add(i); used_c.add(j)
        retained = []
        for i, track in enumerate(tracks):
            if i not in used_t:
                track['missing'] += 1
            if track['missing'] < cfg.missing_exit_observations:
                retained.append(track)
        # A once-missing old track retains its slot. New strong observations do
        # not steal a still-authorized slot; no silent capacity enlargement.
        for j, candidate in enumerate(selected):
            if j not in used_c and len(retained) < cfg.max_centers:
                retained.append(dict(candidate, track_id=next_id, missing=0, observation=observation))
                next_id += 1
        tracks = sorted(retained, key=lambda t: t['track_id'])
        centers = tuple(sorted(t['frequency_hz'] for t in tracks))
        if centers != previous:
            updates.append(dict(sample=end, frequencies_hz=list(centers), observation=observation))
            previous = centers
        observations.append(dict(observation=observation, window_start_sample=end-cfg.window,
                                 window_end_sample=end, result_available_sample=end,
                                 candidates=possible, selected=selected, tracks=[dict(t) for t in tracks],
                                 strong_overflow=overflow, update=bool(updates and updates[-1]['sample']==end)))
    return dict(config=asdict(cfg), updates=updates, observations=observations,
                stats=dict(observations=len(observations), updates=len(updates),
                    observations_with_active_centers=sum(bool(o['tracks']) for o in observations),
                    strong_overflow_observations=sum(o['strong_overflow'] for o in observations),
                    maximum_active_centers=max((len(o['tracks']) for o in observations), default=0)),
                convention=dict(input='mixed signed int16 only; no clean labels or generator truth',
                    fft='float64 periodic-Hann FFT,4/N normalization; not quantized RTL FFT',
                    reference='512:±3..12 bins each10;1024:±6..24 each19, same physical187.5..750Hz',
                    admission='one strong observation; no second entry confirmation',
                    exit='two consecutive missing strong observations',
                    ordering='raw-power descending with frequency ascending ties; top4; overflow logged',
                    service_latency='zero; complete window ending at exclusive index end, update applies sample[end]',
                    scope='ordinary raw-spectrum independent peak estimator, not joint-template fitting'))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = detect_updates(np.load(args.input), json.loads(args.config.read_text()) if args.config else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf8')

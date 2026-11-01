"""Conventional independent-peak1024 detector with declared finite arithmetic.

Only mixed signed-int16 input enters this module. No target/event labels.
This is a software numerical model, not an implemented1024 hardware design.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
from functools import lru_cache
from pathlib import Path
import sys
import numpy as np

HERE = Path(__file__).resolve().parent
RTL = HERE.parents[2]
sys.path.insert(0, str(RTL / 'pro_v2/model'))
import fft_reference
import gate_reference
sys.path.insert(0, 'D:/FPGA_Competition/pro_v2_resource_v7/model')
import aux_reference


@dataclass(frozen=True)
class Config:
    window: int = 1024
    hop: int = 64
    fs: int = 32000
    threshold_db: int = 21
    estimator: str = 'dtft_grid_q30'
    minimum_grid: int = 256
    maximum_grid: int = 1792
    max_centers: int = 4
    match_grid: int = 24
    minimum_separation_grid: int = 12
    missing_exit_observations: int = 2
    fade_samples: int = 32


def rne_array(value, shift):
    value = np.asarray(value, dtype=np.int64)
    base = value >> shift
    remainder = value - (base << shift)
    half = 1 << (shift - 1)
    return base + ((remainder > half) | ((remainder == half) & ((base & 1) != 0)))


def ratio_q32(threshold_db):
    assert threshold_db in (18, 21)
    with localcontext() as context:
        context.prec = 60
        value = round(Decimal(10) ** (Decimal(threshold_db) / 10) * (1 << 32))
    if threshold_db == 21:
        assert value == gate_reference.RATIO_Q32
    return value


def strength(power, k, threshold_db=21):
    """Same physical187.5..750Hz references; odd19-bin power medians."""
    p = list(map(int, power))
    assert len(p) == 513 and all(0 <= v < (1 << 64) for v in p)
    offsets = range(6, 25)
    if k - 24 < 1 or k + 24 >= 512:
        return dict(valid=False, strong=False)
    left = sorted(p[k - d] for d in offsets)
    right = sorted(p[k + d] for d in offsets)
    # For odd19, the median is item9, exactly, so no averaging/truncation.
    background_twice = 2 * max(left[9], right[9], gate_reference.POWER_FLOOR)
    numerator = max(p[k], gate_reference.POWER_FLOOR) << 33
    denominator = ratio_q32(threshold_db) * background_twice
    return dict(valid=True, strong=numerator >= denominator, raw_peak_power=p[k],
                background_twice=background_twice, reference_per_side=19,
                peak_shifted_q33=numerator, ratio_times_background=denominator)


@lru_cache(maxsize=1)
def dtft_twiddles():
    # Same Q30 trigonometric precision and ties-to-even constant generation as
    # the production FFT, extended to the already used8192-point phase grid.
    a = 2 * np.pi * np.arange(8192) / 8192
    return np.rint(np.cos(a) * (1 << 30)).astype(np.int64), np.rint(-np.sin(a) * (1 << 30)).astype(np.int64)


@lru_cache(maxsize=512)
def phases(k):
    center = k * 8
    grids = np.arange(max(256, center - 6), min(1792, center + 6) + 1, dtype=np.int64)
    indices = (grids[:, None] * np.arange(1024)[None, :]) & 8191
    real, imag = dtft_twiddles()
    return grids, real[indices], imag[indices]


def refine_dtft(windowed_q29, k):
    grids, wr, wi = phases(k)
    # Exact Q29*Q30 products accumulate before rounding; worst-case sum needs
    # signed71bits. Split into floor-high and unsigned-low sums to avoid any
    # numpy int64 overflow, reconstruct using Python integers, then RNE38
    # combines Q30 rescaling and4/N. This is not a radix2 FFT equivalence claim.
    def sum_products(twiddle):
        products = twiddle * windowed_q29[None, :]
        high = products >> 30
        low = products - (high << 30)
        hs, ls = np.sum(high, axis=1), np.sum(low, axis=1)
        return np.asarray([fft_reference.rne((int(h) << 30) + int(l), 38)
                           for h, l in zip(hs, ls)], np.int64)
    real = sum_products(wr)
    imag = sum_products(wi)
    real = np.clip(real, -(1 << 31), (1 << 31) - 1)
    imag = np.clip(imag, -(1 << 31), (1 << 31) - 1)
    # Python integers preserve the complete unsignedQ58 rank, including any
    # value beyond signed64; first/lowest grid wins exact integer ties.
    scores = [int(a) ** 2 + int(b) ** 2 for a, b in zip(real, imag)]
    best = max(range(len(scores)), key=lambda i: scores[i])
    return int(grids[best]), dict(grid_trials=len(grids), dtft_multiplications=2 * 1024 * len(grids))


def refine_log(power, k):
    a, b, c = [aux_reference.log2_power(int(power[i])) for i in (k - 1, k, k + 1)]
    denominator = 2 * b - a - c
    offset = 0 if denominator <= 0 else max(-4, min(4, aux_reference.divide_rne(4 * (c - a), denominator)))
    return k * 8 + offset, dict(log_degenerate=denominator <= 0)


def extract_observations(samples, estimator):
    """Shared fixed raw observations, retaining every18dB eligible peak.

    Both predeclared18/21 arms use this same cache. No parameters are selected
    from the final comparison; candidates below18 cannot pass either arm.
    """
    assert estimator in ('dtft_grid_q30', 'log_parabolic_q22')
    values = np.asarray(samples)
    assert values.ndim == 1 and values.dtype.kind in 'iu'
    assert np.min(values, initial=0) >= -32768 and np.max(values, initial=0) <= 32767
    observations = []
    hann = fft_reference.constants()[0]
    for observation, end in enumerate(range(1024, len(values) + 1, 64)):
        frame = values[end - 1024:end].astype(np.int64)
        re, im, power = fft_reference.transform(frame)
        windowed = rne_array(frame * hann, 16)
        possible = []
        for k in range(32, 225):
            # Preserve the old conventional baseline's exact local-max tie.
            if not (power[k] >= power[k - 1] and power[k] > power[k + 1]):
                continue
            evidence = strength(power, k, 18)
            if not evidence['valid'] or not evidence['strong']:
                continue
            grid, arithmetic = refine_dtft(windowed, k) if estimator == 'dtft_grid_q30' else refine_log(power, k)
            grid = min(1792, max(256, grid))
            possible.append(dict(grid=grid, frequency_hz=grid * 3.90625, native_bin=k, **evidence, **arithmetic))
        observations.append(dict(observation=observation, window_start_sample=end - 1024,
                                 window_end_sample=end, candidates_min18=possible))
    return observations


def detect_updates(samples, config, extracted=None):
    cfg = config if isinstance(config, Config) else Config(**config)
    assert cfg.window == 1024 and cfg.hop == 64 and cfg.fs == 32000 and cfg.threshold_db in (18, 21)
    assert cfg.estimator in ('dtft_grid_q30', 'log_parabolic_q22')
    if extracted is None:
        extracted = extract_observations(samples, cfg.estimator)
    tracks, observations, updates = [], [], []
    next_id, previous = 0, ()
    ratio = ratio_q32(cfg.threshold_db)
    for record in extracted:
        observation, end = record['observation'], record['window_end_sample']
        possible = []
        for candidate in record['candidates_min18']:
            denominator = ratio * candidate['background_twice']
            if candidate['peak_shifted_q33'] >= denominator:
                possible.append(dict(candidate, ratio_times_background=denominator))
        possible.sort(key=lambda c: (-c['raw_peak_power'], c['grid']))
        unique = []
        for candidate in possible:
            if all(abs(candidate['grid'] - p['grid']) > cfg.minimum_separation_grid for p in unique):
                unique.append(candidate)
        overflow = len(unique) > cfg.max_centers
        selected = unique[:cfg.max_centers]
        edges = sorted((abs(t['grid'] - c['grid']), i, j)
                       for i, t in enumerate(tracks) for j, c in enumerate(selected)
                       if abs(t['grid'] - c['grid']) <= cfg.match_grid)
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
        for j, candidate in enumerate(selected):
            if j not in used_c and len(retained) < cfg.max_centers:
                retained.append(dict(candidate, track_id=next_id, missing=0, observation=observation))
                next_id += 1
        tracks = sorted(retained, key=lambda t: t['track_id'])
        centers = tuple(sorted(t['grid'] * 3.90625 for t in tracks))
        if centers != previous:
            updates.append(dict(sample=end, frequencies_hz=list(centers), observation=observation))
            previous = centers
        observations.append(dict(observation=observation, window_start_sample=end - 1024,
            window_end_sample=end, result_available_sample=end, candidates=possible, selected=selected,
            tracks=[dict(t) for t in tracks], strong_overflow=overflow))
    return dict(config=asdict(cfg), observations=observations, updates=updates,
                stats=dict(observations=len(observations), updates=len(updates),
                    strong_overflow_observations=sum(o['strong_overflow'] for o in observations),
                    maximum_active_centers=max((len(o['tracks']) for o in observations), default=0)),
                convention=dict(input='mixed signed-int16 only; no labels or clean component',
                    fft='production integer1024 periodic-Hann Q30 window/twiddles; stage RNE; normalized Q29 complex/Q58 power',
                    gate=str(cfg.threshold_db) + 'dB Q32 ratio and power floor29; greater side of19-point raw power medians',
                    refs='±6..24/native1024 equals187.5..750Hz; same physical neighborhood, not proven equal false alarm',
                    ordering='descending raw power, ascending grid ties; top capacity; overflow retained in logs',
                    admission='one strong observation; two missing observations exit; no extra amplitude floor',
                    service='zero compute and bank preparation; earliest next-sample application; idealized compute waiting, not a quality bound',
                    estimator='independent single-tone grid DTFT or separately named log interpolation; no joint fitting'))

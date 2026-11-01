"""Candidate-independent, greatest-of-two-side raw background estimate.

Only the reference construction changes from v1.2. The numerator remains one
nearest native FFT bin. No residual spectrum, clean signal, previous frames or
candidate-dependent masks enter this statistic. 21 dB is an initial conservative
ratio setting for this NEW metric, not a calibrated false-alarm probability.
"""
import numpy as np
from short_detector import FS
from raw_strength import POWER_FLOOR

METRIC = 'fixed_sides_max_median_v1'
THRESHOLD_DB = 21.
GUARD_BINS = 2
REFERENCE_BINS = 12
MIN_SIDE_BINS = 10


def reference_statistics(power, peak_bin):
    """Use k-12..k-3 and k+3..k+12; both sides must be complete.

    A ten-value median tolerates up to four arbitrarily elevated bins on each
    side before both central order statistics can be contaminated. This is a
    structural robustness property, not a guarantee about Hann leakage.
    """
    power = np.asarray(power, dtype=float)
    if power.ndim != 1 or len(power) < 4:
        raise ValueError('one real power spectrum required')
    if not np.isfinite(power).all() or np.any(power < 0):
        raise ValueError('power spectrum must be finite and nonnegative')
    if not 1 <= peak_bin < len(power)-1:
        raise ValueError('peak bin must exclude DC and Nyquist')
    offsets = np.arange(GUARD_BINS+1, REFERENCE_BINS+1)
    left = peak_bin-offsets
    right = peak_bin+offsets
    left = left[(left > 0) & (left < len(power)-1)]
    right = right[(right > 0) & (right < len(power)-1)]
    valid = len(left) >= MIN_SIDE_BINS and len(right) >= MIN_SIDE_BINS
    left_level = max(float(np.median(power[left])), POWER_FLOOR) if len(left) else None
    right_level = max(float(np.median(power[right])), POWER_FLOOR) if len(right) else None
    background = max(left_level, right_level) if valid else None
    return dict(metric=METRIC, background_power=background,
                left_background_power=left_level, right_background_power=right_level,
                left_reference_bins=left.tolist(), right_reference_bins=right.tolist(),
                left_reference_count=len(left), right_reference_count=len(right),
                reference_bins=sorted(left.tolist()+right.tolist()),
                reference_count=len(left)+len(right), enough_reference=bool(valid))


def local_strength_evidence(samples, peaks):
    """Same-window raw FFT; adding/removing other candidates has no effect."""
    x = np.asarray(samples, dtype=float)
    if x.ndim != 1 or len(x) < 4 or not np.isfinite(x).all():
        raise ValueError('samples must be one finite window')
    win = np.hanning(len(x)+1)[:-1]
    with np.errstate(over='raise', invalid='raise', divide='raise'):
        power = np.abs(2*np.fft.rfft(x*win)/win.sum())**2
    evidence = []
    for p in peaks:
        frequency = float(p['frequency_hz'])
        if not np.isfinite(frequency) or not 0 < frequency < FS/2:
            raise ValueError('candidate frequency outside (0, Nyquist)')
        k = int(np.clip(np.round(frequency*len(x)/FS), 1, len(power)-2))
        item = reference_statistics(power, k)
        peak_power = float(power[k])
        background = item['background_power']
        contrast = (float(10*np.log10(max(peak_power, POWER_FLOOR)/background))
                    if background is not None else None)
        item.update(frequency_hz=frequency, fitted_amplitude=float(p['amplitude']),
                    residual_contrast_db=float(p['contrast_db']), peak_bin=k,
                    raw_peak_power=peak_power, local_contrast_db=contrast,
                    strong=bool(contrast is not None and contrast >= THRESHOLD_DB))
        evidence.append(item)
    return evidence

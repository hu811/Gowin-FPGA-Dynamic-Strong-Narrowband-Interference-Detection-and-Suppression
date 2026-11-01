"""Raw-spectrum metric only. No legacy fast/ordinary entry fallback is included."""
import numpy as np
from short_detector import FS

MIN_AMPLITUDE = .055


POWER_FLOOR = 1e-16  # Squared amplitude floor, not an adaptive oracle.


def local_strength_evidence(samples, peaks, strong_contrast_db=21., guard_bins=2,
                            reference_bins=12, min_reference_bins=10):
    """Compare raw peak-bin power with a median of nearby raw bin powers.

Guard/reference widths are native FFT bins, *not* the fine fit grid. Exclude
every observed candidate's guarded region, including neighboring interference.
An insufficient reference set disables fast confirmation, never normal tracking.
The nearest native bin supplies the numerator without scalloping compensation.
"""
    if not 0 <= guard_bins < reference_bins:
        raise ValueError("require 0 <= guard_bins < reference_bins")
    if min_reference_bins < 1:
        raise ValueError("min_reference_bins must be positive")
    if not np.isfinite(strong_contrast_db):
        raise ValueError("strong_contrast_db must be finite")
    x = np.asarray(samples, dtype=float)
    if x.ndim != 1 or len(x) < 4 or not np.all(np.isfinite(x)):
        raise ValueError("samples must be one finite window")
    win = np.hanning(len(x) + 1)[:-1]
    # Python recomputes this for isolation from legacy code. Hardware can tap
    # the existing same-window FFT before matching-pursuit residue subtraction.
    power = np.abs(2 * np.fft.rfft(x * win) / win.sum()) ** 2
    bins = np.arange(len(power))
    centers = np.asarray([p["frequency_hz"] * len(x) / FS for p in peaks])
    excluded = np.zeros(len(power), dtype=bool)
    for center in centers:
        excluded |= np.abs(bins - center) <= guard_bins
    evidence = []
    for p, center in zip(peaks, centers):
        k = int(np.clip(np.round(center), 1, len(power) - 2))
        ref = bins[(np.abs(bins - center) <= reference_bins) & ~excluded &
                   (bins > 0) & (bins < len(power) - 1)]
        count = len(ref)
        background = max(float(np.median(power[ref])), POWER_FLOOR) if count else POWER_FLOOR
        peak_power = float(power[k])
        contrast = float(10 * np.log10(max(peak_power, POWER_FLOOR) / background))
        enough = count >= min_reference_bins
        evidence.append({"frequency_hz": float(p["frequency_hz"]),
                         "fitted_amplitude": float(p["amplitude"]),
                         "residual_contrast_db": float(p["contrast_db"]),
                         "raw_peak_power": peak_power,
                         "background_power": background,
                         "local_contrast_db": contrast,
                         "reference_count": int(count),
                         "reference_bins": ref.tolist(),
                         "peak_bin": k,
                         "enough_reference": bool(enough),
                         "strong": bool(enough and contrast >= strong_contrast_db)})
    return evidence

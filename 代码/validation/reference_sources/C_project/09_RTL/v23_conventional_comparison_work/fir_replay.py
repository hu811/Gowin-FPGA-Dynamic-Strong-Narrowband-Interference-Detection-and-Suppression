"""Matched V19 641-tap Q16 FIR and offline causal continuous-history replay.

The builder uses the archived production ROMs and GRID_ALIGNED integer
coefficient_generator arithmetic, not a newly designed scipy prototype.
The baseline scheduler intentionally has zero compute/preparation latency;
it applies decisions at the supplied sample, retaining the 32-sample fade.
This gives a software baseline an advantage and is not a second FPGA run.
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
import numpy as np
from scipy import signal

HERE = Path(__file__).resolve().parent
FS = 32000
GRID_HZ = FS / 8192
DELAY = 320
TAPS = 641


def _rom(name, bits, signed=False):
    values = [int(line.strip(), 2) for line in (HERE / 'rom' / name).read_text().splitlines() if line.strip()]
    return [value - (1 << bits) if signed and value & (1 << (bits - 1)) else value for value in values]


PROTOTYPE = _rom('prototype.mem', 18, True)[:321]
COSINE = _rom('cos_grid_direct.mem', 16)


def round_div_even(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError('positive divisor required')
    sign = -1 if numerator < 0 else 1
    q, r = divmod(abs(int(numerator)), int(denominator))
    return sign * (q + int(2 * r > denominator or (2 * r == denominator and q & 1)))


def quantized_frequencies(frequencies_hz):
    """Use production 3.90625 Hz grid, with ties-to-even for software estimates."""
    return [float(np.rint(float(f) / GRID_HZ) * GRID_HZ) for f in frequencies_hz]


@lru_cache(maxsize=4096)
def _correction(grid_index: int):
    if not 0 < grid_index < 4096:
        raise ValueError('center must be strictly between DC and Nyquist')
    word = int(grid_index) << 19
    phase = (320 * word) & 0xffffffff
    pcs = []
    gain = 0
    for tap, p in enumerate(PROTOTYPE):
        angle = (0x40000000 - (phase & 0x3fffffff)) if phase & 0x40000000 else phase & 0x3fffffff
        address = (angle >> 19) & 2047
        magnitude = 0 if angle & 0x40000000 else 65536 if address <= 5 else COSINE[address]
        negative = bool((phase >> 31) ^ ((phase >> 30) & 1))
        c = -magnitude if negative else magnitude
        pc = int(p) * c
        assert -(1 << 24) <= pc < (1 << 24)
        square = pc * c
        assert 0 <= square < (1 << 40)
        gain += square << (1 if tap == 320 else 2)
        pcs.append(pc)
        phase = (phase - word) & 0xffffffff
    assert (1 << 47) <= gain < (1 << 50)
    denominator = 50 * gain
    result = tuple(round_div_even(pc * 98 * (1 << 32), denominator) for pc in pcs)
    assert max(map(abs, result)) <= 1024
    return result


def build_bank(frequencies_hz):
    """Return full 641 signed-18-bit Q16 taps, delta[n-320]-sum(b_i)."""
    half = np.zeros(321, dtype=np.int64)
    half[320] = 65536
    for frequency in quantized_frequencies(frequencies_hz):
        half -= np.asarray(_correction(int(np.rint(frequency / GRID_HZ))), dtype=np.int64)
    if np.any(half < -131072) or np.any(half > 131071):
        raise OverflowError('production coefficient merger signed-18-bit overflow')
    return np.r_[half, half[-2::-1]]


def _rne_clip_array(numerator, shift=21):
    numerator = np.asarray(numerator, dtype=np.int64)
    magnitude = np.abs(numerator)
    q = magnitude >> shift
    remainder = magnitude & ((1 << shift) - 1)
    q += (remainder > (1 << (shift - 1))) | ((remainder == (1 << (shift - 1))) & ((q & 1) != 0))
    q = np.where(numerator < 0, -q, q)
    saturated = (q < -32768) | (q > 32767)
    return np.clip(q, -32768, 32767).astype(np.int16), int(saturated.sum())


def replay(input_rows, updates, mixed_input=None, fade_samples=32):
    """Execute supplied online decisions; never inspect clean labels.

    input_rows has shape (components,samples), in ADC counts; it can include
    an explicit input-quantization residual row. Their sum must equal the
    mixed int16 input. Updates are {sample,frequencies_hz}; a supplied `bank`
    may instead freeze exact recorded taps. Same-sample updates use the last.
    Updates during a fade keep only the newest deferred decision, applied at
    the first free boundary. Production coefficient-task scheduling is not
    imitated; the ordinary FFT baseline receives zero compute latency.
    """
    if fade_samples != 32:
        raise ValueError('matched production fade is exactly 32 samples')
    rows = np.asarray(input_rows, dtype=float)
    if rows.ndim == 1:
        rows = rows[None, :]
    size = rows.shape[1]
    mixed = np.asarray(np.rint(rows.sum(axis=0)) if mixed_input is None else mixed_input, dtype=np.int64)
    if len(mixed) != size or np.max(np.abs(rows.sum(axis=0) - mixed), initial=0) > 1e-7:
        raise ValueError('component rows plus quantization residue must sum to actual input')
    if np.max(np.abs(mixed), initial=0) > 32768 or np.max(mixed, initial=0) > 32767:
        raise ValueError('input outside int16 range')
    banks = [build_bank([])]
    bank_ids = {banks[0].tobytes(): 0}
    def bank_id(update):
        h = np.asarray(update['bank'], dtype=np.int64) if 'bank' in update else build_bank(update['frequencies_hz'])
        if h.shape != (641,):
            raise ValueError('complete 641-tap bank required')
        key = h.tobytes()
        if key not in bank_ids:
            bank_ids[key] = len(banks)
            banks.append(h)
        return bank_ids[key]
    plan = sorted([(int(u['sample']), order, u) for order, u in enumerate(updates)], key=lambda x: (x[0], x[1]))
    trajectory = np.zeros((size, 3), dtype=np.int32)
    active = 0
    target = None
    pending = None
    alpha = 0
    next_update = 0
    applied = []
    for n in range(size):
        while next_update < len(plan) and plan[next_update][0] <= n:
            _, _, update = plan[next_update]
            pending = bank_id(update)
            next_update += 1
        if target is None and pending is not None:
            if pending != active:
                target = pending
                alpha = 1
                applied.append({'sample': n, 'old_bank': active, 'new_bank': target})
            pending = None
        if target is None:
            trajectory[n] = active, active, 0
        else:
            trajectory[n] = active, target, alpha
            if alpha == 32:
                active, target = target, None
                alpha = 0
            else:
                alpha += 1
    # Each bank convolution sees the identical uninterrupted source history.
    component_out = np.zeros_like(rows)
    mixed_numerator = np.zeros(size, dtype=np.int64)
    for index, h in enumerate(banks):
        old_weight = np.where(trajectory[:, 0] == index, 32 - trajectory[:, 2], 0)
        new_weight = np.where(trajectory[:, 1] == index, trajectory[:, 2], 0)
        weight = old_weight + new_weight
        if not np.any(weight):
            continue
        linear = signal.lfilter(h.astype(float) / 65536, [1.], rows, axis=-1)
        component_out += linear * (weight / 32)[None, :]
        integer = np.convolve(mixed, h)[:size]
        mixed_numerator += integer * weight
    mixed_int16, saturations = _rne_clip_array(mixed_numerator)
    mixed_linear = mixed_numerator.astype(float) / (1 << 21)
    residue = float(np.max(np.abs(component_out.sum(axis=0) - mixed_linear), initial=0))
    assert residue < 1e-7
    return {'component_out': component_out, 'mixed_linear': mixed_linear, 'mixed_int16': mixed_int16,
            'trajectory': trajectory, 'banks': np.asarray(banks), 'applied': applied,
            'saturations': saturations, 'component_sum_max_error_counts': residue,
            'output_rounding_saturation_residual': mixed_int16.astype(float) - mixed_linear,
            'note': 'Same production coefficients/history/32-sample fade; baseline decisions have zero compute latency, not RTL scheduling.'}


if __name__ == '__main__':
    import csv, json, argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', type=Path, required=True, help='actual V19 component_archive/coefficients.csv')
    args = parser.parse_args()
    with args.verify.open(encoding='utf8') as file:
        actual = list(csv.DictReader(file))
    comparisons = {}
    for label, frequencies in [('bank1', [2800.78125]), ('bank2', [2800.78125, 2925.78125])]:
        generated = build_bank(frequencies)[:321]
        expected = np.array([int(row[label]) for row in actual])
        comparisons[label] = {'values': len(expected), 'mismatches': int(np.sum(generated != expected)),
                              'max_absolute_error_q16': int(np.max(np.abs(generated - expected)))}
        assert np.array_equal(generated, expected), comparisons
    print(json.dumps({'known_actual_generator_banks': comparisons, 'status': 'PASS'}, indent=2))

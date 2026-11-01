"""Bit exact radix-2 FFT used by analysis_fft (no detection/control truth).

Q15 samples -> periodic Q30 Hann -> Q29 data, each stage RNE /2.
Complex twiddles use Q30; each complex product rounds once before the
butterfly. Final amplitude-normalized complex spectrum is Q29 (4/N DFT),
and power is unsigned Q58. Only the positive spectrum is returned.
"""
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
Q = 1 << 29
TWIDDLE_Q = 1 << 30
POWER_FLOOR = round(1e-16 * (1 << 58))


def rne(value, shift):
    """Arithmetic division with nearest-even rounding, including negatives."""
    base, residue = divmod(int(value), 1 << shift)
    half = 1 << (shift - 1)
    return base + int(residue > half or (residue == half and (base & 1)))


def sat32(value):
    return max(-(1 << 31), min((1 << 31) - 1, int(value)))


def constants():
    n = np.arange(1024, dtype=float)
    hann = np.rint((.5 - .5 * np.cos(2 * np.pi * n / 1024)) * TWIDDLE_Q).astype(np.int64)
    angle = 2 * np.pi * np.arange(512) / 1024
    real = np.rint(np.cos(angle) * TWIDDLE_Q).astype(np.int64)
    imag = np.rint(-np.sin(angle) * TWIDDLE_Q).astype(np.int64)
    return hann, real, imag


def generate_roms(root=ROOT):
    root = Path(root)
    rom = root / 'rom'
    rom.mkdir(parents=True, exist_ok=True)
    names = ('fft_hann_q30.mem', 'fft_twiddle_re_q30.mem', 'fft_twiddle_im_q30.mem')
    for name, values in zip(names, constants()):
        (rom / name).write_text(''.join(f'{int(v) & 0xffffffff:032b}\n' for v in values), encoding='ascii')
    hann=constants()[0]
    assert int(hann[512])==1<<30 and all(int(hann[k])==int(hann[1024-k]) for k in range(1,1024))
    (rom/'fft_hann_half_q30.mem').write_text(''.join(f'{int(v):032b}\n' for v in hann[:512]),encoding='ascii')


def transform(samples):
    """Return (real, imag, power) integer arrays, natural positive bin order."""
    samples = [int(x) for x in samples]
    n = len(samples)
    if n not in (512, 1024):
        raise ValueError('analysis_fft supports 512 and 1024 samples')
    if any(x < -32768 or x > 32767 for x in samples):
        raise ValueError('input is signed Q15')
    bits = n.bit_length() - 1
    hann, twr, twi = constants()
    real = [0] * n
    imag = [0] * n
    stride = 1024 // n
    for k, sample in enumerate(samples):
        address = int(f'{k:0{bits}b}'[::-1], 2)
        real[address] = rne(sample * int(hann[k * stride]), 16)
    for stage in range(1, bits + 1):
        half = 1 << (stage - 1)
        span = half << 1
        for base in range(0, n, span):
            for j in range(half):
                a, b = base + j, base + j + half
                wi = j << (10 - stage)
                br, bi = real[b], imag[b]
                tr = rne(br * int(twr[wi]) - bi * int(twi[wi]), 30)
                ti = rne(bi * int(twr[wi]) + br * int(twi[wi]), 30)
                ar, ai = real[a], imag[a]
                real[a], imag[a] = rne(ar + tr, 1), rne(ai + ti, 1)
                real[b], imag[b] = rne(ar - tr, 1), rne(ai - ti, 1)
    re = np.array([sat32(x * 4) for x in real[:n // 2 + 1]], dtype=np.int64)
    im = np.array([sat32(x * 4) for x in imag[:n // 2 + 1]], dtype=np.int64)
    power = np.array([int(r) * int(r) + int(i) * int(i) for r, i in zip(re, im)], dtype=np.uint64)
    return re, im, power


def float_reference(samples):
    n = len(samples)
    win = np.hanning(n + 1)[:-1]
    return np.fft.rfft(np.asarray(samples, dtype=float) / 32768 * win) * (2 / win.sum())


if __name__ == '__main__':
    generate_roms()

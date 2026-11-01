"""Exact archived coefficient functions; api.py always supplies direct641 explicitly."""
from functools import lru_cache
import json
import numpy as np
from scipy import signal

FS = 32000


COEFFICIENT_BITS = 18


COEFFICIENT_FRAC = 16


DEFAULT_CONFIG = {"name": "remez3_769", "method": "remez3", "taps": 769,
                  "pass_hz": 8., "stop_hz": 54.6875, "far_hz": 250.,
                  "stop_weight": 2., "far_weight": 50.}


def quantize(values):
    q = np.rint(np.asarray(values) * 65536)
    if np.any(q < -131072) or np.any(q > 131071):
        raise OverflowError("18-bit Q16 coefficient overflow")
    return q / 65536


def _key(config):
    return json.dumps(config or DEFAULT_CONFIG, sort_keys=True)


@lru_cache(maxsize=256)
def _prototype(key):
    cfg = json.loads(key)
    n = cfg["taps"]
    if n % 2 != 1:
        raise ValueError("Only odd, symmetric linear-phase prototypes")
    if cfg["method"] == "remez3":
        p = signal.remez(n, [0, cfg["pass_hz"], cfg["stop_hz"], cfg["far_hz"],
                            cfg["far_hz"]+1, FS/2], [1, 0, 0],
                         weight=[1, cfg["stop_weight"], cfg["far_weight"]],
                         fs=FS, maxiter=200, grid_density=24)
    elif cfg["method"] == "remez":
        p = signal.remez(n, [0, cfg["pass_hz"], cfg["stop_hz"], FS/2], [1, 0],
                         weight=[1, cfg["stop_weight"]], fs=FS,
                         maxiter=200, grid_density=24)
    elif cfg["method"] == "firwin":
        p = signal.firwin(n, cfg["cutoff_hz"], fs=FS,
                         window=("kaiser", cfg["beta"]))
    else:
        raise ValueError(cfg["method"])
    p = quantize(p / np.sum(p))
    p.flags.writeable = False
    return p


def prototype(config=None):
    """Predesigned lowpass prototype, exactly representable signed Q16/18-bit."""
    return _prototype(_key(config))


@lru_cache(maxsize=4096)
def _correction(center, key):
    p = _prototype(key)
    m = np.arange(len(p)) - len(p)//2
    # Modeling quantized cosine ROM and final modulated coefficient storage.
    # The intermediate product and normalization are not yet a bit-exact RTL.
    c = quantize(np.cos(2*np.pi*center*m/FS))
    h = 2*p*c
    gain = np.sum(h*c)
    if abs(gain) < 0.5:
        raise ValueError("Invalid center normalization")
    h = quantize(h/gain * json.loads(key).get("strength", 1.))
    h.flags.writeable = False
    return h


def correction(center, config=None):
    """One real symmetric correction, driven by an estimated center in Hz."""
    if not 0 < center < FS/2:
        raise ValueError("Center outside (0, Nyquist)")
    return _correction(float(center), _key(config))

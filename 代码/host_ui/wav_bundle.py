"""Offline WAV conversion and authenticated reference import; no board processing.

Only returned int16 ``samples`` are an FPGA input. Components are optional
offline references and never contain purported FPGA output.
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import gcd
from pathlib import Path
import struct
import warnings

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

from scene import FS, MAX_SAMPLES

MIN_SAMPLES = 64


def digest(path: Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def _length(count: int) -> None:
    if not MIN_SAMPLES <= count <= MAX_SAMPLES:
        raise ValueError(f"input length must be {MIN_SAMPLES}..{MAX_SAMPLES} samples at 32000 Hz")


def _wav_format(path: Path) -> dict:
    """Reject compressed/ambiguous files before SciPy decodes ordinary RIFF WAV."""
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError("supported WAV container is little-endian RIFF/WAVE (not RF64/RIFX)")
        total = path.stat().st_size
        if struct.unpack("<I", header[4:8])[0] + 8 != total:
            raise ValueError("WAV RIFF size disagrees with file length")
        fmt = None
        data_sizes = []
        while handle.tell() < total:
            chunk = handle.read(8)
            if len(chunk) != 8:
                raise ValueError("truncated WAV chunk")
            tag, size = struct.unpack("<4sI", chunk)
            if handle.tell() + size > total:
                raise ValueError("WAV chunk extends past end of file")
            if tag == b"fmt ":
                if fmt is not None or size < 16 or size > 4096:
                    raise ValueError("invalid or repeated WAV format chunk")
                raw = handle.read(size)
                encoding, channels, rate, byte_rate, block, bits = struct.unpack("<HHIIHH", raw[:16])
                if encoding == 0xFFFE:
                    if len(raw) < 40 or struct.unpack("<H", raw[16:18])[0] < 22:
                        raise ValueError("truncated extensible WAV format")
                    valid_bits = struct.unpack("<H", raw[18:20])[0]
                    if valid_bits != bits:
                        raise ValueError("extensible WAV valid/container bit widths differ; convert explicitly first")
                    guid = raw[24:40]
                    if guid[4:] != bytes.fromhex("00001000800000aa00389b71"):
                        raise ValueError("unsupported extensible WAV subformat")
                    encoding = struct.unpack("<I", guid[:4])[0]
                if encoding not in (1, 3) or (encoding == 1 and bits not in (8, 16, 24, 32)) or (encoding == 3 and bits not in (32, 64)):
                    raise ValueError("WAV must be PCM8/16/24/32 or IEEE float32/64; compressed audio is unsupported")
                if channels < 1 or rate < 1 or block != channels * (bits // 8) or byte_rate != rate * block:
                    raise ValueError("invalid WAV rate/channel/block alignment")
                fmt = dict(encoding="PCM" if encoding == 1 else "IEEE_FLOAT", channels=channels,
                           sample_rate_hz=rate, bits_per_sample=bits, block_align=block)
            else:
                if tag == b"data":
                    data_sizes.append(size)
                handle.seek(size, 1)
            if size % 2:
                if len(handle.read(1)) != 1:
                    raise ValueError("WAV odd-length chunk lacks pad byte")
        if fmt is None or len(data_sizes) != 1 or data_sizes[0] % fmt["block_align"]:
            raise ValueError("WAV requires one format and one whole-sample data chunk")
        fmt["sample_count"] = data_sizes[0] // fmt["block_align"]
        return fmt


def read_wav_float(path: Path, channel: str = "left", start_s: float = 0.0,
                   seconds: float | None = None) -> tuple[np.ndarray, dict]:
    """Read, choose a channel, and anti-alias resample. No amplitude normalization.

    ``left`` selects channel 0; ``right`` requires a channel 1; ``mean`` averages
    every channel. Trimming is explicit and occurs before resampling.
    """
    path = Path(path).resolve()
    if channel not in ("left", "right", "mean"):
        raise ValueError("channel must be left, right or mean")
    if not np.isfinite(start_s) or start_s < 0 or (seconds is not None and (not np.isfinite(seconds) or seconds <= 0)):
        raise ValueError("start must be nonnegative; duration must be positive and finite")
    fmt = _wav_format(path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rate, raw = wavfile.read(path)
    if rate != fmt["sample_rate_hz"] or len(raw) != fmt["sample_count"]:
        raise ValueError("decoded WAV samples disagree with header")
    if raw.dtype.kind == "u" and raw.dtype.itemsize == 1:
        data = (raw.astype(np.float64) - 128.0) / 128.0
    elif raw.dtype.kind == "i":
        # SciPy left-justifies PCM24 in int32, so this also handles PCM24.
        data = raw.astype(np.float64) / float(2 ** (8 * raw.dtype.itemsize - 1))
    elif raw.dtype.kind == "f":
        data = raw.astype(np.float64)
    else:
        raise ValueError("unsupported decoded WAV dtype")
    if not np.all(np.isfinite(data)):
        raise ValueError("WAV contains NaN or infinity")
    if data.ndim == 1:
        if channel == "right":
            raise ValueError("right channel requested for mono WAV")
    elif data.ndim == 2 and data.shape[1] == fmt["channels"]:
        if channel == "right" and data.shape[1] < 2:
            raise ValueError("right channel requested without channel 1")
        data = data.mean(axis=1) if channel == "mean" else data[:, int(channel == "right")]
    else:
        raise ValueError("WAV channel dimensions are invalid")
    start = int(round(start_s * rate))
    stop = len(data) if seconds is None else start + int(round(seconds * rate))
    if start >= len(data) or stop > len(data) or stop <= start:
        raise ValueError("requested source interval is empty or exceeds WAV duration; no padding/truncation is automatic")
    data = data[start:stop]
    divisor = gcd(int(rate), FS)
    up, down = FS // divisor, int(rate) // divisor
    if up != down:
        data = resample_poly(data, up, down)
    _length(len(data))
    if not np.all(np.isfinite(data)):
        raise ValueError("WAV conversion produced nonfinite samples")
    return data, dict(source_path=str(path), source_sha256=digest(path), source_format=fmt,
        channel_policy=channel, source_start_sample=start, source_stop_sample_exclusive=stop,
        output_sample_rate_hz=FS, output_sample_count=len(data),
        resampling=dict(method="scipy.signal.resample_poly", up=up, down=down,
                        window="default Kaiser beta=5.0", amplitude_normalization=False),
        decoder_warnings=[str(item.message) for item in caught])


def quantize_common(components: dict[str, np.ndarray], names: list[str],
                    force_gain: float | None = None) -> tuple[np.ndarray, dict, dict]:
    """One common gain only if int16 would overflow; no per-sample clipping."""
    mixture = np.zeros_like(components[names[0]], dtype=np.float64)
    for name in names:
        mixture += components[name]
    if not np.all(np.isfinite(mixture)):
        raise ValueError("nonfinite mixture")
    maximum = float(np.max(np.abs(mixture)))
    with np.errstate(over="ignore"):
        original_rounded = np.rint(mixture * 32768)
    overflow = original_rounded.max() > 32767 or original_rounded.min() < -32768
    gain = (min(1.0, 0.95 / maximum) if overflow else 1.0) if force_gain is None else float(force_gain)
    if not np.isfinite(gain) or not 0 < gain <= 1:
        raise ValueError("common gain must be finite and in (0,1]")
    scaled = {key: np.asarray(value, dtype=np.float64) * gain for key, value in components.items()}
    mixture.fill(0)
    for name in names:
        mixture += scaled[name]
    rounded = np.rint(mixture * 32768)
    if rounded.min() < -32768 or rounded.max() > 32767:
        raise ValueError("common gain would overflow int16; no samples clipped")
    samples = rounded.astype("<i2")
    scaled["quantization_error"] = samples.astype(np.float64) / 32768 - mixture
    return samples, dict(scale=gain, scale_policy="one common gain only when needed; no clipping",
        pre_scale_max_abs=maximum, actual_max_abs_sample=int(np.max(np.abs(samples.astype(np.int32))))), scaled


def _load_json(path: Path) -> dict:
    def reject(value):
        raise ValueError(f"nonfinite JSON value {value}")
    data = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject)
    if not isinstance(data, dict):
        raise ValueError(f"metadata must be an object: {path.name}")
    return data


def load_import_bundle(path: Path, channel: str = "left") -> tuple[np.ndarray, dict, dict]:
    """Load an ordinary input or verified scene.save_scene-compatible bundle.

    A sidecar pair is mandatory if either source_metadata.json/components.npz
    exists. References are accepted only for unchanged mono32k PCM16 or NPY
    payloads with matching file/payload/component hashes and exact reconstruction.
    Unverified sidecars are errors rather than silently ignored references.
    """
    path = Path(path).resolve()
    if path.suffix.lower() == ".wav":
        fmt = _wav_format(path)
        if fmt["encoding"] != "PCM" or fmt["bits_per_sample"] != 16 or fmt["channels"] != 1 or fmt["sample_rate_hz"] != FS:
            raise ValueError("GUI input must already be mono32000Hz PCM16 WAV; run prepare_wav.py convert/mix-clean explicitly first")
        values, conversion = read_wav_float(path, channel)
        data, scale, _ = quantize_common({"input": values}, ["input"])
        conversion.update(scale)
    elif path.suffix.lower() == ".npy":
        raw = np.load(path, allow_pickle=False)
        if raw.ndim != 1 or raw.dtype.kind != "i" or raw.dtype.itemsize != 2:
            raise ValueError("NPY import must be a one-dimensional signed int16 array at 32000 Hz")
        data = raw.astype("<i2", copy=True)
        _length(len(data))
        conversion = dict(source_path=str(path), source_sha256=digest(path),
            source_format="NPY int16; sample rate explicitly assumed 32000 Hz", scale=1.0)
    else:
        raise ValueError("input must be WAV or NPY")
    meta_path, comps_path = path.parent / "source_metadata.json", path.parent / "components.npz"
    if meta_path.exists() != comps_path.exists():
        raise ValueError("incomplete reference bundle: source_metadata.json and components.npz must both exist")
    metadata = dict(fs_hz=FS, sample_rate_hz=FS, sample_count=len(data),
        imported_input=str(path), import_conversion=conversion, clean_reference_available=False,
        reference_use="offline display/scoring only; not transmitted to FPGA")
    if not meta_path.exists():
        import_meta = path.parent / "import_metadata.json"
        if import_meta.exists():
            declared = _load_json(import_meta)
            if declared.get("input_wav_sha256" if path.suffix.lower() == ".wav" else "input_npy_sha256") != digest(path):
                raise ValueError("conversion metadata input file SHA256 mismatch")
            metadata["prepared_conversion"] = declared
        return data, metadata, {}
    source = _load_json(meta_path)
    key = "input_wav_sha256" if path.suffix.lower() == ".wav" else "input_npy_sha256"
    if source.get(key) != digest(path) or source.get("components_npz_sha256") != digest(comps_path):
        raise ValueError("reference bundle input/components file SHA256 mismatch")
    if path.suffix.lower() == ".wav":
        fmt = conversion["source_format"]
        if fmt["encoding"] != "PCM" or fmt["bits_per_sample"] != 16 or fmt["channels"] != 1 or fmt["sample_rate_hz"] != FS or conversion["scale"] != 1:
            raise ValueError("reference bundle WAV must already be mono32k PCM16; convert/mix the entire bundle offline first")
    if source.get("fs_hz") != FS or source.get("sample_count") != len(data) or source.get("input_sha256") != sha256(data.tobytes()).hexdigest():
        raise ValueError("reference bundle rate/count/payload mismatch")
    gain = source.get("scale")
    if isinstance(gain, bool) or not isinstance(gain, (float, int)) or not np.isfinite(gain) or not 0 < gain <= 1:
        raise ValueError("reference bundle common scale is invalid")
    names = source.get("mixture_component_names")
    if not isinstance(names, list) or not names or not all(isinstance(x, str) for x in names) or len(names) != len(set(names)):
        raise ValueError("reference bundle mixture component names are invalid")
    components = {}
    with np.load(comps_path, allow_pickle=False) as archive:
        for name in archive.files:
            raw = archive[name]
            if raw.ndim != 1 or len(raw) != len(data) or raw.dtype.kind not in "fiu" or not np.all(np.isfinite(raw)):
                raise ValueError(f"invalid reference component: {name}")
            components[name] = raw.astype(np.float64, copy=True)
    if "target" not in names or "quantization_error" not in components or any(name not in components for name in names):
        raise ValueError("reference bundle lacks target/quantization_error/mixture component")
    mixture = np.zeros(len(data), dtype=np.float64)
    for name in names:
        mixture += components[name]
    if not np.allclose(mixture + components["quantization_error"], data.astype(np.float64) / 32768, rtol=0, atol=2e-12):
        raise ValueError("reference component sum does not reconstruct input at the declared common scale")
    if np.max(np.abs(components["quantization_error"])) > 0.500001 / 32768:
        raise ValueError("reference quantization residual exceeds int16 rounding tolerance")
    # save_scene records both before/after scale RMS; reject separately rescaled references.
    before, after = source.get("pre_scale_component_rms"), source.get("component_rms")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("reference bundle lacks common-scale RMS provenance")
    for name in names:
        actual = float(np.sqrt(np.mean(components[name] ** 2)))
        if name not in before or name not in after or not np.isclose(actual, after[name], rtol=1e-10, atol=1e-14) or not np.isclose(actual, before[name] * gain, rtol=1e-10, atol=1e-14):
            raise ValueError(f"reference component common-scale mismatch: {name}")
    metadata.update(source)
    metadata.update(imported_input=str(path), import_conversion=conversion,
                    clean_reference_available=True, reference_bundle_verified=True,
                    reference_use="offline display/scoring only; not transmitted to FPGA")
    return data, metadata, components

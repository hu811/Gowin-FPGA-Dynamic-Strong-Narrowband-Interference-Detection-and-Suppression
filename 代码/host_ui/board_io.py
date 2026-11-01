"""V31 GUI adapter to the preserved, validated digital UART transport.

No serial port opens on import or release verification. Capture is a blocking
worker operation; callbacks run on that worker and must remain nonblocking.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np

from vendor_stream import stream
from vendor_stream.protocol import Snapshot

FS = 32_000
MAX_SAMPLES = 1_048_575
MIN_SAMPLES = 64
CaptureFailure = stream.CaptureFailure
load_input = stream.load_input


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_release(root: Path, capacity: int) -> dict:
    """Check the local V31 image, never the image currently loaded in the FPGA."""
    if capacity not in (3, 4):
        raise ValueError("Capacity must be 3 or 4")
    folder = Path(root).resolve() / f"c{capacity}"
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    record = manifest.get("bitstream", {})
    filename = record.get('filename', f'ACG525_C{capacity}_V31_512.fs')
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.fs'):
        raise ValueError('Invalid bitstream filename in local release manifest')
    fs = folder / filename
    if manifest.get("capacity") != capacity or not fs.is_file():
        raise ValueError("Local release capacity/bitstream mismatch")
    digest = _sha(fs)
    if digest != record.get("sha256") or fs.stat().st_size != record.get("bytes"):
        raise ValueError("Local bitstream size/SHA does not match manifest")
    return {
        "capacity": capacity, "local_file_verified": True,
        "fpga_image_readback_verified": False,
        "bitstream": str(fs), "bitstream_sha256": digest,
        "bitstream_bytes": fs.stat().st_size,
        "manifest": str(manifest_path), "manifest_sha256": _sha(manifest_path),
        "scope": "Local release-file verification only; not FPGA image readback or board testing.",
    }


def _save(directory: Path, data: np.ndarray, output: np.ndarray,
          rows: list[dict], metadata: dict) -> None:
    stream.save_capture(directory, data, output, rows, metadata)
    # Preserve a machine-readable empty table for failures before any status.
    if not rows:
        fields = list(Snapshot().row()) + ["host_elapsed_s", "post_complete_observation"]
        with (directory / "status.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            csv.DictWriter(handle, fieldnames=fields).writeheader()


def capture(port: str, capacity: int, data: np.ndarray, directory: Path,
            source_metadata: dict | None = None, on_frame=None,
            on_log=None, cancelled=None) -> dict:
    """Stream mixed int16 input and return/save real received output.

Cancellation raises CaptureFailure with metadata.cancelled=True and sends ABORT
if a connection has been established. Partial output/status evidence is saved.
Only this explicit call opens a serial connection; it never programs the board.
"""
    if capacity not in (3, 4):
        raise ValueError("Capacity must be 3 or 4")
    if not isinstance(data, np.ndarray) or data.ndim != 1 or data.dtype.kind != "i" or data.dtype.itemsize != 2:
        raise ValueError("Input must be a one-dimensional signed int16 array")
    if not MIN_SAMPLES <= len(data) <= MAX_SAMPLES:
        raise ValueError(f"Input length must be {MIN_SAMPLES}..{MAX_SAMPLES} samples")
    if not isinstance(port, str) or not port.strip():
        raise ValueError("An explicit board UART port is required")
    if source_metadata is not None and source_metadata.get("sample_rate_hz", FS) != FS:
        raise ValueError("Input sample rate must be 32000 Hz; no resampling is performed")
    if source_metadata is not None and source_metadata.get("capacity", capacity) != capacity:
        raise ValueError("Scene and requested FPGA capacity disagree")
    directory = Path(directory).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Capture directory must be new or empty; previous evidence is never overwritten")
    directory.mkdir(parents=True, exist_ok=True)
    data = np.asarray(data, dtype=np.int16).copy()
    metadata = {
        "schema": "v32-gui-capture-2", "created_utc": datetime.now(timezone.utc).isoformat(),
        "capacity": capacity, "port": port.strip(), "baud": stream.BAUD,
        "sample_rate_hz": FS, "source_metadata": source_metadata,
        "fpga_image_readback_verified": False,
        "adapter_sha256": _sha(Path(__file__)),
        "notes": [
            "Only mixed input samples and sequence numbers are sent; all online analysis/filtering runs in FPGA.",
            "Any local release digest in source_metadata is a host file check, not FPGA image readback.",
            "USB wall time is not detector latency. UART frequency fields are authorized tracks, not actual FIR-bank centers.",
            "Final 50ms/status drain does not prove every queued coefficient task/fade completed; no extra samples are added.",
            "Inputs below 512 samples have no main observation; below 1024 have no auxiliary observation.",
            "A transport pass is not attenuation/target-protection/30ms quality acceptance.",
        ],
    }
    log = on_log if on_log is not None else (lambda message: None)
    output = np.empty(0, dtype=np.int16)
    rows = []
    try:
        # Imported only when the caller explicitly starts a capture.
        import serial
        with serial.Serial(port.strip(), stream.BAUD, bytesize=8, parity="N", stopbits=1,
                           timeout=0, write_timeout=0.05, rtscts=False, dsrdtr=False) as connection:
            output, rows, result = stream.run_serial(
                connection, data, capacity, progress=log, on_frame=on_frame, cancelled=cancelled)
            metadata.update(result)
        _save(directory, data, output, rows, metadata)
        return {"output": output, "rows": rows, "metadata": metadata, "directory": str(directory)}
    except CaptureFailure as exc:
        exc.metadata = {**metadata, **exc.metadata}
        exc.directory = str(directory)
        _save(directory, data, exc.output, exc.rows, exc.metadata)
        raise
    except (Exception, KeyboardInterrupt) as exc:
        result = {**metadata, "transport_valid": False, "board_verified": False,
                  "failure": str(exc) or "Interrupted", "cancelled": False,
                  "samples_sent": metadata.get("samples_sent", 0), "samples_received": len(output),
                  "detector_response_ms": None, "quality_pass": None}
        partial = output.copy()
        failure = CaptureFailure(str(exc) or "Interrupted", partial, rows, result)
        failure.directory = str(directory)
        _save(directory, data, partial, rows, result)
        raise failure from exc

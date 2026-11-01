"""Validate saved board evidence and score the same samples against software.

No serial, GUI, detector, or input generation is performed here.  The caller
supplies host_ui on sys.path; its original scoring definitions stay unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from metrics import compute_metrics


FS = 32000


def _samples(values, name):
    values = np.asarray(values)
    if values.ndim != 1 or values.dtype.kind != "i" or values.dtype.itemsize != 2:
        raise ValueError(f"{name} must be a one-dimensional signed int16 array")
    return values


def payload(values):
    """Return canonical little-endian int16 payload bytes, without an NPY header."""
    return _samples(values, "samples").astype("<i2", copy=False).tobytes(order="C")


def load_actual_capture(directory, expected_input):
    """Load a complete, hash-checked capture of exactly expected_input.

    board_verified denotes the existing transport's board/protocol checks.  It
    does not claim FPGA image readback or successful filter-quality acceptance.
    """
    directory = Path(directory)
    expected = _samples(expected_input, "expected_input")
    if not len(expected):
        raise ValueError("expected_input must not be empty")
    metadata = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("capture metadata must be an object")
    for name in ("board_verified", "transport_valid", "start_ack_seen", "complete_seen"):
        if metadata.get(name) is not True:
            raise ValueError(f"capture is not complete actual board evidence: {name}")
    if metadata.get("failure") or metadata.get("failed") or metadata.get("synthetic") or metadata.get("mock"):
        raise ValueError("failed or synthetic capture cannot be compared as actual board output")
    if metadata.get("sample_rate_hz") != FS:
        raise ValueError("capture sample rate must be 32000 Hz")
    for name in ("samples_sent", "samples_received", "input_admitted", "input_consumed"):
        if type(metadata.get(name)) is not int or metadata[name] != len(expected):
            raise ValueError(f"capture count is incomplete or inconsistent: {name}")

    saved_input = _samples(np.load(directory / "input.npy", allow_pickle=False), "saved input")
    output = _samples(np.load(directory / "output.npy", allow_pickle=False), "saved output")
    if len(saved_input) != len(expected) or not np.array_equal(saved_input, expected):
        raise ValueError("capture input differs from the selected comparison input")
    if len(output) != len(expected):
        raise ValueError("capture output is not a complete same-length sample sequence")
    for name, values in (("input_sha256", saved_input), ("output_sha256", output)):
        if metadata.get(name) != hashlib.sha256(payload(values)).hexdigest():
            raise ValueError(f"capture payload hash mismatch: {name}")
    return output.astype(np.int16, copy=True), metadata


def measure_pair(data, board_output_or_None, software_output_or_None,
                 target_or_None, metadata, start, width):
    """Use the original host metrics with one fixed 320-sample aligned window.

    Outputs are complete sequences or None; incomplete captures are rejected.
    Positive relative_reduction_pct means lower board NRMSE than the software
    reference.  Without a target, both fidelity measurements remain unavailable.
    """
    data = _samples(data, "data")
    outputs = {}
    for name, values in (("board", board_output_or_None), ("software", software_output_or_None)):
        if values is None:
            outputs[name] = None
        else:
            values = _samples(values, f"{name} output")
            if len(values) != len(data):
                raise ValueError(f"{name} output must be complete and match input length")
            outputs[name] = values

    measured = {
        name: None if values is None else compute_metrics(
            data, values, target_or_None, metadata, start, width, align=True)
        for name, values in outputs.items()
    }
    available = next((value for value in measured.values() if value is not None), None)
    if available is None:
        available = compute_metrics(data, np.empty(0, dtype=np.int16), target_or_None,
                                    metadata, start, width, align=True)
    relative = None
    if all(measured[name] is not None for name in ("board", "software")):
        board_error = measured["board"]["output_nrmse_pct"]
        software_error = measured["software"]["output_nrmse_pct"]
        if (board_error is not None and software_error is not None
                and math.isfinite(board_error) and math.isfinite(software_error)
                and board_error > 0 and software_error > 0):
            relative = 100.0 * (software_error - board_error) / software_error
    result = dict(
        board=measured["board"], software=measured["software"],
        relative_reduction_pct=relative,
        common_samples=available["common_samples"],
        first_sample=available["first_sample"],
        stop_sample_exclusive=available["stop_sample_exclusive"],
    )
    # Reject non-finite diagnostics rather than emitting non-standard JSON.
    json.dumps(result, allow_nan=False)
    return result

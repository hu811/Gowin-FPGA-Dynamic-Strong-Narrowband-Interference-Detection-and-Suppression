"""Offline analysis of two completed, actual UART board captures; no serial I/O."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np

FS, DELAY = 32000, 320
WINDOWS = {"full_common": (0.0, 1.99), "stable": (0.4, 0.8), "legacy_local": (0.4, 0.09)}
COUNTERS = ("dropped", "numeric", "deadline", "sequence_faults", "saturated",
            "link_faults", "error_code", "max_fir", "waiting", "task_running",
            "commits", "observations", "input_level", "output_level")


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(values):
    return hashlib.sha256(values.astype("<i2").tobytes()).hexdigest()


def _load(directory):
    meta = _json(directory / "capture.json")
    if meta.get("board_verified") is not True or meta.get("transport_valid") is not True:
        raise ValueError(f"Not a completed verified board capture: {directory}")
    for key in ("synthetic", "synthetic_output", "mock_output", "mock", "simulated", "simulated_output", "protocol_simulation_pass", "rtl_simulation"):
        if meta.get(key):
            raise ValueError(f"Synthetic/simulation evidence rejected: {directory}: {key}")
    if meta.get("schema") != "v32-gui-capture-2" or not meta.get("port"):
        raise ValueError(f"Expected explicit board_io UART capture provenance: {directory}")
    if meta.get("start_ack_seen") is not True or meta.get("complete_seen") is not True:
        raise ValueError(f"Missing board START/COMPLETE acknowledgement: {directory}")
    data = np.load(directory / "input.npy", allow_pickle=False)
    output = np.load(directory / "output.npy", allow_pickle=False)
    for name, array in (("input", data), ("output", output)):
        if array.ndim != 1 or array.dtype != np.dtype("int16") or len(array) != 64000:
            raise ValueError(f"Expected complete 64000-sample int16 {name}: {directory}")
        if meta.get(name + "_sha256") != _sha(array):
            raise ValueError(f"Saved {name} payload hash mismatch: {directory}")
    for key in ("samples_sent", "samples_received", "input_admitted", "input_consumed"):
        if meta.get(key) != len(data):
            raise ValueError(f"Incomplete sample count {key}: {directory}")
    if meta.get("sample_rate_hz") != FS:
        raise ValueError(f"Sample-rate mismatch: {directory}")
    with np.load(directory / "source_components.npz", allow_pickle=False) as parts:
        reference = np.asarray(parts["target"], dtype=float).copy()
    if reference.shape != data.shape or not np.all(np.isfinite(reference)):
        raise ValueError(f"Invalid recorded reference: {directory}")
    source = meta.get("source_metadata") or {}
    if source.get("reference_type") != "unaugmented_recorded_received_waveform":
        raise ValueError(f"Not the recorded-DRM reference bundle: {directory}")
    if source.get("input_sha256") != _sha(data):
        raise ValueError(f"Imported bundle/input mismatch: {directory}")
    rows = []
    if (directory / "status.csv").exists():
        with (directory / "status.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    return dict(directory=str(directory), metadata=meta, source=source,
                data=data, output=output, reference=reference, status=rows)


def _status(run):
    def numbers(key):
        return [float(r[key]) for r in run["status"] if r.get(key) not in (None, "")]
    maxima = {key: int(max(values)) if (values := numbers(key)) else None for key in COUNTERS}
    active = numbers("active")
    return dict(rows=len(run["status"]), maxima=maxima,
                active_masks_seen=sorted(set(map(int, active))) if active else None,
                rows_with_active=sum(v != 0 for v in active) if active else None,
                counter_encoding=run["metadata"].get("diagnostic_counter_encoding"),
                diagnostic_255_means_at_least=run["metadata"].get("diagnostic_255_means_at_least"),
                crc_errors=run["metadata"].get("crc_errors"),
                format_errors=run["metadata"].get("format_errors"),
                note="Observed status maxima; absent fields are null. Row counts are not active-time estimates.")


def _plot(runs, destination):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(13, 7.6), constrained_layout=True)
    styles = (("Reference recording", "#009E73", "-", 1.1),
              ("Input to FPGA", "#D55E00", "-", .8),
              ("Actual FPGA output", "#0072B2", "--", 1.0))
    for column, (name, run) in enumerate(runs.items()):
        for row, (start, stop) in enumerate(((.5, .51), (.4, 1.2))):
            a, b = round(start * FS), round(stop * FS)
            values = (run["reference"][a:b], run["data"][a:b] / 32768.,
                      run["output"][a + DELAY:b + DELAY] / 32768.)
            ax = axes[row, column]
            for x, (label, color, style, width) in zip(values, styles):
                if row == 0:
                    horizontal, vertical = np.arange(a, b) / FS * 1000, x
                else:
                    window = np.hanning(len(x))
                    amp = 2 * np.abs(np.fft.rfft(x * window)) / window.sum()
                    amp[0] *= .5
                    amp[-1] *= .5
                    horizontal = np.fft.rfftfreq(len(x), 1 / FS)
                    vertical = 20 * np.log10(np.maximum(amp, 1e-6))
                ax.plot(horizontal, vertical, label=label, color=color, ls=style, lw=width)
            ax.set_title(f"{name}: " + ("aligned waveform" if row == 0 else "stable-segment spectrum"))
            ax.set_xlabel("Reference time / ms" if row == 0 else "Frequency / Hz")
            ax.set_ylabel("Normalized sample" if row == 0 else "Amplitude / dBFS")
            ax.grid(color="#e8eaed", lw=.6)
            if row == 1:
                ax.set_xlim(1000, 14000)
                ax.set_ylim(-120, 0)
        axes[0, column].legend(fontsize=8)
    fig.suptitle("Actual-data plot: recorded DRM + injected tones\nFixed 320-sample alignment; reference retains original noise/channel/pilots")
    fig.savefig(destination, dpi=180, facecolor="white")
    plt.close(fig)


def analyze_session(run_root, release_root):
    """Analyze session/control and session/mixed, preserving all actual samples."""
    run_root, release_root = Path(run_root).resolve(), Path(release_root).resolve()
    host = release_root / "host_ui"
    sys.path.insert(0, str(host))
    spec = importlib.util.spec_from_file_location("recorded_board_metrics", host / "metrics.py")
    metrics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metrics)
    definition_path = Path(__file__).resolve().parent / "experiment.json"
    frozen = _json(definition_path)
    runs = {name: _load(run_root / name) for name in ("control", "mixed")}
    for name, run in runs.items():
        expected = frozen["inputs"][name]
        if _sha(run["data"]) != expected["payload_sha256"]:
            raise ValueError(f"Capture is not the frozen {name} input payload")
        parts_path = release_root / "real_recorded_inputs" / "prepared" / expected["folder"] / "components.npz"
        if hashlib.sha256(parts_path.read_bytes()).hexdigest() != expected["files"]["components.npz"]:
            raise ValueError(f"Frozen reference component file changed: {name}")
        with np.load(parts_path, allow_pickle=False) as parts:
            if not np.array_equal(run["reference"], parts["target"]):
                raise ValueError(f"Saved reference does not match the frozen recording: {name}")
    control, mixed = runs.values()
    if not np.array_equal(control["reference"], mixed["reference"]):
        raise ValueError("Control and mixed runs do not have the identical recorded reference")
    if control["source"].get("scale") != mixed["source"].get("scale"):
        raise ValueError("Control/mixed front-end gains differ")
    report = dict(schema="recorded-board-pair-analysis-1", board_capture_pair_verified=True,
                  experiment_definition_sha256=hashlib.sha256(definition_path.read_bytes()).hexdigest(),
                  reference="Unaugmented receiver recording, including original noise/channel/pilots; not noise-free transmitter truth",
                  alignment_samples=DELAY, gain_fitting=False, sample_rate_hz=FS,
                  evidence_limit="Host files validate saved UART provenance, not cryptographic FPGA image readback. Mixed-band/midpoint spectra do not certify isolated 20 dB / 1 dB acceptance or BER.",
                  windows={key: {"start_s": start, "stop_s_exclusive": start + width} for key, (start, width) in WINDOWS.items()}, runs={})
    flat = []
    for name, run in runs.items():
        measured = {key: metrics.compute_metrics(run["data"], run["output"], run["reference"],
                    run["source"], start, width, align=True) for key, (start, width) in WINDOWS.items()}
        report["runs"][name] = dict(directory=run["directory"], status=_status(run), metrics=measured)
        for window, value in measured.items():
            common = dict(run=name, window=window, samples=value["common_samples"])
            flat.extend({**common, "metric": key, "value": value[key], "unit": unit, "detail": "fixed alignment; no gain fitting"}
                        for key, unit in (("input_nrmse_pct", "%"), ("output_nrmse_pct", "%"), ("error_reduction_db", "dB")))
            for category, key in (("interferers", "attenuation_db"), ("midpoints", "spectral_change_db")):
                for item in value[category]:
                    flat.append({**common, "metric": category + ":" + str(item.get("event_index", item.get("pair"))),
                                 "value": item[key], "unit": "dB", "detail": item.get("reason", "")})
    (run_root / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    with (run_root / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("run", "window", "samples", "metric", "value", "unit", "detail"))
        writer.writeheader()
        writer.writerows(flat)
    _plot(runs, run_root / "actual_data_comparison.png")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--release-root", required=True, type=Path)
    args = parser.parse_args()
    result = analyze_session(args.session, args.release_root)
    print(json.dumps({"analysis": str(args.session / "analysis.json"), "runs": list(result["runs"])}, ensure_ascii=False))

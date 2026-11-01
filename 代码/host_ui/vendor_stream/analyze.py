"""Offline plots and exploratory mixed-signal projections; not formal component scoring."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy import signal


def evaluate(directory: Path, source_metadata: dict | None = None, plot: bool = True) -> dict:
    x, y = np.load(directory / "input.npy"), np.load(directory / "output.npy")
    if x.dtype != np.int16 or y.dtype != np.int16 or len(x) != len(y):
        raise ValueError("Evaluation needs equal-length signed int16 input/output")
    delay, fs = 320, 32000
    capture_path = directory / "capture.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8")) if capture_path.exists() else {}
    is_board_capture = capture.get("board_verified") is True and not capture.get("synthetic", False)
    aligned_count = max(0, len(x) - delay)
    x_aligned, y_aligned = x[:aligned_count].astype(float), y[delay:].astype(float)
    report = {
        "sample_rate_hz": fs, "alignment_delay_samples": delay,
        "alignment_delay_ms": 10.0, "formal_quality_pass": None,
        "board_verified": is_board_capture,
        "capture_origin": "actual_fpga" if is_board_capture else "unverified_or_synthetic",
        "input_samples": len(x), "aligned_samples": aligned_count,
        "analysis_status": "ok" if aligned_count > 1024 else "insufficient_data",
        "limitations": [
            "These are projections of mixed input/output, not separated interference powers.",
            "Random target energy, colored residuals, transitions and finite windows bias projections.",
            "No formal >=20 dB attenuation, <=1 dB damage or <=30 ms response pass is assigned.",
            "Formal component damage requires replaying the saved actual FPGA coefficient/fade trajectory on isolated components.",
            "Alignment only compares signal shape; 10 ms must remain in end-to-end response time.",
        ],
        "mixed_input_rms_counts": float(np.sqrt(np.mean(x_aligned**2))) if aligned_count else None,
        "mixed_output_rms_counts": float(np.sqrt(np.mean(y_aligned**2))) if aligned_count else None,
        "mixed_aligned_rms_difference_counts": float(np.sqrt(np.mean((x_aligned-y_aligned)**2))) if aligned_count else None,
        "exploratory_projections": [],
    }
    if source_metadata and len(x_aligned) > 1024:
        frequencies = source_metadata.get("frequencies_hz", []) + source_metadata.get("probe_frequencies_hz", [])
        for event in source_metadata.get("events", []):
            # Stay 80 ms away from edges. Never silently score a transient as steady state.
            start = int((event["start_s"] + 0.08) * fs)
            end = min(int((event["end_s"] - 0.08) * fs), len(x_aligned))
            if end - start < 2048:
                continue
            t = np.arange(start, end) / fs
            design = np.column_stack([part for f in frequencies
                                      for part in (np.cos(2*np.pi*f*t), np.sin(2*np.pi*f*t))])
            a = np.linalg.lstsq(design, x_aligned[start:end], rcond=None)[0]
            b = np.linalg.lstsq(design, y_aligned[start:end], rcond=None)[0]
            for i, frequency in enumerate(frequencies):
                ai, bi = float(np.hypot(*a[2*i:2*i+2])), float(np.hypot(*b[2*i:2*i+2]))
                report["exploratory_projections"].append({
                    "window_start_s": start/fs, "window_end_s": end/fs,
                    "frequency_hz": frequency, "input_projection_peak_counts": ai,
                    "output_projection_peak_counts": bi,
                    "mixed_projection_output_over_input_db": float(20*np.log10(max(bi,1e-12)/max(ai,1e-12))),
                    "formal_component_metric": False,
                })
    (directory / "exploratory_analysis.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    if plot and len(x_aligned) > 1024:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 1, figsize=(11, 7), constrained_layout=True)
        offset = min(int(.6*fs), max(0, len(x_aligned)-3200))
        n = min(3200, len(x_aligned)-offset)
        time_axis = np.arange(offset, offset+n)/fs
        axes[0].plot(time_axis, x_aligned[offset:offset+n]/32768, label="Input")
        axes[0].plot(time_axis, y_aligned[offset:offset+n]/32768,
                     label="Output (320-sample aligned)", alpha=.8)
        title = "Actual FPGA capture" if is_board_capture else "Unverified / synthetic capture"
        axes[0].set(xlabel="Input sample time (s)", ylabel="FS", title=f"{title}: {n/fs*1000:.1f} ms excerpt")
        axes[0].legend()
        for data, name in ((x_aligned, "Input"), (y_aligned, "Output")):
            frequency, psd = signal.welch(data/32768, fs, nperseg=min(4096, len(data)))
            axes[1].plot(frequency, 10*np.log10(np.maximum(psd, 1e-18)), label=name)
        axes[1].set(xlabel="Frequency (Hz)", ylabel="PSD (dB FS^2/Hz)", xlim=(0, 8000),
                    title="Mixed spectra: exploratory, no component-quality pass assigned")
        axes[1].legend()
        fig.savefig(directory / "input_output.png", dpi=150)
        plt.close(fig)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source-metadata", type=Path)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    evaluate(args.directory, json.loads(args.source_metadata.read_text(encoding="utf-8"))
             if args.source_metadata else None, not args.no_plot)

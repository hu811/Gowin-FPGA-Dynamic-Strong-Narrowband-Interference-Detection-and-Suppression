"""V19 C3/C4 finite digital input/output validation over 2 Mbaud UART."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
from scipy.io import wavfile

from .protocol import (ABORT, COMPLETE, DATA, FAULT, OUTPUT, RESET, START,
                      START_ACCEPTED, FrameParser, RunValidator, Snapshot,
                      ValidationError, available_credit, encode_command, encode_snapshot,
                      DIAGNOSTIC_U8_SATURATING, FINITE_CAPTURE_20,
                      FINITE_CAPTURE_20_MAX_SAMPLES, UINT32_MAX_SAMPLES)
from .fixtures import FS, SCENARIOS, SEED, save_fixture

BAUD = 2_000_000
POST_COMPLETE_SECONDS = 0.050


def load_input(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".npy":
        data = np.load(path, allow_pickle=False)
    elif path.suffix.lower() == ".wav":
        rate, data = wavfile.read(path)
        if rate != FS:
            raise ValueError(f"WAV must be {FS} Hz; got {rate}. No implicit resampling is performed.")
    else:
        raise ValueError("Input must be .npy or 16-bit mono .wav")
    if data.ndim != 1 or data.dtype.kind != "i" or data.dtype.itemsize != 2:
        raise ValueError("Input must be one-dimensional signed int16 (mono); no implicit scaling")
    if not 1 <= len(data) <= 0xFFFFFFFF:
        raise ValueError("Input must contain 1..4294967295 samples")
    return np.asarray(data, dtype=np.int16)


def describe(frame: Snapshot, received: int, total: int) -> str:
    hz = ",".join(f"{f*FS/2**32:.1f}" for i, f in enumerate(frame.frequencies)
                  if frame.active & (1 << i)) or "-"
    return (f"C{frame.capacity} received={received}/{total} in={frame.consumed}/{frame.admitted} "
            f"Hz=[{hz}] active=0x{frame.active:02x} commits={frame.commits} "
            f"FIFO={frame.input_level}/{frame.output_level} FIR={frame.max_fir} "
            f"link=0x{frame.link_faults:04x} core=0x{frame.error_code:02x}")


def run_serial(serial_port, data: np.ndarray, capacity: int, *, timeout: float = 3.0,
               progress=print, on_frame=None, cancelled=None) -> tuple[np.ndarray, list[dict], dict]:
    """Bounded transport; USB wall time is never reported as detector response time.

    Any integrity fault raises CaptureFailure with the partial capture. The host
    neither resamples nor supplies detector truth/filter coefficients.
    """
    parser = FrameParser()
    validator = RunValidator(capacity, len(data))
    output = np.empty(len(data), dtype=np.int16)
    rows = []
    sent = 0
    start_sent = False
    reset_ack = False
    startup_crc_errors = startup_format_errors = startup_stale_frames = 0
    beginning = last_progress = time.perf_counter()
    printed = beginning - 1
    reset_deadline = beginning + timeout
    expected_observations = max(0, (len(data)-512)//64+1)
    tail_started = None
    tail_status_frames = 0
    tail_status = None
    tail_status_time = None
    io_service = dict(clock="QueryPerformanceCounter via perf_counter_ns",
        scope="Host service diagnostics only; not FPGA detector/filter latency",
        block_samples=64, prefill_samples=256, outstanding_credit_samples=384,
        read_calls=0, write_calls=0, callback_calls=0,
        max_read_gap_ns=0, max_parse_batch_ns=0, max_callback_ns=0,
        max_write_call_ns=0, max_read_batch_frames=0, max_refill_frames=0,
        min_running_input_level=None)
    previous_read_ns = None

    def write(packet: bytes) -> None:
        if packet[4] != ABORT and cancelled is not None and cancelled():
            raise _CaptureCancelled("Capture cancelled by user")
        write_started = time.perf_counter_ns()
        try:
            count = serial_port.write(packet)
        finally:
            io_service["write_calls"] += 1
            io_service["max_write_call_ns"] = max(io_service["max_write_call_ns"], time.perf_counter_ns()-write_started)
        if count != len(packet):
            raise ValidationError(f"Short serial write ({count}/{len(packet)}); do not retry an ambiguous partial frame")

    def diagnostic_metadata() -> dict:
        features=validator.features
        return {'host_io_service':dict(io_service),'features':features,'diagnostic_counter_encoding':
                ('unknown_no_valid_status' if features is None else
                 'uint8_saturating_zero_extended_to_uint32' if features & DIAGNOSTIC_U8_SATURATING else 'uint32_exact'),
                'diagnostic_255_means_at_least':bool(features is not None and features & DIAGNOSTIC_U8_SATURATING),
                'max_capture_samples':(None if features is None else FINITE_CAPTURE_20_MAX_SAMPLES
                                       if features & FINITE_CAPTURE_20 else UINT32_MAX_SAMPLES)}

    try:
        serial_port.reset_input_buffer()
        serial_port.reset_output_buffer()
        write(encode_command(RESET))
        while True:
            if cancelled is not None and cancelled():
                raise _CaptureCancelled("Capture cancelled by user")
            now = time.perf_counter()
            read_started = time.perf_counter_ns()
            if previous_read_ns is not None:
                io_service["max_read_gap_ns"] = max(io_service["max_read_gap_ns"], read_started-previous_read_ns)
            previous_read_ns = read_started
            io_service["read_calls"] += 1
            incoming = serial_port.read(min(max(serial_port.in_waiting, 0), 32768))
            parse_started = time.perf_counter_ns()
            parser.feed(incoming)
            io_service["max_parse_batch_ns"] = max(io_service["max_parse_batch_ns"], time.perf_counter_ns()-parse_started)
            io_service["max_read_batch_frames"] = max(io_service["max_read_batch_frames"], len(parser.events))
            for frame in parser.events:
                if isinstance(frame, str):
                    if reset_ack:
                        raise ValidationError(f"Corrupt FPGA frame after RESET acknowledgement: {frame}")
                    startup_crc_errors += int(frame == "crc_error")
                    startup_format_errors += int(frame == "format_error")
                    continue
                if frame.capacity != capacity:
                    raise ValidationError(f"Board is C{frame.capacity}; launcher is C{capacity}. Select the matching .fs/launcher.")
                if not reset_ack:
                    if (frame.kind != OUTPUT and frame.consumed == 0 and frame.admitted == 0
                            and frame.first_index == 0 and frame.flags == 0
                            and not frame.link_faults and not frame.error_code
                            and not any((frame.dropped, frame.numeric, frame.deadline,
                                         frame.sequence_faults, frame.saturated, frame.commits,
                                         frame.observations, frame.active, frame.groups,
                                         frame.input_level, frame.output_level,
                                         frame.waiting, frame.task_running))):
                        reset_ack = True
                        last_progress = now
                        progress(f"RESET acknowledged by V19 C{capacity}; prefill 256, credit limit 384 samples.")
                    else:
                        startup_stale_frames += 1
                        continue  # Old in-flight status can precede the coherent reset snapshot.
                row = frame.row()
                row["host_elapsed_s"] = now - beginning
                row["post_complete_observation"] = tail_started is not None
                rows.append(row)
                previous = (validator.next_output, validator.last_admitted, validator.last_consumed)
                validator.accept(frame, sent)
                if frame.flags & 1:
                    current_min = io_service["min_running_input_level"]
                    io_service["min_running_input_level"] = frame.input_level if current_min is None else min(current_min,frame.input_level)
                if tail_started is not None and frame.kind != OUTPUT:
                    tail_status_frames += 1
                    tail_status = frame
                    tail_status_time = now
                if validator.done and tail_started is None:
                    tail_started = now
                    progress("All samples received; observing background status for at least 50 ms.")
                if frame.kind == OUTPUT:
                    start = frame.first_index
                    output[start:start+frame.count] = frame.samples
                if previous != (validator.next_output, validator.last_admitted, validator.last_consumed):
                    last_progress = now
                if now - printed >= 0.5:
                    progress(describe(frame, validator.next_output, len(data)))
                    printed = now
                if frame.max_fir > 1562:
                    raise ValidationError(f"FIR service budget exceeded: {frame.max_fir} >1562 clocks")
                if on_frame is not None:
                    callback_started = time.perf_counter_ns()
                    try:
                        on_frame(frame)
                    finally:
                        io_service["callback_calls"] += 1
                        io_service["max_callback_ns"] = max(io_service["max_callback_ns"], time.perf_counter_ns()-callback_started)
            if validator.done:
                # COMPLETE marks the last sample output, not the last FFT or
                # coefficient task. Keep checking CRC and all core/link faults.
                drained = (tail_status is not None and tail_status.consumed == len(data)
                           and tail_status.admitted == len(data) and tail_status.output_level == 0
                           and tail_status.observations == expected_observations)
                if (drained and tail_status_frames >= 2 and tail_status_time is not None
                        and tail_status_time-tail_started >= POST_COMPLETE_SECONDS):
                    break
                if now-tail_started > timeout:
                    raise ValidationError(
                        "Post-COMPLETE background observation timed out: "
                        f"status_frames={tail_status_frames}, expected_main={expected_observations}, "
                        f"last_status={tail_status}. No extra samples were sent.")
            elif not reset_ack:
                if now >= reset_deadline:
                    raise ValidationError(
                        "No valid V19 RESET acknowledgement. Check UART port/2 Mbaud and burn the V19 digital .fs. "
                        "V18 status firmware cannot accept digital samples. Do not select the FTDI download cable.")
            else:
                if not start_sent:
                    prefill = min(len(data), 256)
                    if sent < prefill:
                        count = min(64, prefill - sent)
                        write(encode_command(DATA, sent, data[sent:sent+count]))
                        sent += count
                    elif validator.last_admitted >= prefill:
                        write(encode_command(START, len(data)))
                        start_sent = True
                        last_progress = now
                else:
                    # Catch up against the latest validated consumed count. This
                    # is at most6existing64sample frames, still credit384 total.
                    # No new credit is assumed while this bounded burst writes.
                    refill_frames = 0
                    while True:
                        count = available_credit(sent, validator.last_consumed, len(data))
                        if not count:
                            break
                        write(encode_command(DATA, sent, data[sent:sent+count]))
                        sent += count
                        refill_frames += 1
                        io_service["max_refill_frames"] = max(io_service["max_refill_frames"],refill_frames)
                if now - last_progress > timeout:
                    raise ValidationError(
                        f"No transport progress for {timeout:.1f} s: sent={sent}, "
                        f"admitted={validator.last_admitted}, consumed={validator.last_consumed}, "
                        f"received={validator.next_output}. Run is invalid.")
            if not incoming:
                time.sleep(0.0001)
        # A complete marker is necessary but never replaces all N consecutive outputs.
        return output, rows, {
            **diagnostic_metadata(),
            "transport_valid": True, "board_verified": True,
            "samples_sent": sent, "samples_received": validator.next_output,
            "input_admitted": validator.last_admitted, "input_consumed": validator.last_consumed,
            "start_ack_seen": validator.start_seen, "complete_seen": validator.complete_seen,
            "host_transport_wall_s": time.perf_counter() - beginning,
            "crc_errors": parser.crc_errors - startup_crc_errors,
            "format_errors": parser.format_errors - startup_format_errors,
            "startup_crc_errors": startup_crc_errors,
            "startup_format_errors": startup_format_errors,
            "startup_stale_frames": startup_stale_frames,
            "discarded_wire_bytes": parser.discarded_bytes,
            "detector_response_ms": None, "quality_pass": None,
            "post_complete_observation_wall_s": time.perf_counter()-tail_started,
            "post_complete_status_frames": tail_status_frames,
            "post_complete_sample_output_drained": True,
            "expected_main_observations": expected_observations,
            "final_main_observations": tail_status.observations,
            "final_waiting": tail_status.waiting,
            "final_task_running": tail_status.task_running,
            "post_complete_scope": "At least 50 ms and two fresh status frames; all N outputs received, output FIFO empty and expected main observations complete. Queue/worker may wait for a future sample; this does not prove every configuration/fade completed.",
        }
    except (Exception, KeyboardInterrupt) as exc:
        try:
            write(encode_command(ABORT))
        except Exception:
            pass
        raise CaptureFailure(str(exc) or "Interrupted", output[:validator.next_output].copy(), rows, {
            **diagnostic_metadata(),
            "transport_valid": False, "board_verified": False, "failure": str(exc) or "Interrupted",
            "cancelled": isinstance(exc, _CaptureCancelled),
            "samples_sent": sent, "samples_received": validator.next_output,
            "input_admitted": validator.last_admitted, "input_consumed": validator.last_consumed,
            "host_transport_wall_s": time.perf_counter() - beginning,
            "crc_errors": parser.crc_errors - startup_crc_errors,
            "format_errors": parser.format_errors - startup_format_errors,
            "startup_crc_errors": startup_crc_errors,
            "startup_format_errors": startup_format_errors,
            "startup_stale_frames": startup_stale_frames,
            "discarded_wire_bytes": parser.discarded_bytes,
            "detector_response_ms": None, "quality_pass": None,
            "post_complete_observation_wall_s": (time.perf_counter()-tail_started) if tail_started is not None else None,
            "post_complete_status_frames": tail_status_frames,
            "post_complete_sample_output_drained": False,
            "expected_main_observations": expected_observations,
            "final_main_observations": tail_status.observations if tail_status is not None else None,
            "final_waiting": tail_status.waiting if tail_status is not None else None,
            "final_task_running": tail_status.task_running if tail_status is not None else None,
        }) from exc


class _CaptureCancelled(ValidationError):
    pass


class CaptureFailure(RuntimeError):
    def __init__(self, message, output, rows, metadata):
        super().__init__(message)
        self.output, self.rows, self.metadata = output, rows, metadata


def save_capture(directory: Path, data: np.ndarray, output: np.ndarray, rows: list[dict], metadata: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    np.save(directory / "input.npy", data)
    np.save(directory / "output.npy", output)
    wavfile.write(directory / "input.wav", FS, data)
    if len(output):
        wavfile.write(directory / "output.wav", FS, output)
    metadata["input_sha256"] = hashlib.sha256(data.astype("<i2").tobytes()).hexdigest()
    metadata["output_sha256"] = hashlib.sha256(output.astype("<i2").tobytes()).hexdigest()
    metadata["host_source_sha256"] = {
        name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("stream.py", "protocol.py", "fixtures.py", "analyze.py")
    }
    (directory / "capture.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    if rows:
        with (directory / "status.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def simulate_protocol(capacity: int, count: int = 1537) -> dict:
    """Host transport test only. Outputs are an artificial delayed ramp, not FPGA filtering."""
    samples = (np.arange(count, dtype=np.int32) * 97 % 65536 - 32768).astype(np.int16)
    wire = bytearray()
    consumed = sent = 0
    validator = RunValidator(capacity, count)
    parser = FrameParser()
    while consumed < count:
        credit = available_credit(sent, consumed, count)
        if credit:
            frame = encode_command(DATA, sent, samples[sent:sent+credit])
            assert len(frame) == 140
            sent += credit
        take = min(64, sent - consumed)
        if not take:
            continue
        snap = Snapshot(kind=OUTPUT, count=take, capacity=capacity,
                        flags=START_ACCEPTED | (COMPLETE if consumed+take == count else 1),
                        first_index=consumed, consumed=consumed+take, admitted=sent,
                        samples=tuple(map(int, samples[consumed:consumed+take])), max_fir=1296)
        wire.extend(encode_snapshot(snap))
        consumed += take
        while len(wire) > 17:
            for parsed in parser.feed(bytes(wire[:17])):
                validator.accept(parsed, sent)
            del wire[:17]
    for parsed in parser.feed(bytes(wire)):
        validator.accept(parsed, sent)
    if not validator.done:
        raise AssertionError("Protocol simulation did not complete")
    return {"protocol_simulation_pass": True, "capacity": capacity, "samples": count,
            "board_verified": False, "quality_pass": None,
            "note": "No hardware was contacted. This is not an FPGA filter/performance result."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacity", type=int, choices=(3, 4))
    parser.add_argument("--port")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", "--outputdir", dest="output", type=Path)
    parser.add_argument("--scenario", choices=SCENARIOS, default="dynamic")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--reference-output", type=Path)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--simulate-protocol", action="store_true")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        from serial.tools import list_ports
        for port in list_ports.comports():
            print(f"{port.device}: {port.description} [{port.hwid}]")
        return 0
    if args.capacity is None:
        parser.error("--capacity 3 or 4 is required (must match the burned firmware)")
    if args.simulate_protocol:
        print(json.dumps(simulate_protocol(args.capacity), indent=2))
        return 0
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out = args.output or Path(__file__).resolve().parent.parent / "captures" / f"c{args.capacity}_{timestamp}"
    if out.exists() and any(out.iterdir()):
        parser.error(f"Output directory is not empty: {out}. Use a new directory to preserve earlier evidence.")
    out.mkdir(parents=True, exist_ok=True)
    input_path = args.input or save_fixture(out / "source", args.capacity, args.scenario, args.seed, args.seconds)
    data = load_input(input_path)
    expected = load_input(args.reference_output) if args.reference_output else None
    if expected is not None and len(expected) != len(data):
        parser.error("Reference output length must equal input length before starting a board run")
    source_json = input_path.with_suffix(".json")
    source_metadata = json.loads(source_json.read_text(encoding="utf-8")) if source_json.exists() else None
    if source_metadata and source_metadata.get("capacity", args.capacity) != args.capacity:
        parser.error("Fixture capacity and requested FPGA capacity disagree")
    port_name = args.port
    if not port_name:
        from serial.tools import list_ports
        for port in list_ports.comports():
            print(f"{port.device}: {port.description} [{port.hwid}]")
        port_name = input("Board CH9102 UART port (for example COM9): ").strip()
    import serial
    base = {"schema": "v19-capture-1", "created_utc": datetime.now(timezone.utc).isoformat(),
            "features":None,"diagnostic_counter_encoding":"unknown_no_valid_status",
            "diagnostic_255_means_at_least":False,
            "max_capture_samples":None,
            "capacity": args.capacity, "port": port_name, "baud": BAUD,
            "sample_rate_hz": FS, "input_path": str(input_path.resolve()),
            "source_metadata": source_metadata,
            "notes": ["PC generates/replays tests, transports samples and evaluates offline only.",
                      "USB wall time and prefill latency are not FPGA detector response time.",
                      "A transport pass does not prove attenuation, midpoint protection or 30 ms response."]}
    try:
        with serial.Serial(port_name, BAUD, bytesize=8, parity="N", stopbits=1,
                           timeout=0, write_timeout=0.05, rtscts=False, dsrdtr=False) as port:
            output, rows, meta = run_serial(port, data, args.capacity, timeout=args.timeout)
    except CaptureFailure as exc:
        save_capture(out, data, exc.output, exc.rows, {**base, **exc.metadata})
        print(f"INVALID RUN: {exc}\nPartial evidence saved: {out}", file=sys.stderr)
        return 2
    except (serial.SerialException, OSError) as exc:
        save_capture(out, data, np.empty(0, dtype=np.int16), [],
                     {**base, "transport_valid": False, "board_verified": False, "failure": str(exc)})
        print(f"Cannot open/use board UART: {exc}\nCheck CH9102 UART, not FTDI downloader. Evidence: {out}", file=sys.stderr)
        return 2
    result = {**base, **meta}
    reference_ok = True
    if args.reference_output:
        reference_ok = len(expected) == len(output) and np.array_equal(expected, output)
        result["reference_output"] = str(args.reference_output.resolve())
        result["reference_sha256"] = hashlib.sha256(expected.astype("<i2").tobytes()).hexdigest()
        result["bit_exact_reference_match"] = reference_ok
        if len(expected) == len(output):
            errors = expected.astype(np.int32) - output.astype(np.int32)
            result["reference_mismatched_samples"] = int(np.count_nonzero(errors))
            result["reference_max_abs_error_lsb"] = int(np.max(np.abs(errors)))
    save_capture(out, data, output, rows, result)
    if not args.no_plot:
        from .analyze import evaluate
        evaluate(out, source_metadata)
    print(f"TRANSPORT PASS: {len(output)} consecutive actual FPGA output samples saved to {out}")
    if not reference_ok:
        print("REFERENCE FAIL: actual FPGA output differs from supplied reference; see capture.json", file=sys.stderr)
        return 3
    if expected is not None:
        print(f"REFERENCE PASS: {len(output)} samples match the frozen RTL reference bit for bit.")
    print("Filter quality/30 ms response are NOT automatically passed; inspect offline evidence.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, EOFError) as exc:
        print(f"Input/configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)

"""Re-score frozen real UART AM outputs; no serial, GUI, RTL or detector run."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import re
import wave

import numpy as np

HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parent.parent


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(values):
    return hashlib.sha256(values.astype('<i2', copy=False).tobytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected, label):
    require(math.isfinite(float(actual)) and math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-10),
            f'{label}: {actual} != {expected}')


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def audio(path):
    with wave.open(str(path), 'rb') as stream:
        require((stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (1, 2, 32000), 'WAV format')
        return np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2')


def verify():
    manifest = load(HERE / 'manifest.json')
    for name, record in manifest['files'].items():
        path = (HERE / name).resolve()
        require(path.is_relative_to(HERE), 'Invalid AM evidence path: ' + name)
        require(path.is_file() and path.stat().st_size == record['bytes'] and sha(path) == record['sha256'], 'Evidence changed: ' + name)
    for name, record in manifest['bundle_bindings'].items():
        path = (BUNDLE / name).resolve()
        require(path.is_relative_to(BUNDLE) and path.is_file() and sha(path) == record['sha256'], 'Numerical/source binding changed: ' + name)

    expected = load(HERE / 'expected_results.json')
    original = load(HERE / 'provenance/offline_result_original.json')
    experiment = load(HERE / 'provenance/experiment_at_preparation.json')
    capture = load(HERE / 'board_capture/capture.json')
    n, fs, delay = expected['samples'], expected['fs_hz'], expected['alignment_samples']
    values = {}
    for key, name in (('input', 'prepared/input.npy'), ('board_input', 'board_capture/input.npy'),
                      ('board', 'board_capture/output.npy'), ('rtl', 'rtl_evidence/output_our_rtl.npy'),
                      ('reference', 'reference/output_1024.npy')):
        values[key] = np.load(HERE / name, allow_pickle=False)
        require(values[key].shape == (n,) and values[key].dtype.kind == 'i' and values[key].dtype.itemsize == 2, 'Invalid sample array: ' + key)
    require(np.array_equal(values['input'], values['board_input']), 'Prepared/board input mismatch')
    require(np.array_equal(values['board'], values['rtl']), 'Board/archived complete RTL output mismatch')
    require(payload(values['input']) == expected['input_payload_sha256'] == capture['input_sha256'] == original['input_payload_sha256'], 'Input payload identity')
    require(payload(values['board']) == expected['board_output_payload_sha256'] == capture['output_sha256'] == original['output_our_payload_sha256'], 'Board output payload identity')
    require(payload(values['reference']) == expected['software_output_payload_sha256'] == original['output_reference_payload_sha256'], 'Reference payload identity')
    for name, key in (('prepared/input.wav', 'input'), ('board_capture/input.wav', 'input'), ('board_capture/output.wav', 'board')):
        require(np.array_equal(audio(HERE / name), values[key]), 'WAV/NPY mismatch: ' + name)
    require(int(np.max(np.abs(values['input'].astype(np.int32)))) == 18389, 'Input amplitude changed')

    with np.load(HERE / 'prepared/components.npz', allow_pickle=False) as arrays:
        components = {name: arrays[name].copy() for name in arrays.files}
    with np.load(HERE / 'board_capture/source_components.npz', allow_pickle=False) as arrays:
        require(set(arrays.files) == set(components) and all(np.array_equal(arrays[name], components[name]) for name in components), 'Prepared/capture components mismatch')
    for name, data in components.items():
        require(data.shape == (n,) and np.all(np.isfinite(data)), 'Invalid component: ' + name)
    for name in ('target', 'noise'):
        require(hashlib.sha256(components[name].astype('<f8').tobytes()).hexdigest() == experiment[name + '_array_sha256'], name + ' changed')
    mixed = sum((components[name] for name in ('target', 'noise', 'jam_1', 'jam_2', 'jam_3', 'jam_4')), np.zeros(n))
    require(np.array_equal(np.rint(mixed * 32768).astype('<i2'), values['input']), 'Input is not the documented mixed components')
    require(np.max(np.abs(values['input'].astype(float) / 32768 - mixed - components['quantization_error'])) < 1e-15, 'Quantization residual changed')
    plan = load(BUNDLE / 'validation/timing_sweep/experiment_plan.json')
    sample = np.arange(n)
    time = sample / fs
    for index, (frequency, start, stop, phase) in enumerate(zip(experiment['frequencies_hz'], experiment['start_samples'], experiment['stop_samples'], experiment['onset_phases_rad'])):
        relative_time = time - start / fs
        tone = plan['fixed_jammer_peak_before_gain'] * np.sin(2 * np.pi * frequency * relative_time + phase) * ((sample >= start) & (sample < stop))
        envelope = 1 + experiment['am']['depth'] * np.sin(2 * np.pi * experiment['am']['frequency_hz'] * relative_time + experiment['am']['phases_rad'][index])
        regenerated = tone * envelope * experiment['am']['per_source_power_match_factors'][index]
        require(np.array_equal(regenerated, components[f'jam_{index + 1}']), 'AM formula/component mismatch')

    samples = csv_rows(HERE / 'board_capture/samples.csv')
    require(len(samples) == n and [int(row['sample_index']) for row in samples] == list(range(n)), 'UART sample indices/count')
    for column, key in (('input_int16', 'input'), ('fpga_output_int16', 'board')):
        require(np.array_equal(np.asarray([int(row[column]) for row in samples], dtype=np.int16), values[key]), 'UART CSV/NPY mismatch: ' + column)
    status = csv_rows(HERE / 'board_capture/status.csv')
    frames = [row for row in status if int(row['kind']) == 129]
    require(len(status) == expected['status_rows'] and len(frames) == expected['sample_frames'], 'Status/sample frame counts')
    require(all(int(row['count']) == 64 for row in frames) and [int(row['first_index']) for row in frames] == list(range(0, n, 64)), 'UART frame continuity')
    require(sum(int(row['count']) for row in frames) == n, 'Frame payload sample total')
    for key in ('transport_valid', 'board_verified', 'start_ack_seen', 'complete_seen', 'post_complete_sample_output_drained'):
        require(capture[key] is True, 'Capture transport evidence: ' + key)
    for key in ('samples_sent', 'samples_received', 'input_admitted', 'input_consumed'):
        require(capture[key] == n, 'Capture sample count: ' + key)
    require(capture['sample_rate_hz'] == fs and capture['baud'] == 2000000 and capture['port'] == 'COM9', 'Capture interface changed')
    for key in ('crc_errors', 'format_errors', 'startup_crc_errors', 'startup_format_errors', 'discarded_wire_bytes'):
        require(capture[key] == 0, 'Capture format/CRC fault: ' + key)
    for key in ('dropped', 'numeric', 'deadline', 'sequence_faults', 'saturated', 'link_faults', 'error_code'):
        require(all(int(row[key]) == 0 for row in status), 'Decoded UART fault counter: ' + key)
    require(int(status[-1]['observations']) == capture['final_main_observations'] == expected['main_observations'], 'Main observation count')
    require(int(status[-1]['commits']) == expected['commits'], 'Commit count')

    log = (HERE / 'rtl_evidence/run.log').read_text(encoding='utf-8')
    require('PASS FULL_AUTO_QUALITY_TRACE' in log, 'Archived RTL run did not pass execution checks')
    final, = [line for line in log.splitlines() if line.startswith('FINAL case=')]
    recorded = {key: int(value, 16 if key in ('active', 'error_code') else 10) for key, value in re.findall(r'(\w+)=([0-9a-fA-F]+)', final)}
    require(recorded == original['rtl_status'] and recorded['outputs'] == n, 'Archived RTL status identity')

    gui = BUNDLE / 'comparison_results/20261028_230454_196243_701f2bec'
    for filename, key in (('input.npy', 'input'), ('output_512_actual.npy', 'board'), ('output_1024.npy', 'reference')):
        require(np.array_equal(np.load(gui / filename, allow_pickle=False), values[key]), 'Original GUI output mismatch: ' + filename)
    require(np.array_equal(np.load(gui / 'target_reference.npy', allow_pickle=False), components['target']), 'Original GUI target mismatch')

    metrics = {}
    for name, window in expected['windows'].items():
        start, stop = window['source_sample_range']
        require(0 <= start < stop <= n - delay, 'Invalid scoring interval')
        target = components['target'][start:stop]
        denominator = np.linalg.norm(target)
        require(denominator > 0, 'Zero target norm')
        error = lambda key, shift: float(100 * np.linalg.norm(values[key][start + shift:stop + shift].astype(float) / 32768 - target) / denominator)
        board, reference, input_error = error('board', delay), error('reference', delay), error('input', 0)
        reduction = 100 * (reference - board) / reference
        for actual, field in ((board, 'nrmse_512_pct'), (reference, 'nrmse_1024_pct'), (input_error, 'input_nrmse_pct'), (reduction, 'relative_reduction_pct')):
            close(actual, window[field], name + '/' + field)
        metrics[name] = dict(source_sample_range=[start, stop], board_nrmse_pct=board, software_1024_nrmse_pct=reference, relative_reduction_pct=reduction)
    return dict(status='AM_FROZEN_UART_AND_METRICS_RECHECK_PASS', input_id=expected['input_payload_sha256'][:12],
                samples=n, status_rows=len(status), sample_frames=len(frames), non_sample_status_frames=len(status)-len(frames),
                observations=expected['main_observations'], commits=expected['commits'], board_vs_archived_rtl_mismatches=0,
                metrics=metrics, no_independent_midpoint_probe=True,
                new_serial_run=False, new_detector_or_reference_run=False, new_rtl_run=False, new_pnr_run=False,
                scope='Recomputed existing actual UART capture and frozen software arrays; no new pipeline execution or universal quality acceptance.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    result = verify()
    (HERE / 'verification_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))

"""Frozen 1024 finite-arithmetic software reference on recorded mixed input.

The detector and coefficient/FIR execution receive mixed samples only. Offline
reference and actual board output are loaded only after software output exists.
No UART, synthesis, or new hardware result is produced here.
"""
from pathlib import Path
import ast
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time
import types

for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'
import numpy as np

HERE = Path(__file__).resolve().parent
RELEASE = HERE.parents[1]
SOURCE = HERE/'frozen_source'
INPUT = RELEASE/'real_recorded_inputs/prepared/drm_recorded_two_pairs/input.npy'
BOARD = RELEASE/'host_ui/captures/c4_20261027_223556_432532'
EXPECTED_INPUT = 'f7273ee103dedc1a4a49282317bf0fd88425d1408b2abdc316b29c7daee03358'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def payload(values):
    return hashlib.sha256(np.asarray(values).astype('<i2').tobytes()).hexdigest()

def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def functions(path, names, namespace):
    """Reuse unchanged function ASTs, without unrelated historical scoring imports."""
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in selected} == set(names)
    code = ast.Module(body=selected, type_ignores=[])
    exec(compile(code, str(path), 'exec'), namespace)
    return types.SimpleNamespace(**{name: namespace[name] for name in names})

def main():
    HERE.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((HERE/'source_manifest.json').read_text(encoding='utf-8'))
    sources = {}
    for name, record in manifest['files'].items():
        copy = SOURCE/name
        assert sha(copy) == record['sha256'], name
        sources[name] = {'package_source': str(copy), 'sha256': sha(copy)}
    for name, expected in manifest['scoring_files'].items():
        assert sha(RELEASE/name) == expected, name
    for name, expected in manifest['backend_reuse_files'].items():
        assert sha(HERE/name) == expected, name
    x = np.load(INPUT, allow_pickle=False)
    assert x.dtype == np.dtype('int16') and x.shape == (64000,) and payload(x) == EXPECTED_INPUT
    x.setflags(write=False)
    np.save(HERE/'input.npy', x)
    fft = load('fft_reference', SOURCE/'fft_reference.py')
    load('gate_reference', SOURCE/'gate_reference.py')
    load('aux_reference', SOURCE/'aux_reference.py')
    detector = load('fixed_baseline', SOURCE/'fixed_baseline.py')
    fir = load('fir_replay', SOURCE/'fir_replay.py')
    builder = load('fixed_builder', SOURCE/'fixed_builder.py')
    basis = load('basis_probe', SOURCE/'basis_probe.py')
    from functools import lru_cache
    q15 = functions(SOURCE/'evaluate_q15.py', ['response_q15', 'build_q15'],
                    dict(np=np, lru_cache=lru_cache, basis_probe=basis, cosq=builder.cosq,
                         ONE=32768, quantized_frequencies=fir.quantized_frequencies, round_div_even=fir.round_div_even))
    replay = functions(SOURCE/'run_comparison.py', ['replay'],
                       dict(np=np, actual=q15, bench=types.SimpleNamespace(fir_replay=fir))).replay

    # Check the unchanged software backend against its existing frozen output.
    with np.load(HERE/'backend_reuse_fixture.npz', allow_pickle=False) as z:
        old_x, old_y, old_banks, old_trajectory = (z[k].copy() for k in ('input', 'output', 'banks_q16', 'trajectory'))
    old_decisions = json.loads((HERE/'backend_reuse_updates.json').read_text(encoding='utf-8'))
    check, _ = replay(old_x.astype(float)[None, :], old_x, old_decisions)
    assert np.array_equal(check['output'], old_y), 'Frozen software output mismatch'
    assert np.array_equal(check['banks'], old_banks), 'Frozen software banks mismatch'
    assert np.array_equal(check['trajectory'], old_trajectory), 'Frozen software trajectory mismatch'
    check_report = {'status': 'PASS', 'samples': len(old_x), 'output_mismatches_lsb': 0,
                    'banks_equal': True, 'trajectory_equal': True,
                    'source_archive': 'backend_reuse_fixture.npz',
                    'source_archive_sha256': sha(HERE/'backend_reuse_fixture.npz'),
                    'original_provenance': manifest['backend_reuse_original_provenance']}
    write(HERE/'backend_reuse_check.json', check_report)
    print('Frozen Q15 software backend output/banks/trajectory identical.', flush=True)

    cfg = detector.Config(max_centers=4, threshold_db=21)
    begin = time.perf_counter()
    extracted = detector.extract_observations(x, cfg.estimator)
    write(HERE/'raw_observations.json', {'observations': extracted, 'input_payload_sha256': payload(x)})
    decisions = detector.detect_updates(x, cfg, extracted)
    write(HERE/'decisions_1024_21db.json', decisions)
    print('1024 observations/updates:', len(extracted), len(decisions['updates']), flush=True)
    result, details = replay(x.astype(float)[None, :], x, decisions)
    y = result['output']
    np.save(HERE/'output_1024_software.npy', y)
    np.savez_compressed(HERE/'backend_state.npz', banks_q16=result['banks'], trajectory=result['trajectory'])
    write(HERE/'backend_diagnostics.json', details)
    offline_wall = time.perf_counter()-begin

    # Reference, source labels, and actual board output are used only below for scoring.
    meta = json.loads((BOARD/'capture.json').read_text(encoding='utf-8'))
    assert meta['transport_valid'] is True and meta['board_verified'] is True
    actual_x = np.load(BOARD/'input.npy', allow_pickle=False)
    actual_y = np.load(BOARD/'output.npy', allow_pickle=False)
    assert np.array_equal(actual_x, x) and len(actual_y) == 64000
    assert payload(actual_y) == meta['output_sha256']
    with np.load(BOARD/'source_components.npz', allow_pickle=False) as parts:
        reference = parts['target'].copy()
    sys.path.insert(0, str(RELEASE/'host_ui'))
    metrics = load('recorded_comparison_metrics', RELEASE/'host_ui/metrics.py')
    windows = {'full_common': (0.0, 1.99), 'stable': (0.4, 0.8)}
    measured, rows = {}, []
    for name, (start, width) in windows.items():
        measured[name] = {}
        for arm, output in [('actual_512_board', actual_y), ('software_1024_21db', y)]:
            q = metrics.compute_metrics(x, output, reference, meta['source_metadata'], start, width, align=True)
            measured[name][arm] = q
            rows.append({'window': name, 'start_s': start, 'stop_s': start+width,
                         'method': arm, 'common_samples': q['common_samples'],
                         'input_nrmse_pct': q['input_nrmse_pct'], 'output_nrmse_pct': q['output_nrmse_pct'],
                         'error_reduction_db': q['error_reduction_db'],
                         'layer': 'Actual FPGA UART output' if arm == 'actual_512_board' else 'Finite-arithmetic software, zero compute/preparation waiting'})
        a = measured[name]['actual_512_board']['output_nrmse_pct']
        b = measured[name]['software_1024_21db']['output_nrmse_pct']
        measured[name]['actual_512_relative_error_reduction_vs_software_1024_pct'] = 100*(1-a/b)
    with (HERE/'paired_metrics.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        w = csv.DictWriter(handle, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    report = {
        'status': 'OFFLINE_RECORDED_1024_REFERENCE_COMPLETE',
        'input': {'file': str(INPUT), 'payload_sha256': payload(x), 'samples': len(x), 'sample_rate_hz': 32000},
        'actual_512_board_capture': str(BOARD), 'actual_output_payload_sha256': payload(actual_y),
        'software_output_payload_sha256': payload(y), 'config': decisions['config'],
        'sources': sources, 'driver_sha256': sha(__file__), 'backend_reuse_check': check_report,
        'function_reuse': {'evaluate_q15.py': ['response_q15', 'build_q15'], 'run_comparison.py': ['replay'],
                           'mechanism': 'Original unchanged function ASTs executed with explicit frozen numeric dependencies; unrelated scoring imports omitted.'},
        'detector_statistics': decisions['stats'], 'coefficient_guard_failures': len(details['guard_failures']),
        'output_saturations': result['saturations'], 'windows': measured,
        'offline_wall_seconds_not_fpga_latency': offline_wall,
        'limitations': [
            '512 output is actual FPGA board capture; 1024 output is software finite arithmetic. This is not two-board or complete online RTL comparison.',
            '1024 software observation is available at completed window sample; FFT/DTFT computation and coefficient preparation waiting are zero. Not an FPGA latency measurement or quality bound.',
            'Software latest-deferred-bank scheduling and existing actual FPGA authorization/queue/commit scheduling may differ. 32-sample fades and continuous input history are retained.',
            'Same numeric 21 dB gate and physical 187.5..750 Hz reference interval do not prove equal false-alarm probability; 1024 uses 19 samples per side.',
            'Received recording retains noise/channel/pilots; no clean transmitter truth or BER measurement. Full interval includes transitions; stable interval is separately reported.',
            'No new UART/RTL/synthesis/P&R or threshold tuning performed.'
        ],
    }
    write(HERE/'results.json', report)
    print(json.dumps({'windows': {k: {'actual512': v['actual_512_board']['output_nrmse_pct'],
                                    'software1024': v['software_1024_21db']['output_nrmse_pct'],
                                    'relative_reduction_pct': v['actual_512_relative_error_reduction_vs_software_1024_pct']}
                                        for k,v in measured.items()}, 'guards': len(details['guard_failures']),
                      'saturations': result['saturations'], 'offline_wall_seconds': offline_wall}, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()

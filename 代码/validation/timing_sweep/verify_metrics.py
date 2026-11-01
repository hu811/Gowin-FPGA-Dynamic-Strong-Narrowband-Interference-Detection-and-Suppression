"""Independently re-score 48 frozen sweep rows; no UART, RTL run, or plotting."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import re

import numpy as np

HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parent.parent


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def payload(values):
    return hashlib.sha256(values.astype('<i2', copy=False).tobytes()).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected, label):
    check(math.isfinite(float(actual)) and math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-10),
          f'{label}: {actual} != {expected}')


def verify():
    frozen = load(HERE/'frozen_files.json')['files']
    for relative, record in frozen.items():
        path = (HERE/relative).resolve()
        check(path.is_relative_to(HERE), 'Invalid frozen relative path')
        check(path.is_file() and path.stat().st_size == record['bytes'] and sha(path) == record['sha256'],
              'Frozen file changed: '+relative)
    plan, result = load(HERE/'experiment_plan.json'), load(HERE/'results.json')
    check(result['status'] == 'COMPLETE' and result['passed'] == 15 and result['failures'] == 0, 'Incomplete sweep')
    plan_sha = sha(HERE/'experiment_plan.json')
    check(result['plan_sha256'] == plan_sha, 'Plan/result identity mismatch')
    check(sha(HERE/'runtime_identity.json') == plan['runtime_identity_sha256'], 'Runtime identity changed')
    check(sha(BUNDLE/'host_ui/scene.py') == plan['generator_source_sha256'], 'Target generator source changed')
    check(sha(BUNDLE/'comparison_ui/reference_1024.py') == plan['engine_source_sha256'], '1024 engine source changed')
    identity = load(HERE/'run_source_identity.json')['source_hashes']
    for name, checksum in identity.items():
        source = HERE/name if (HERE/name).exists() else HERE/'provenance'/name
        check(sha(source) == checksum, 'Run source changed: '+name)
    runtime = load(HERE/'runtime_identity.json')
    for binding in runtime['source_binding']:
        check(sha(BUNDLE/'c4/rtl'/binding['name']) == binding['production_sha256'],
              'Current RTL source identity changed: '+binding['name'])
    for relative, checksum in runtime['copied_files'].items():
        relative = relative.replace('\\', '/')
        if relative.startswith('rom/'):
            check(sha(BUNDLE/'c4'/relative) == checksum, 'Current ROM changed: '+relative)
        elif '/' not in relative:
            check(sha(HERE/'rtl_rebuild'/relative) == checksum, 'Testbench/primitive changed: '+relative)

    fs, count = plan['fs_hz'], plan['sample_count']
    with np.load(HERE/'base_reference.npz', allow_pickle=False) as base:
        target, noise = base['target'].copy(), base['noise'].copy()
    for name, values in (('target', target), ('noise', noise)):
        check(values.shape == (count,) and np.all(np.isfinite(values)), 'Invalid '+name)
        check(hashlib.sha256(values.astype('<f8').tobytes()).hexdigest() == plan[name+'_array_sha256'], name+' array changed')
    cache = {}
    metric_cache = {}
    for item in plan['unique_cases']:
        case_id = item['case_id']
        check(re.fullmatch(r'case_\d{2}', case_id) is not None, 'Invalid case ID')
        folder = HERE/'cases'/case_id
        case = load(folder/'case_result.json')
        check(case == result['unique_results'][case_id] and case['status'] == 'PASS', 'Case/summary mismatch: '+case_id)
        check(case['plan_sha256'] == plan_sha, 'Case uses another plan')
        values = {}
        for key, filename in (('input', 'input.npy'), ('output_512', 'output_512_rtl.npy'),
                              ('output_1024', 'output_1024_software.npy')):
            array = np.load(folder/filename, allow_pickle=False)
            check(array.shape == (count,) and array.dtype.kind == 'i' and array.dtype.itemsize == 2, 'Invalid samples: '+case_id+'/'+filename)
            check(payload(array) == case[key+'_payload_sha256'], 'Payload hash mismatch: '+case_id+'/'+filename)
            values[key] = array
        check(payload(values['input']) == item['input_payload_sha256'], 'Input/plan mismatch')
        check(case['native_exe_sha256'] == runtime['exe_sha256'] == plan['native_executable_sha256'], 'Native identity mismatch')
        log = (folder/'rtl_trace/run.log').read_text(encoding='utf-8')
        check('PASS FULL_AUTO_QUALITY_TRACE' in log, 'RTL trace did not pass')
        final_lines = [line for line in log.splitlines() if line.startswith('FINAL case=')]
        check(len(final_lines) == 1, 'Missing or repeated final RTL status')
        final = {key: int(value, 16 if key in ('active', 'error_code') else 10)
                 for key, value in re.findall(r'(\w+)=([0-9a-fA-F]+)', final_lines[0])}
        check(final == case['rtl_status'], 'RTL log/result counters mismatch')
        check(final['sent'] == final['outputs'] == count and final['max_fir'] <= 1562, 'RTL sample/service budget failure')
        check(all(final[key] == 0 for key in ('dropped', 'sat', 'numeric', 'deadline', 'seqfault', 'errors', 'error_code')), 'RTL fault counter')
        check(load(folder/'software_stats.json') == case['software_stats'], 'Software stats mismatch')
        for window, interval in plan['score_windows'].items():
            first, stop = interval['first_sample'], interval['stop_sample_exclusive']
            ref = target[first:stop]
            check(0 <= first < stop <= count-320 and np.linalg.norm(ref) > 0, 'Invalid scoring interval')
            errors = [float(100*np.linalg.norm(values[name][first+320:stop+320].astype(float)/32768-ref)/np.linalg.norm(ref))
                      for name in ('output_512', 'output_1024')]
            metric = dict(nrmse_512_pct=errors[0], nrmse_1024_pct=errors[1],
                          relative_reduction_pct=100*(errors[1]-errors[0])/errors[1])
            for name, value in metric.items():
                close(value, case['metrics'][window][name], case_id+'/'+window+'/'+name)
            metric_cache[case_id, window] = metric
        cache[case_id] = values['input']

    with (HERE/'sweep_metrics.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    check(len(rows) == 48 and len(cache) == 15 and len(plan['plan_cases']) == 16, 'Wrong sweep size')
    by_key = {(row['axis'], int(row['delay_ms']), row['window']): row for row in rows}
    check(len(by_key) == len(rows), 'Repeated CSV key')
    for item in plan['plan_cases']:
        for window, interval in plan['score_windows'].items():
            row = by_key[item['axis'], item['delay_ms'], window]
            check(row['case_id'] == item['case_id'] and row['input_payload_sha256'] == item['input_payload_sha256'], 'CSV case/input mismatch')
            check(row['rtl_status'] == 'PASS', 'CSV failed case')
            for name, value in metric_cache[item['case_id'], window].items():
                close(float(row[name]), value, 'CSV '+str((item['axis'], item['delay_ms'], window, name)))
            check(int(row['common_samples']) == interval['stop_sample_exclusive']-interval['first_sample'], 'CSV count mismatch')
            close(float(row['score_start_s']), interval['first_sample']/fs, 'CSV start')
            close(float(row['score_stop_s']), interval['stop_sample_exclusive']/fs, 'CSV stop')
    return dict(status='PASS', scope='Archived-array SHA and independent48-row scoring; no new RTL or board execution',
                frozen_files_checked=len(frozen), unique_inputs_checked=len(cache), plan_conditions=16,
                rows_recomputed=48, input_generation_audit='Original independent_plan_audit.json retained; generation not rerun here',
                alignment_samples=320, gain_fitting=False, missing_output_padding=False,
                retained_negative_rows=sum(float(row['relative_reduction_pct']) < 0 for row in rows),
                new_uart_executed=False, new_rtl_executed=False, new_pnr_executed=False,
                rtl_rebuild_status='Source/command supplied; not compiled or rerun during packaging')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=HERE/'verification_result.json')
    args = parser.parse_args()
    verified = verify()
    args.report.write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(verified, ensure_ascii=False, indent=2))

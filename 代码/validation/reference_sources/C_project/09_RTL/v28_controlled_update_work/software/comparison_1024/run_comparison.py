"""Frozen current actual RTL vs finite-arithmetic conventional1024 references."""
from pathlib import Path
import argparse
import json
import sys
import time
import numpy as np
import fixed_baseline

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import score_actual_control as scoring
bench, actual = scoring.bench, scoring.old


def replay(rows, x, decisions):
    """Same final Q15_MAC bank golden and641tap/Q16/fade arithmetic."""
    n = len(x)
    bypass = actual.build_q15([])
    banks, info = [bypass], [dict(centers_hz=[])]
    ids, config_cache = {bypass.tobytes(): 0}, {}
    trajectory = np.zeros((n, 3), np.int32)
    current, target, pending, alpha, pointer = 0, None, None, 0, 0
    updates = decisions['updates']
    failures, requests = [], []
    for i in range(n):
        while pointer < len(updates) and updates[pointer]['sample'] <= i:
            update = updates[pointer]
            centers = tuple(update['frequencies_hz'])
            if centers not in config_cache:
                try:
                    h, diagnostic = actual.build_q15(centers, True)
                    key = h.tobytes()
                    if key not in ids:
                        ids[key] = len(banks); banks.append(h); info.append(diagnostic)
                    config_cache[centers] = (ids[key], None)
                except (ArithmeticError, ValueError, OverflowError) as error:
                    config_cache[centers] = (None, str(error))
            bank, failure = config_cache[centers]
            requests.append(dict(sample=i, observation=update['observation'], centers_hz=list(centers),
                                 fcw=[int(round(f / 3.90625)) << 19 for f in centers],
                                 bank=bank, guard_error=failure))
            if failure:
                failures.append(requests[-1])
            else:
                pending = bank
            pointer += 1
        if target is None and pending is not None:
            if pending != current:
                target, alpha = pending, 1
            pending = None
        if target is None:
            trajectory[i] = current, current, 0
        else:
            trajectory[i] = current, target, alpha
            if alpha == 32:
                current, target, alpha = target, None, 0
            else:
                alpha += 1
    padded = np.pad(rows, ((0, 0), (640, 0)))
    mixed = np.pad(x.astype(np.int64), (640, 0))
    numerator = np.zeros(n, np.int64)
    components = np.zeros_like(rows)
    split = np.r_[0, np.flatnonzero(np.any(trajectory[1:, :2] != trajectory[:-1, :2], axis=1)) + 1, n]
    for a, b in zip(split[:-1], split[1:]):
        old, new = trajectory[a, :2]
        weights = {int(old): 32 - trajectory[a:b, 2]}
        weights[int(new)] = weights.get(int(new), 0) + trajectory[a:b, 2]
        for bank, weight in weights.items():
            if not np.any(weight):
                continue
            h = banks[bank]
            numerator[a:b] += np.convolve(mixed[a:b + 640], h, 'valid') * weight
            for row in range(len(rows)):
                components[row, a:b] += np.convolve(padded[row, a:b + 640], h / 65536, 'valid') * weight / 32
    output, saturations = bench.fir_replay._rne_clip_array(numerator)
    assert np.max(abs(components.sum(axis=0) - numerator / (1 << 21)), initial=0) < 1e-7
    return dict(output=output, components=components, banks=np.asarray(banks), trajectory=trajectory,
                saturations=saturations), dict(guard_failures=failures, requests=requests, banks=info)


def pressure(updates):
    samples = np.asarray([u['sample'] for u in updates], np.int64)
    return dict(configuration_updates=len(samples),
        minimum_update_spacing_samples=int(np.diff(samples).min()) if len(samples) > 1 else None,
        minimum_update_spacing_ms=float(np.diff(samples).min() / 32) if len(samples) > 1 else None,
        max_updates_in8ms=max((int(np.sum((samples >= p) & (samples < p + 256))) for p in samples), default=0))


def dynamic_energy(meta, names, rows, result, method):
    """Every start/end change; fixed delay alignment, no best-window selection."""
    clean = [i for i, name in enumerate(names) if name == 'target' or name.startswith('probe_')]
    reference = bench.align(rows)[clean].sum(axis=0)
    y = result['output'].astype(float)
    changes = sorted(set(v for event in meta['events'] for v in (event['start_sample'], event['end_sample'])))
    entries = []
    for event, onset in enumerate(changes):
        following = changes[event + 1] if event + 1 < len(changes) else len(y)
        for start_ms, end_ms in [(0, 50), (0, 10), (10, 20), (20, 30), (30, 50)]:
            a = onset + 320 + start_ms * 32
            requested_end = onset + 320 + end_ms * 32
            b = min(requested_end, following + 320, len(y))
            common = dict(case=meta['name'], method=method, change_sample=onset,
                next_change_sample=following, interval_start_ms=start_ms, interval_end_ms=end_ms,
                requested_output_start_sample=a, requested_output_end_sample=requested_end,
                actual_output_end_sample=b, censored=b < requested_end)
            if a >= b:
                entries.append(dict(**common, status='NO_COVERAGE_AFTER_NEXT_CHANGE', samples=0,
                                    error_energy_counts2=None, reference_energy_counts2=None, nrmse_percent=None))
                continue
            error = y[a:b] - reference[a:b]
            ee, rr = float(error @ error), float(reference[a:b] @ reference[a:b])
            entries.append(dict(**common, status='SCORED', samples=b - a, error_energy_counts2=ee,
                                reference_energy_counts2=rr, nrmse_percent=100 * np.sqrt(ee / rr) if rr else None))
    return entries


def score(meta, x, names, rows, value, decisions, method, clocks_in, clocks_out):
    stat, windows = bench.score(meta, names, rows, value, decisions, method, 'same_q15_fir')
    stat.update(actual.settled_diagnostic(meta, rows, names, value))
    stat.update(scoring.false_active_diagnostic(meta, value))
    stat['response_events'] = actual.event_responses(meta, windows, clocks_in, clocks_out)
    for row in windows:
        row['evidence_available_clock'] = int(clocks_out[row['end_sample'] - 1])
    return stat, windows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', nargs='*')
    args = parser.parse_args()
    plan = bench.read(HERE / 'experiment_plan.json')
    assert bench.read(HERE / 'arithmetic_check.json')['status'] == 'PASS'
    assert bench.read(HERE / 'arithmetic_check.json')['fixed_baseline_sha256'] == bench.sha(HERE / 'fixed_baseline.py')
    out = HERE / 'results'; out.mkdir(exist_ok=True)
    summaries, windows, pairs, dynamic, audit = [], [], [], [], []
    for case_plan in plan['cases']:
        case = case_plan['case']
        if args.cases and case not in args.cases:
            continue
        folder = Path(case_plan['current_actual_export']); fixture = Path(case_plan['fixture'])
        assert bench.sha(fixture / 'input.npy') == case_plan['input_sha256']
        assert bench.sha(folder / 'frozen_operator_and_components.npz') == case_plan['current_archive_sha256']
        meta, x, names, rows, current, data, check = actual.load_actual(folder, fixture)
        meta = dict(meta, name=case)
        cap = int(meta['capacity'])
        destination = out / case; destination.mkdir(exist_ok=True)
        baseline_cache = destination / 'fixed_dtft_raw_extraction.json'
        if baseline_cache.exists():
            cache = bench.read(baseline_cache)
            assert cache['source_sha256'] == bench.sha(HERE / 'fixed_baseline.py') and cache['input_sha256'] == case_plan['input_sha256']
            extracted = cache['observations']
        else:
            began = time.perf_counter()
            extracted = fixed_baseline.extract_observations(x, 'dtft_grid_q30')
            bench.write(baseline_cache, dict(source_sha256=bench.sha(HERE / 'fixed_baseline.py'),
                input_sha256=case_plan['input_sha256'], observations=extracted))
            print(case, 'fixed FFT/DTFT extraction', round(time.perf_counter() - began, 2), 'seconds offline', flush=True)
        method = 'actual_current_q15'
        current_stat, win = score(meta, x, names, rows, current, dict(updates=[]), method, data['input_clocks'], data['output_clocks'])
        current_stat.update(actual_rtl=True, board_measured=False, actual_export=str(folder),
                            configuration_updates=len(actual.table(folder / 'cfg_snapshots.csv')))
        summaries.append(current_stat); windows += win
        dynamic += dynamic_energy(meta, names, rows, current, method)
        audit.append(dict(case=case, actual_verification=check, actual_snapshot_check=actual.validate_basis_snapshots(folder, cap)))
        for method_plan in plan['methods']:
            method = method_plan['name']
            cfg = fixed_baseline.Config(max_centers=cap, threshold_db=method_plan['threshold_db'])
            decisions = fixed_baseline.detect_updates(x, cfg, extracted)
            bench.write(destination / (method + '_decisions.json'), decisions)
            result, detail = replay(rows, x, decisions)
            bench.write(destination / (method + '_bank_diagnostics.json'), detail)
            stat, win = score(meta, x, names, rows, result, decisions, method, data['input_clocks'], data['input_clocks'])
            stat.update(pressure(decisions['updates']))
            stat.update(actual_rtl=False, board_measured=False, zero_service=True,
                        guard_failure_count=len(detail['guard_failures']), baseline_config=decisions['config'])
            summaries.append(stat); windows += win
            dynamic += dynamic_energy(meta, names, rows, result, method)
            np.savez_compressed(destination / (method + '.npz'), input=x, output=result['output'],
                component_names=np.asarray(names), component_output_counts=result['components'],
                banks_q16=result['banks'], trajectory=result['trajectory'], input_clocks=data['input_clocks'])
            bench.csv_write(destination / (method + '_bank_requests.csv'), detail['requests'])
            paired = dict(case=case, capacity=cap, current_method='actual_current_q15', baseline_method=method,
                current_primary_nrmse_percent=current_stat['primary_nrmse_percent'], baseline_primary_nrmse_percent=stat['primary_nrmse_percent'],
                current_error_reduction_relative_percent=100 * (1 - current_stat['primary_nrmse_percent'] / stat['primary_nrmse_percent']),
                current_settled_nrmse_percent=current_stat['settled_diagnostic_nrmse_percent'], baseline_settled_nrmse_percent=stat['settled_diagnostic_nrmse_percent'],
                current_full_probe_abs_db=current_stat['worst_probe_amplitude_abs_change_db'], baseline_full_probe_abs_db=stat['worst_probe_amplitude_abs_change_db'],
                current_updates=current_stat['configuration_updates'], baseline_updates=stat['configuration_updates'],
                baseline_guard_failure_count=len(detail['guard_failures']))
            pairs.append(paired)
            print(json.dumps(paired), flush=True)
        status = dict(status='PARTIAL_OR_REQUESTED_SUBSET', plan_sha256=bench.sha(HERE / 'experiment_plan.json'),
            summaries=summaries, comparisons=pairs, verification=audit,
            completed_cases=sorted(set(s['case'] for s in summaries)),
            conventions=plan, dynamic_energy_file='dynamic_energy.csv',
            limits=['Current is actual complete RTL simulation at requested50MHz; not board measured.',
                    'Conventional1024 is finite-arithmetic software at zero compute/config service, not real RTL.',
                    'Zero service removes waiting, not a quality bound. Update/fade differences remain.',
                    'Same21 numeric threshold is not a proof of matched false alarm.18 is retained sensitivity, not tuned here.',
                    'Not a pure window-length ablation or hardware resource comparison.'])
        bench.write(out / 'results.json', status)
        bench.csv_write(out / 'summary.csv', summaries); bench.csv_write(out / 'paired.csv', pairs)
        bench.csv_write(out / 'windows.csv', windows); bench.csv_write(out / 'dynamic_energy.csv', dynamic)
    status['status'] = 'COMPLETE_FROZEN_FIVE_INPUT_COMPARISON' if len(status['completed_cases']) == len(plan['cases']) else 'PARTIAL_OR_REQUESTED_SUBSET'
    status['executed_sources'] = {str(p): bench.sha(p) for p in (Path(__file__), HERE / 'fixed_baseline.py', HERE / 'implementation_errata.json')}
    bench.write(out / 'results.json', status)
    print('SCORES', out / 'results.json', flush=True)


if __name__ == '__main__':
    main()

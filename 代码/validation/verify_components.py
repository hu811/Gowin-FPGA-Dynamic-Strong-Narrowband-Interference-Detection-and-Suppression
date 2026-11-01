"""Recompute report table 4(b) and joint windows from frozen simulation arrays.

No online detector, FPGA, serial port or new experiment is run. Both window
origins are reported; the global grid is the original report definition.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import numpy as np

HERE = Path(__file__).resolve().parent
CASES = ('c4_temporal_gap2ms_dev', 'c4_temporal_gap8ms_heldout')
DELAY, WINDOW, HOP = 320, 256, 64


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def ratio_db(numerator, denominator):
    return float(10*np.log10(max(float(numerator @ numerator), 1e-30) /
                            max(float(denominator @ denominator), 1e-30)))


def verify_components(csv_path=None):
    manifest = read(HERE/'manifest.json')['files']
    rows, details = [], []
    for case in CASES:
        folder = HERE/'samples/hybrid'/case
        # A standalone run checks the frozen files it actually uses as well.
        paths = [folder/'fixture.json', folder/'expected_results.json', folder/'input.npy']
        paths += [folder/arm/'frozen_operator_and_components.npz'
                  for arm in ('current', 'fixed21')]
        hashes = {}
        for path in paths:
            relative = path.relative_to(HERE).as_posix()
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if manifest[relative] != actual:
                raise AssertionError('Frozen input changed: '+relative)
            hashes[relative] = actual
        fixture = read(folder/'fixture.json')
        expected = read(folder/'expected_results.json')['summaries']
        mixed = np.load(folder/'input.npy', allow_pickle=False)
        overlap_start = max(e['start_sample'] for e in fixture['events'])
        overlap_stop = min(e['end_sample'] for e in fixture['events'])
        primary_start, primary_stop = fixture['primary_output_sample_range']
        paired_input = None
        for arm_index, arm in enumerate(('current', 'fixed21')):
            with np.load(folder/arm/'frozen_operator_and_components.npz', allow_pickle=False) as z:
                names = z['component_names'].tolist()
                x, y = z['component_input_counts'], z['component_output_counts']
            if x.shape != y.shape or x.shape[1] != len(mixed):
                raise AssertionError('Component dimensions')
            if np.max(np.abs(x.sum(axis=0)-mixed)) >= 1e-8:
                raise AssertionError('Components do not reconstruct mixed input')
            if paired_input is not None and not np.array_equal(x, paired_input):
                raise AssertionError('Paired component inputs differ')
            paired_input = x
            ref = np.pad(x, ((0, 0), (DELAY, 0)))[:, :len(mixed)]
            interferers = [names.index('interference_'+str(j)) for j in range(4)]
            probes = [names.index('probe_'+str(j)) for j in range(2)]
            atten = [ratio_db(ref[k, primary_start:primary_stop],
                              y[k, primary_start:primary_stop]) for k in interferers]
            for j, value in enumerate(atten):
                baseline = expected[arm_index]['primary_per_jammer_attenuation_db'][str(j)]
                if abs(value-baseline) > 1e-9:
                    raise AssertionError(('Primary attenuation mismatch', case, arm, j))
            grids = {}
            # Source intervals [s,s+256), output intervals [s+320,s+576).
            # The original report uses s=64*m from sample zero. The diagnostic
            # uses s=overlap_start+64*m. Both must stay fully inside overlap.
            for grid, origin, output_offset in (('report_global', 0, DELAY),
                                 ('overlap_anchored_diagnostic', overlap_start, DELAY),
                                 ('unshifted_output_coordinate_diagnostic', overlap_start, 0)):
                first = origin + max(0, (overlap_start-origin+HOP-1)//HOP)*HOP
                starts = range(first, overlap_stop-WINDOW+1, HOP)
                qualified = streak = longest = total = 0
                first_three = None
                first_start = last_end = None
                for source_begin in starts:
                    begin = source_begin+output_offset
                    end = begin+WINDOW
                    if end > len(mixed):
                        break
                    at = [ratio_db(ref[k, begin:end], y[k, begin:end]) for k in interferers]
                    pr = [ratio_db(y[k, begin:end], ref[k, begin:end]) for k in probes]
                    passed = min(at) >= 20 and max(abs(v) for v in pr) <= 1
                    total += 1
                    qualified += int(passed)
                    streak = streak+1 if passed else 0
                    longest = max(longest, streak)
                    if streak == 3 and first_three is None:
                        first_three = end
                    if first_start is None:
                        first_start = source_begin
                    last_end = source_begin+WINDOW
                    details.append(dict(case=case, arm=arm, grid=grid,
                        source_start_sample=begin-DELAY, source_end_sample_exclusive=end-DELAY,
                        output_start_sample=begin, output_end_sample_exclusive=end,
                        **{f'interference_{j}_attenuation_db':v for j,v in enumerate(at)},
                        **{f'probe_{j}_change_db':v for j,v in enumerate(pr)}, joint_pass=int(passed)))
                grids[grid] = dict(source_start_grid_origin=origin+output_offset-DELAY,
                    valid_four_way_overlap_scoring=output_offset==DELAY,
                    first_source_start_sample=first_start+output_offset-DELAY if first_start is not None else None,
                    last_source_end_sample_exclusive=last_end+output_offset-DELAY if last_end is not None else None,
                    evaluable_windows=total, joint_pass_windows=qualified,
                    longest_joint_streak=longest, three_window_joint_pass=first_three is not None,
                    first_three_output_end_sample_exclusive=first_three)
            rows.append(dict(case=case, arm=arm, primary_output_sample_range=[primary_start,primary_stop],
                primary_attenuation_db=atten, primary_at_least_20db_count=sum(v>=20 for v in atten),
                overlap_source_sample_range=[overlap_start,overlap_stop], grids=grids,
                frozen_files_sha256=hashes))
    if csv_path is not None:
        with Path(csv_path).open('w', newline='', encoding='utf-8-sig') as out:
            writer = csv.DictWriter(out, fieldnames=list(details[0]))
            writer.writeheader()
            writer.writerows(details)
    return dict(scope='Offline re-score of frozen paired simulation components; not a new board experiment.',
        sample_rate_hz=32000, delay_samples=DELAY, window_samples=WINDOW, hop_samples=HOP,
        index_convention='Zero-based half-open [start,stop); output is delayed by 320 samples.',
        report_grid='source start=64*m; output end exclusive=576+64*m; retain only fully overlapping four-interferer windows.',
        diagnostic_grid='source start=latest interferer onset+64*m; same overlap and thresholds; not a replacement for the report grid.',
        coordinate_diagnostic='unshifted_output_coordinate_diagnostic deliberately omits the +320 output offset to reproduce the reviewer counts; its first windows precede full source overlap, so it is NOT valid four-way-overlap evidence.',
        joint_condition='Every interferer attenuation >=20dB and every probe absolute energy-ratio change <=1dB in the same window; comparisons use unrounded values.',
        primary_definition='10log10(sum(delayed component input^2)/sum(component output^2)) over fixture primary_output_sample_range.',
        probe_definition='10log10(sum(probe output^2)/sum(delayed probe input^2)); absolute value <=1dB.',
        rows=rows)


def print_summary(result):
    for row in result['rows']:
        print(row['case'], row['arm'], 'primary dB =',
              '/'.join(f'{v:.8f}' for v in row['primary_attenuation_db']))
        for grid, values in row['grids'].items():
            print(' ', grid, f"{values['joint_pass_windows']}/{values['evaluable_windows']}",
                  'longest =', values['longest_joint_streak'],
                  'three consecutive =', values['three_window_joint_pass'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE/'component_verification_result.json')
    parser.add_argument('--csv', type=Path, default=HERE/'component_windows.csv')
    args = parser.parse_args()
    result = verify_components(args.csv)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print_summary(result)

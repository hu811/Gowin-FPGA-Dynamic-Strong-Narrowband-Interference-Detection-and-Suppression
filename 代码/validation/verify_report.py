"""Re-score frozen actual outputs; never run detection, serial, plotting or P&R."""
from pathlib import Path
import csv
import hashlib
import json
import xml.etree.ElementTree as ET
import numpy as np
from datetime import datetime, timezone
from verify_components import verify_components, print_summary
from scipy import signal

HERE = Path(__file__).resolve().parent


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def close(value, expected):
    if abs(value - expected) > 1e-9:
        raise AssertionError((value, expected))


def rne(values, shift):
    values = np.asarray(values, dtype=np.int64)
    magnitude = np.abs(values)
    q = magnitude >> shift
    remainder = magnitude & ((1 << shift) - 1)
    q += (remainder > (1 << (shift-1))) | ((remainder == (1 << (shift-1))) & ((q & 1) != 0))
    return np.clip(np.where(values < 0, -q, q), -32768, 32767).astype(np.int16)


def nrmse(reference, output, mask):
    a, b = reference[mask], output[mask].astype(float)
    return float(100 * np.sqrt(np.sum((b-a)**2) / np.sum(a*a)))


def replay_operator(x, data):
    """Same integer convolution, bank snapshots and alpha as archived scorer."""
    banks, trajectory = data['banks_q16'], data['trajectory']
    if not np.array_equal(banks, banks[:, ::-1]):
        raise AssertionError('Asymmetric archived FIR')
    size = len(x)
    split = np.r_[0, np.flatnonzero(np.any(trajectory[1:, :2] != trajectory[:-1, :2], axis=1))+1, size]
    numerator = np.zeros(size, np.int64)
    rows = data['component_input_counts']
    component = np.zeros_like(rows)
    mixed_history = np.pad(x.astype(np.int64), (640, 0))
    component_history = np.pad(rows, ((0, 0), (640, 0)))
    for start, stop in zip(split[:-1], split[1:]):
        old, new = trajectory[start, :2]
        weights = {int(old): 32-trajectory[start:stop, 2]}
        weights[int(new)] = weights.get(int(new), 0)+trajectory[start:stop, 2]
        for bank, weight in weights.items():
            if not np.any(weight):
                continue
            h = banks[bank]
            numerator[start:stop] += np.convolve(mixed_history[start:stop+640], h, 'valid')*weight
            for row in range(len(rows)):
                component[row, start:stop] += np.convolve(component_history[row, start:stop+640], h/65536, 'valid')*weight/32
    differences = int(np.count_nonzero(rne(numerator, 21) != data['actual_output']))
    maximum_component_error = float(np.max(np.abs(component-data['component_output_counts'])))
    if differences or maximum_component_error >= 1e-7:
        raise AssertionError(('operator replay', differences, maximum_component_error))
    return dict(int16_mismatches=differences, component_max_difference_counts=maximum_component_error)


def dynamic():
    results = []
    for case in ('c4_temporal_gap2ms_dev', 'c4_temporal_gap8ms_heldout'):
        folder = HERE/'samples/hybrid'/case
        fixture = read(folder/'fixture.json')
        x = np.load(folder/'input.npy', allow_pickle=False)
        if sha(folder/'input.npy') != fixture['input_sha256']:
            raise AssertionError('Fixture input hash')
        expected = read(folder/'expected_results.json')['comparisons'][0]
        arms = {}
        first_rows = None
        first_clocks = None
        for name, field in (('current', 'current'), ('fixed21', 'baseline')):
            with np.load(folder/name/'frozen_operator_and_components.npz', allow_pickle=False) as archive:
                data = {key: archive[key].copy() for key in archive.files}
            names = data['component_names'].tolist()
            rows = data['component_input_counts']
            if np.max(np.abs(rows.sum(axis=0)-x)) >= 1e-8:
                raise AssertionError('Mixture component reconstruction')
            if first_rows is not None and (not np.array_equal(rows, first_rows) or not np.array_equal(data['input_clocks'], first_clocks)):
                raise AssertionError('Paired inputs or input clocks differ')
            first_rows, first_clocks = rows, data['input_clocks']
            if set(np.diff(data['input_clocks']).tolist()) - {1562, 1563}:
                raise AssertionError('Input cadence')
            proof = replay_operator(x, data)
            clean = rows[[i for i, key in enumerate(names) if key == 'target' or key.startswith('probe_')]].sum(axis=0)
            reference = np.pad(clean, (320, 0))[:len(x)]
            a, b = fixture['primary_output_sample_range']
            primary = nrmse(reference, data['actual_output'], slice(a, b))
            close(primary, expected[field+'_primary_nrmse_percent'])
            mask = np.zeros(len(x), bool)
            changes = sorted(set(value for event in fixture['events'] for value in (event['start_sample'], event['end_sample'])))
            for change, next_change in zip(changes, changes[1:]+[len(x)]):
                active = any(event['start_sample'] <= change < event['end_sample'] for event in fixture['events'])
                start, stop = change+1280+320, min(len(x), next_change)
                if active and start < stop:
                    mask[start:stop] = True
            settled = nrmse(reference, data['actual_output'], mask)
            close(settled, expected[field+'_settled_nrmse_percent'])
            arms[name] = dict(primary_nrmse_percent=primary, settled_nrmse_percent=settled, replay=proof,
                              samples=len(x), primary_output_samples=[a, b], settled_samples=int(mask.sum()))
        reduction = 100*(1-arms['current']['primary_nrmse_percent']/arms['fixed21']['primary_nrmse_percent'])
        close(reduction, expected['current_relative_nrmse_reduction_percent'])
        results.append(dict(case=case, arms=arms, relative_nrmse_reduction_percent=reduction,
                            scope='Frozen actual complete/current and common-backend HYBRID output re-score; no new RTL run.'))
    return results


def switching():
    folder = HERE/'samples/switching'
    expected = read(folder/'results.json')
    with (folder/'coefficients.csv').open(newline='', encoding='utf-8') as handle:
        coefficients = list(csv.DictReader(handle))
    h1 = np.array([int(row['bank1']) for row in coefficients], np.int64)
    h2 = np.array([int(row['bank2']) for row in coefficients], np.int64)
    h1, h2 = np.r_[h1, h1[-2::-1]], np.r_[h2, h2[-2::-1]]
    hp = signal.firwin(129, 10000, fs=32000, pass_zero=False)
    results = []
    for case in range(2):
        x = np.load(folder/f'case{case}_input.npy', allow_pickle=False)
        conv1 = np.convolve(x.astype(np.int64), h1)[:len(x)]
        conv2 = np.convolve(x.astype(np.int64), h2)[:len(x)]
        delay = np.r_[np.zeros(320, np.int64), x.astype(np.int64)][:len(x)]*65536
        hard = delay.copy()
        hard[800:2304], hard[2304:] = conv1[800:2304], conv2[2304:]
        blend = hard*32
        for start, old, new in ((800, delay, conv1), (2304, conv1, conv2)):
            for offset in range(32):
                index = start+offset
                blend[index] = (31-offset)*old[index]+(offset+1)*new[index]
        energy = {}
        for mode, model in (('hard', rne(hard, 16)), ('fade', rne(blend, 21))):
            actual = np.load(folder/f'case{case}_{mode}_rtl.npy', allow_pickle=False)
            if not np.array_equal(actual, model):
                raise AssertionError(('component integer replay', case, mode))
            high = signal.convolve(actual.astype(float)-rne(conv1, 16).astype(float), hp, mode='same')
            energy[mode] = float(np.sum(high[2304-128:2304+160]**2))
        change = float(10*np.log10(energy['fade']/energy['hard']))
        close(change, expected['cases'][case]['metrics']['fade_minus_hard_highband_energy_db'])
        results.append(dict(case=case, int16_mismatches=0, fade_minus_hard_highband_energy_db=change,
                            extra_samples_to_new_trajectory=31, extra_ms=31/32000*1000))
    return results


def area():
    result = {}
    for name in ('independent_dual_lane', 'merged_storage_production_cache'):
        attrib = ET.parse(HERE/'samples/area'/name/'area_syn_rsc.xml').getroot().attrib
        integer = lambda key: int(attrib[key].split('(')[0])
        result[name] = dict(LUT=integer('T_Lut'), ALU=integer('T_Alu'), REG=integer('T_Register'),
                            MULTALU27X18=integer('T_MULTALU27X18'), BSRAM=integer('T_Bsram'))
        result[name]['LUT_ALU'] = result[name]['LUT']+result[name]['ALU']
    a, b = result.values()
    if (a['LUT_ALU'], a['REG'], a['MULTALU27X18'], a['BSRAM']) != (921, 488, 2, 6):
        raise AssertionError(a)
    if (b['LUT_ALU'], b['REG'], b['MULTALU27X18'], b['BSRAM']) != (986, 523, 1, 7):
        raise AssertionError(b)
    return dict(archived_synthesis=result, deltas={key:b[key]-a[key] for key in a},
                scope='Parse frozen original resource XML; synthesis/P&R not rerun. Native MAC units, not full-device DSP normalization.')


def main():
    frozen = read(HERE/'manifest.json')
    for name, expected in frozen['files'].items():
        if sha(HERE/name) != expected:
            raise AssertionError('Frozen package file changed: '+name)
    components = verify_components(HERE/'component_windows.csv')
    result = dict(status='PASS_ARCHIVED_OUTPUT_RESCORING', dynamic=dynamic(), switching=switching(), area=area(),
                  components=components, verified_at_utc=datetime.now(timezone.utc).isoformat(),
                  evidence='Re-score/replay immutable existing actual RTL outputs and original XML; no new detection/RTL/P&R/board experiment.',
                  full_rtl_rerun_performed=False, serial_opened=False, plots_generated=False,
                  limitations=['HYBRID 1024 frontend is precomputed/zero-time; not complete online 1024 RTL or board comparison.',
                               'Retains existing failures: some full-window midpoint errors exceed 1dB and not all 20dB/30ms goals pass.',
                               'Smoothing lowers defined high-band switching energy, adds 31 samples, and does not guarantee lower total target error.',
                               'Merged subsystem saves one native MAC but adds logic/registers/RAM; not total system area reduction.'])
    (HERE/'verification_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(result['status'])
    print_summary(components)
    for row in result['dynamic']:
        print(row['case'], 'NRMSE relative reduction =', f"{row['relative_nrmse_reduction_percent']:.8f}%")
    print('Switch high-band fade/hard =', f"{result['switching'][0]['fade_minus_hard_highband_energy_db']:.8f} dB")
    print('Merged minus independent:', result['area']['deltas'])


if __name__ == '__main__':
    main()

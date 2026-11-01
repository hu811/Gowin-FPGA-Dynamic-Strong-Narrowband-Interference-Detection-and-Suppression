"""Minimal frozen-configuration feasibility check; no production RTL changes.

Weights are computed only from estimated centers and the actual correction ROM.
True component labels are accessed exclusively by offline evaluation.
"""
from pathlib import Path
import csv, hashlib, json, sys
import numpy as np
from scipy import signal

HERE = Path(__file__).resolve().parent
BUILDER = HERE.parent/'v23_conventional_comparison_work'
if not BUILDER.is_dir():
    BUILDER = Path('D:/FPGA_Competition/validation_conventional_v23')
sys.path.insert(0, str(BUILDER))
from fir_replay import build_bank, _correction, _rne_clip_array, quantized_frequencies

ARCHIVE = Path('D:/FPGA_Competition/validation_evidence_v21/board_quality/reference_capture')
FS, D = 32000, 320


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metrics(reference, output):
    reference, output = np.asarray(reference, float), np.asarray(output, float)
    error = output-reference
    return dict(nrmse_percent=float(100*np.linalg.norm(error)/np.linalg.norm(reference)),
                pearson=float(np.corrcoef(reference, output)[0, 1]),
                energy_percent=float(100*np.dot(output, output)/np.dot(reference, reference)),
                error_rms_counts=float(np.sqrt(np.mean(error**2))))


def align(values):
    return np.pad(values, (D, 0))[:len(values)]


def full(half):
    return np.r_[half, half[-2::-1]]


def bank_for(centers, depth):
    """depth=None is production; .98 only fixes overlap; 1.0 changes depth too."""
    correction = np.array([full(np.asarray(_correction(int(round(f/(FS/8192)))), dtype=np.int64)) for f in centers])
    n = np.arange(641)-D
    G = np.array([[float((b @ np.exp(-2j*np.pi*f*n/FS)/65536).real) for b in correction] for f in centers])
    if depth is None:
        weights = np.ones(len(centers))
        weighted = correction
    else:
        weights = np.linalg.solve(G, np.full(len(centers), depth))
        # Explicit offline float weights -> each branch Q16 RNE -> merged Q16.
        # This is not yet a fixed-point implementation of the weight solver.
        weighted = np.rint(correction*weights[:, None]).astype(np.int64)
    h = -weighted.sum(axis=0)
    h[D] += 65536
    assert np.array_equal(h, h[::-1])
    overflow = int(np.sum((h < -131072)|(h > 131071)))
    assert overflow == 0
    if depth is None:
        assert np.array_equal(h, build_bank(centers))
    return h, dict(estimated_centers_hz=centers, desired_correction_depth=depth,
                   G=G.tolist(), condition_number=float(np.linalg.cond(G)), weights=weights.tolist(),
                   coefficient_min=int(h.min()), coefficient_max=int(h.max()), overflow_coefficients=overflow)


def build_weighted_bank(frequencies_hz, depth=1.0, return_diagnostics=False):
    """Drop-in bank API for matched-baseline replay; uses no labels or samples.

    Frequencies are quantized by the original builder grid rule. Empty centers
    return the original delayed bypass bank. No condition-number rejection or
    fallback policy is silently added: singular solves raise, and signed-18-bit
    overflow raises. Caller must record such failures rather than skip cases.
    """
    centers = quantized_frequencies(frequencies_hz)
    if not centers:
        h, info = build_bank([]), dict(estimated_centers_hz=[], weights=[], condition_number=None,
                                      desired_correction_depth=depth, overflow_coefficients=0)
    else:
        h, info = bank_for(centers, depth)
    return (h, info) if return_diagnostics else h


def main():
    x, actual = np.load(ARCHIVE/'input.npy'), np.load(ARCHIVE/'output.npy')
    source = next((ARCHIVE/'source').glob('*_components.npz'))
    with np.load(source) as z:
        names = [k for k in z.files if k == 'target' or k.startswith(('probe_', 'interference_'))]
        rows = np.vstack([z[k]*32767 for k in names])
    assert np.array_equal(np.rint(rows.sum(axis=0)).astype(np.int16), x)
    rows = np.vstack([rows, x.astype(float)-rows.sum(axis=0)])
    names.append('input_quantization')
    clean_ids = [i for i, k in enumerate(names) if k == 'target' or k.startswith('probe_')]
    reference = align(rows[clean_ids].sum(axis=0))
    target = names.index('target')
    cases = [('first_pair_stable', [2800.78125,2925.78125],11200,22400),
             ('two_pairs_candidate', [2800.78125,2925.78125,5027.34375,5148.4375],30400,40000)]
    results = []
    for label, centers, start, stop in cases:
        entries = []
        for arm, depth in [('production',None), ('joint_same_depth_098',.98), ('joint_full_depth_100',1.)]:
            h, config = bank_for(centers, depth)
            linear = signal.lfilter(h/65536., [1.], rows, axis=-1)
            numerator = np.convolve(x.astype(np.int64), h)[:len(x)]
            output, saturation = _rne_clip_array(numerator, shift=16)
            s = slice(start, stop)
            record = dict(arm=arm, configuration=config,
                          complete_output=metrics(reference[s], output[s]),
                          target_only=metrics(align(rows[target])[s], linear[target,s]),
                          saturations_full_input=saturation, components={})
            for i, name in enumerate(names):
                if not name.startswith(('probe_', 'interference_')):
                    continue
                inp, out = align(rows[i])[s], linear[i,s]
                power = float(inp @ inp)
                if power < 1e-12:
                    continue
                change = float(10*np.log10(max(float(out @ out),1e-30)/power))
                record['components'][name] = dict(power_change_db=change,
                    attenuation_db=-change, output_rms_counts=float(np.sqrt(np.mean(out**2))))
            if arm == 'production':
                mismatches = int(np.count_nonzero(output[s] != actual[s]))
                maxerror = int(np.max(np.abs(output[s].astype(int)-actual[s].astype(int))))
            entries.append(record)
        matched = mismatches == 0
        if label == 'first_pair_stable':
            assert matched, (mismatches,maxerror)
        results.append(dict(case=label, output_samples=[start, stop],
                            production_replay_vs_actual=dict(mismatches=mismatches,max_error_lsb=maxerror),
                            formal_frozen_actual_bank_comparison=matched,
                            status='frozen_actual_stable_bank' if matched else 'hypothetical_bank_only_NOT_formal_current_comparison',
                            arms=entries))
    report = dict(status='OFFLINE_FROZEN_CONFIGURATION_FEASIBILITY_ONLY', cases=results,
        scope='Identical input, original estimated centers, 641 taps, Q16 correction/merged coefficients and continuous history. The .98 arm changes only joint overlap normalization; 1.00 additionally changes desired notch depth. No online true-frequency/clean-label access.',
        limitations=['No complete detector/control/queue re-run; no RTL modification, synthesis or timing proof.',
                     'Weight solve is floating offline. Quantized coefficient execution has exact integer output RNE/saturation. A fixed-point weight solver and multiplier format are still to design.',
                     'Other detectors must receive the same improved coefficient builder before claiming an algorithm-specific superiority.',
                     'The second-pair region is formal only if the original fixed bank exactly reproduces all actual samples; otherwise it is labeled hypothetical.'],
        hardware_note='Only backend coefficient weighting is proposed. The existing detector symmetric LDL is busy and cannot directly solve this generally nonsymmetric response matrix. C4 is physically full (11486/11520 CLS,56/56 BSRAM); weight solve, arithmetic reuse, cycles and P&R must be verified, not assumed free. Preserve bank/version/ownership and deadlines.',
        sha256={str(p):sha(p) for p in [Path(__file__),BUILDER/'fir_replay.py',BUILDER/'rom/prototype.mem',BUILDER/'rom/cos_grid_direct.mem',ARCHIVE/'input.npy',ARCHIVE/'output.npy',source]})
    (HERE/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()

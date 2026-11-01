"""Read-only exhaustive primary-energy partition of the existing C4 experiments."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
import numpy as np

HERE = Path(__file__).resolve().parent
V29 = HERE.parents[1]/'v29_c4_control_repair_work'
sys.path.insert(0, str(V29/'software'))
import score_actual as previous
import score_selected as selected
bench = previous.bench
CASES = ('c4_two_pairs_staggered', 'c4_second_pair_simultaneous',
         'c4_both_pairs_enter_together_holdout')+selected.NEW


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--event-alignment',choices=('group_delay','input_clock'),default='group_delay')
    args=parser.parse_args()
    event_shift=320 if args.event_alignment=='group_delay' else 0
    out = HERE/'diagnosis'
    out.mkdir(parents=True, exist_ok=True)
    records, summaries = [], []
    for case in CASES:
        ref_folder = selected.folder(case)
        meta,x,names,rows,current,data,check = previous.actual.load_actual(ref_folder, selected.fixture(case))
        start, stop = meta['primary_output_sample_range']
        aligned = bench.align(rows)
        clean = [i for i,k in enumerate(names) if k == 'target' or k.startswith('probe_')]
        ref = aligned[clean].sum(axis=0)
        clean_power = float(ref[start:stop]@ref[start:stop])
        changes = sorted({int(e[key]) for e in meta['events'] for key in ('start_sample','end_sample')})
        points = sorted({start,stop}|{min(stop,max(start,p+event_shift)) for p in changes}|
                        {min(stop,max(start,p+event_shift+1600)) for p in changes})
        arms = {'actual_v27_q15':current}
        for db in (21,18):
            arms['conventional1024_fixed'+str(db)+'_dtft'] = selected.baseline(case, db, x, names)[0]
        for method,result in arms.items():
            error = result['output'].astype(float)-ref
            energy = float(error[start:stop]@error[start:stop])
            per_kind = {}
            partition_sum = 0
            for a,b in zip(points[:-1], points[1:]):
                source_a,source_b = a-event_shift,b-event_shift
                preceding = [p for p in changes if p <= source_a]
                change = max(preceding) if preceding else None
                on = [int(e['center']) for e in meta['events'] if e['start_sample']==change]
                off = [int(e['center']) for e in meta['events'] if e['end_sample']==change]
                kind = 'before_changes' if change is None else ('on' if on and not off else 'off' if off and not on else 'mixed')
                phase = '0to50ms' if change is not None and source_a-change < 1600 else 'after50ms'
                category = kind+'_'+phase
                segment_energy = float(error[a:b]@error[a:b]); partition_sum += segment_energy
                clean_error = result['components'][clean,a:b].sum(axis=0)-ref[a:b]
                jam_rows = [i for i,k in enumerate(names) if k.startswith('interference_')]
                jam_output = result['components'][jam_rows,a:b].sum(axis=0)
                record = dict(case=case,method=method,start_output_sample=a,stop_output_sample=b,
                    preceding_change_sample=change,on_centers=on,off_centers=off,category=category,
                    start_ms_after_change=None if change is None else (source_a-change)/32,
                    stop_ms_after_change=None if change is None else (source_b-change)/32,
                    samples=b-a,error_energy_counts2=segment_energy,
                    fraction_of_primary_error_energy=segment_energy/energy,
                    nrmse_squared_contribution=10000*segment_energy/clean_power,
                    clean_operator_error_energy_counts2=float(clean_error@clean_error),
                    combined_jammer_output_energy_counts2=float(jam_output@jam_output),
                    cross_rounding_remainder_counts2=segment_energy-float(clean_error@clean_error)-float(jam_output@jam_output))
                records.append(record);per_kind[category]=per_kind.get(category,0)+segment_energy
            assert abs(partition_sum-energy) <= max(1e-6,energy*1e-12)
            summaries.append(dict(case=case,method=method,primary_nrmse_percent=100*np.sqrt(energy/clean_power),
                primary_error_energy_counts2=energy,reference_energy_counts2=clean_power,
                categories={k:dict(energy=v,fraction=v/energy) for k,v in per_kind.items()},
                fixture_sha256=bench.sha(selected.fixture(case)/'input.npy')))
    paired = []
    for case in CASES:
        current = next(s for s in summaries if s['case']==case and s['method']=='actual_v27_q15')
        baseline = next(s for s in summaries if s['case']==case and s['method']=='conventional1024_fixed21_dtft')
        target = baseline['primary_nrmse_percent']*.8
        allowed_energy = current['reference_energy_counts2']*(target/100)**2
        paired.append(dict(case=case,current_nrmse_percent=current['primary_nrmse_percent'],
            baseline21_nrmse_percent=baseline['primary_nrmse_percent'],target_nrmse_percent=target,
            required_reduction_in_current_error_energy_percent=100*(1-allowed_energy/current['primary_error_energy_counts2']),
            categories_current=current['categories'],categories_baseline21=baseline['categories']))
    suffix='' if args.event_alignment=='group_delay' else '_input_clock'
    bench.csv_write(out/('primary_segments'+suffix+'.csv'), records)
    bench.write(out/('primary_partition'+suffix+'.json'), dict(status='READ_ONLY_DIAGNOSIS',event_shift_samples=event_shift,
        allocation=args.event_alignment,clean_reference_delay_samples=320,summaries=summaries,comparisons=paired,
        source_sha256=bench.sha(Path(__file__)), limits=[
            'Labels only partition frozen outputs offline; not a proposed online rule.',
            'Every original primary sample included once; NRMSE is unchanged.',
            'Clean/jammer energy terms have cross terms; they must not be added as independent errors.',
            'Current is actual V27, conventional1024 is frozen software with zero compute/config waiting.',
            'Prior V29 holdout is diagnostic development information for V30, not a new V30 holdout.']))
    print(json.dumps(paired,indent=2))


if __name__ == '__main__':
    main()

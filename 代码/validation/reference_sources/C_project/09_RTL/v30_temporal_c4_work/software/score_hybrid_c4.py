"""Separate common-backend HYBRID; fixed software front end is not1024 RTL."""
from pathlib import Path
import argparse
import importlib.util
import os
import sys
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
import numpy as np
import score_selected as selected
bench,old=selected.bench,selected.old
HERE=Path(__file__).resolve().parent
LEGACY=selected.helpers.RTL/'v28_controlled_update_work/software/comparison_1024/score_hybrid.py'
spec=importlib.util.spec_from_file_location('frozen_common_backend_audit',LEGACY)
legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=selected.NEW,required=True)
    parser.add_argument('--current',type=Path,required=True);parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--threshold-db',type=int,choices=[21],default=21);args=parser.parse_args()
    choice=bench.read(HERE/'selected_variant.json');assert choice['choice']=='KEEP_V27'
    fixture=selected.fixture(args.case);original=selected.folder(args.case)
    output=HERE/'hybrid_results'/(args.case+'_fixed21');output.mkdir(parents=True,exist_ok=True)
    with np.load(original/'frozen_operator_and_components.npz') as z:reference={key:z[key].copy() for key in z.files}
    summaries=[];windows=[];dynamic=[];audits=[];first=None
    for method,folder in [('hybrid_current_q15',args.current),('hybrid1024_fixed21_dtft',args.baseline)]:
        (meta,x,names,rows,result,data,check),identity=legacy.load(folder,fixture)
        assert meta['capacity']==4
        if first is None:
            equal={}
            for key in ('actual_output','input_clocks','output_clocks','component_input_counts','component_output_counts'):
                assert data[key].shape==reference[key].shape
                mismatch=int(np.count_nonzero(data[key]!=reference[key]));assert mismatch==0,(key,mismatch)
                equal[key]=dict(shape=list(data[key].shape),mismatches=mismatch)
            identity['current_original_complete_rtl_exact_reproduction']=equal;first=data
        else:
            policy=identity['policy_manifest'];decisions=bench.read(Path(policy['detector_decisions']))
            assert decisions['config']['max_centers']==4 and decisions['config']['threshold_db']==21
            assert decisions['config']['window']==1024
            assert np.array_equal(data['input_clocks'],first['input_clocks'])
            assert np.array_equal(data['component_input_counts'],first['component_input_counts'])
        stat,win=old.fixed_comparison.score(meta,x,names,rows,result,dict(updates=[]),method,
            data['input_clocks'],data['output_clocks'])
        mask=np.zeros(len(x),bool);a,b=meta['primary_output_sample_range'];mask[a:b]=True
        stat['primary_per_jammer_attenuation_db']=old.evaluate_q15.attenuation(meta,names,rows,result,mask)
        stat.update(old.control.commit_pressure(folder))
        stat.update(kind='HYBRID',board_measured=False,complete_online_detector_rtl=False,actual_backend_rtl=True,
            export=str(folder),
            frontend='Replayed actual unchangedV27 manager-input vector changes with original clocks' if first is data else
                     'Frozen mixed-input integer1024DTFT21 plus declared group/ID/version adapter;zero frontendcompute',
            timestamps='Actual common manager/Q15generator/queue/bank/fade/FIR RTL clocks at requested50MHz,notboardmeasured')
        for event in stat['response_events']:event['timing']='Actual common-backendRTL output clocks;HYBRID frontends,notboardmeasurement'
        summaries.append(stat);windows+=win;dynamic+=old.fixed_comparison.dynamic_energy(meta,names,rows,result,method)
        audits.append(dict(method=method,**check,identity=identity))
    a,b=summaries
    comparison=dict(case=args.case,capacity=4,threshold_db=21,
        current_primary_nrmse_percent=a['primary_nrmse_percent'],baseline_primary_nrmse_percent=b['primary_nrmse_percent'],
        current_relative_nrmse_reduction_percent=100*(1-a['primary_nrmse_percent']/b['primary_nrmse_percent']),
        current_settled_nrmse_percent=a['settled_diagnostic_nrmse_percent'],baseline_settled_nrmse_percent=b['settled_diagnostic_nrmse_percent'],
        current_target_nrmse_percent=a['primary_target_nrmse_percent'],baseline_target_nrmse_percent=b['primary_target_nrmse_percent'],
        current_full_probe_abs_db=a['worst_probe_amplitude_abs_change_db'],baseline_full_probe_abs_db=b['worst_probe_amplitude_abs_change_db'],
        current_commits=a['commits'],baseline_commits=b['commits'])
    bench.write(output/'results.json',dict(status='COMPLETE_CASE_COMMON_BACKEND_HYBRID',choice='KEEP_V27',case=args.case,
        selected_variant_sha256=bench.sha(HERE/'selected_variant.json'),plan_sha256=bench.sha(HERE/'experiment_plan.json'),
        summaries=summaries,comparisons=[comparison],verification=audits,
        source_sha256={str(p):bench.sha(p) for p in (Path(__file__),LEGACY,HERE/'score_selected.py')},
        original_actual_archive_sha256=bench.sha(original/'frozen_operator_and_components.npz'),
        limits=['OriginalV27 unchanged,not a new optimized algorithm.',
            'Only this case,not all dynamicinputs. Frontend1024FFT/DTFT computation is precomputed/zero-time;not a complete1024RTL design or hardwarecost comparison.',
            'Actual common manager,Q15build,queue,fade,FIR outputs and clocks are scored. Current exactly reproduces completeRTL original output/clocks.',
            'Baseline group/ID/version adapter declared separately;not pure window ablation.',
            'Full1dBprobe violations and software18sensitivity remain in finalreport.20%NRMSE advantage is not universal protection qualification.',
            'Requested50MHzsimulation,notboardmeasured. No newP&R orfs.']))
    bench.csv_write(output/'summary.csv',summaries);bench.csv_write(output/'paired.csv',[comparison])
    bench.csv_write(output/'windows.csv',windows);bench.csv_write(output/'dynamic_energy.csv',dynamic)
    bench.csv_write(output/'responses.csv',[dict(method=s['method'],**e) for s in summaries for e in s['response_events']])
    print(comparison)


if __name__=='__main__':main()

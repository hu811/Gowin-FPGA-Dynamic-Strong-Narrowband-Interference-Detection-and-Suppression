"""Frozen KEEP_V27 capacity4 versus the unchanged defined1024 references."""
from pathlib import Path
import argparse
import os
import sys
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
import numpy as np
import retire64_counterfactual as helpers
old,bench=helpers.old,helpers.bench
HERE=Path(__file__).resolve().parent
NEW=('c4_temporal_gap2ms_dev','c4_temporal_gap8ms_heldout')
CASES=helpers.OLD_CASES+('quiet_high',)+NEW
EXPECTED='902174283ea44636d59aa59c88a73cabfe551ccd4358599edd11fb56baa25897'


def fixture(case):return HERE/'fixtures'/case if case in NEW else old.fixture(case)
def folder(case):return HERE.parent/'rtl_validation/results'/(case+'_v27_actual') if case in NEW else helpers.export(case)


def baseline(case,db,x,names):
    if case not in NEW:return old.baseline(case,db,x,names)[:2]
    path=HERE/'baseline_results'/case/('conventional1024_fixed'+str(db)+'_dtft')
    decisions=bench.read(path.with_name(path.name+'_decisions.json'))
    detail=bench.read(path.with_name(path.name+'_bank_diagnostics.json'));assert not detail['guard_failures']
    with np.load(path.with_suffix('.npz')) as z:
        assert np.array_equal(z['input'],x) and z['component_names'].tolist()==names
        result=dict(output=z['output'],components=z['component_output_counts'],banks=z['banks_q16'],trajectory=z['trajectory'],saturations=0)
    return result,decisions


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cases',nargs='*',default=list(CASES));args=parser.parse_args()
    assert set(args.cases)<=set(CASES)
    choice=bench.read(HERE/'selected_variant.json');assert choice['choice']=='KEEP_V27' and choice['controller_sha256']==EXPECTED
    output=HERE/'results';output.mkdir(exist_ok=True)
    summaries=[];windows=[];dynamic=[];verification=[];identities=[];pending=[];comparisons=[]
    for case in args.cases:
        actual_folder=folder(case)
        if not (actual_folder/'verification.json').exists():pending.append(case);continue
        if case in NEW and not (HERE/'baseline_results'/case/'conventional1024_fixed18_dtft.npz').exists():pending.append(case);continue
        meta,x,names,rows,current,data,check=old.actual.load_actual(actual_folder,fixture(case))
        manifest=bench.read(actual_folder/'build_manifest.json')
        controller=[(Path(p),digest) for p,digest in manifest['source_sha256'].items() if Path(p).name=='group_controller_v14.v']
        assert len(controller)==1 and controller[0][1]==EXPECTED and bench.sha(controller[0][0])==EXPECTED
        identity=old.control.read_identity(actual_folder);assert identity['status']=='PASS'
        identities.append(dict(case=case,**identity));verification.append(dict(case=case,**check))
        arms={'actual_v27_q15':(current,dict(updates=[]))}
        for db in (21,18):arms['conventional1024_fixed'+str(db)+'_dtft']=baseline(case,db,x,names)
        local={}
        for method,(result,decisions) in arms.items():
            stat,win=bench.score(meta,names,rows,result,decisions,method,'same_q15_fir')
            stat.update(old.actual.settled_diagnostic(meta,rows,names,result))
            stat.update(old.control.false_active_diagnostic(meta,result))
            actual=method=='actual_v27_q15'
            output_clocks=data['output_clocks'] if actual else data['input_clocks']
            stat['response_events']=old.actual.event_responses(meta,win,data['input_clocks'],output_clocks)
            for event in stat['response_events']:
                event['timing']='CompleteRTL simulated clocks at requested50MHz;not board measured.' if actual else 'Software zero analysis/config waiting;not1024RTL timing.'
            mask=np.zeros(len(x),bool);a,b=meta['primary_output_sample_range'];mask[a:b]=True
            stat['primary_per_jammer_attenuation_db']=old.evaluate_q15.attenuation(meta,names,rows,result,mask)
            if actual:stat.update(old.control.commit_pressure(actual_folder))
            else:stat.update(old.fixed_comparison.pressure(decisions['updates']))
            stat.update(kind='COMPLETE_CLOSED_LOOP_RTL_SIMULATION' if actual else 'FINITE_ARITHMETIC_SOFTWARE',
                board_measured=False,source_controller_sha256=EXPECTED if actual else None,
                zero_analysis_and_config_waiting=not actual,
                input_sha256=bench.sha(fixture(case)/'input.npy'),case_role=meta.get('role','previously_seen_development'))
            summaries.append(stat);windows+=win;local[method]=stat
            dynamic+=old.fixed_comparison.dynamic_energy(meta,names,rows,result,method)
        for db in (21,18):
            reference=local['conventional1024_fixed'+str(db)+'_dtft'];selected=local['actual_v27_q15']
            gain=100*(1-selected['primary_nrmse_percent']/reference['primary_nrmse_percent'])
            comparisons.append(dict(case=case,reference_threshold_db=db,
                current_primary_nrmse_percent=selected['primary_nrmse_percent'],reference_primary_nrmse_percent=reference['primary_nrmse_percent'],
                relative_primary_nrmse_reduction_percent=gain,meets20percent_relative_nrmse=gain>=20,
                current_settled_nrmse_percent=selected['settled_diagnostic_nrmse_percent'],reference_settled_nrmse_percent=reference['settled_diagnostic_nrmse_percent'],
                current_target_nrmse_percent=selected['primary_target_nrmse_percent'],reference_target_nrmse_percent=reference['primary_target_nrmse_percent'],
                current_full_worst_probe_db=selected['worst_probe_amplitude_abs_change_db'],reference_full_worst_probe_db=reference['worst_probe_amplitude_abs_change_db'],
                current_full_probe_exceeds1db=selected['worst_probe_amplitude_abs_change_db']>1,
                current_commits=selected['commits'],reference_updates=reference['configuration_updates']))
        print(case,comparisons[-2:],flush=True)
    bench.write(output/'results.json',dict(status='COMPLETE_FROZEN_SELECTED_SUITE' if not pending and set(args.cases)==set(CASES) else 'PARTIAL',
        choice=choice,selected_variant_sha256=bench.sha(HERE/'selected_variant.json'),
        controller_sha256=EXPECTED,summaries=summaries,comparisons=comparisons,windows_file='windows.csv',
        dynamic_file='dynamic_energy.csv',verification=verification,source_identity=identities,pending=pending,
        source_sha256=bench.sha(Path(__file__)),plan_sha256=bench.sha(HERE/'experiment_plan.json'),
        limits=['KEEP_V27 is unchanged algorithm; no improvement attribution to rejected retirement code.',
            'CompleteRTL at requested50MHz is simulation,not board/P&R evidence. No newfs.',
            'Same21dB fixed1024 is an unchanged defined software reference with zero analysis/config waiting,not a quality bound or pure-window ablation.',
            'Previous18dB developer-selected setting remains sensitivity control; no newthreshold tuning.',
            'Full-primary includes all transitions. Stable diagnostic excludes40msafter/10msbeforechanges and cannot replaceprimary.',
            'Every full8ms probe failure remains; >20%NRMSE improvement does not imply1dBprotection or universaladvantage.',
            'All new inputseeds/frequencies/times frozen before outcomes; newheldout run onlyafterKEEP_V27selection.']))
    bench.csv_write(output/'summary.csv',summaries);bench.csv_write(output/'comparisons.csv',comparisons)
    bench.csv_write(output/'windows.csv',windows);bench.csv_write(output/'dynamic_energy.csv',dynamic)
    bench.csv_write(output/'responses.csv',[dict(case=s['case'],method=s['method'],**e) for s in summaries for e in s['response_events']])


if __name__=='__main__':main()

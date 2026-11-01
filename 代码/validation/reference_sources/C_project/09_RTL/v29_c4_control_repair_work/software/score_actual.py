"""Frozen V29 actual closed-loop C4 scoring, preserving all input failures."""
from pathlib import Path
import argparse
import json
import sys
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
RTL=ROOT.parent
V27=RTL/'v27_dynamic_validation_work'
V28=RTL/'v28_controlled_update_work'
sys.path.insert(0,str(V28/'software'))
import score_actual_control as control
from source_policies import expected_controller
sys.path.insert(0,str(V28/'software/comparison_1024'))
import run_comparison as fixed_comparison
import evaluate_q15

bench,actual=control.bench,control.old
CASES=('c4_two_pairs_staggered','c4_second_pair_simultaneous','quiet_high','c4_both_pairs_enter_together_holdout')
EXPECTED='9a808a124c942ec7a00aaf0dfe3b90666937e0c6bc743ae1fc38044f332d3c57'


def fixture(case):
    return HERE/'holdout_preparation/fixtures'/case if case==CASES[-1] else V27/'fixtures'/case


def expected_eap(v27_text):
    text=expected_controller(v27_text,'fresh_fine_step')
    patch=bench.read(HERE/'source_whitelist/eap_patch.json')
    assert patch['candidate_sha256']==EXPECTED
    for change in patch['changes']:
        assert text.count(change['old'])==1,change['old']
        text=text.replace(change['old'],change['new'])
    return text


def source_audit(old,new):
    before,after=(bench.read(folder/'build_manifest.json') for folder in (old,new))
    def files(value):
        rows={}
        for name,digest in value['source_sha256'].items():
            p=Path(name)
            if p.suffix.lower()=='.gprj':continue
            assert p.name not in rows
            assert bench.sha(p)==digest
            rows[p.name]=(p,digest)
        return rows
    aa,bb=files(before),files(after)
    assert set(aa)==set(bb)
    changes=[]
    for name in sorted(aa):
        pa,ha=aa[name];pb,hb=bb[name]
        if ha!=hb:
            assert name=='group_controller_v14.v' and hb==EXPECTED
            assert pb.read_text(encoding='utf8')==expected_eap(pa.read_text(encoding='utf8'))
            changes.append(dict(file=name,v27_sha256=ha,candidate_sha256=hb))
    assert len(changes)==1
    # GPR location changes are allowed; source order/type/device/enable and
    # all noncontroller source/constraint payloads remain exact.
    sys.path.insert(0,str(V28/'software'))
    from grid_diff_source_whitelist import project
    a,fa=project(before['authoritative_gpr']);b,fb=project(after['authoritative_gpr'])
    assert a==b and len(fa)==len(fb)
    assert all(x['name']==y['name'] and (x['name']=='group_controller_v14.v' or x['sha256']==y['sha256']) for x,y in zip(fa,fb))
    identity=control.read_identity(new)
    assert identity['status']=='PASS'
    return dict(status='PASS',changed_modules=changes,unchanged_source_rom_count=len(aa)-1,
                gpr_semantics_match=True,source_identity=identity)


def baseline(case,db,x,names):
    folder=(HERE/'baseline_results'/case) if case in ('quiet_high',CASES[-1]) else V28/'software/comparison_1024/results'/case
    name='conventional1024_fixed'+str(db)+'_dtft'
    path=folder/(name+'.npz');decisions=bench.read(folder/(name+'_decisions.json'))
    assert decisions['config']['max_centers']==4 and decisions['config']['threshold_db']==db
    with np.load(path) as z:
        assert np.array_equal(z['input'],x) and z['component_names'].tolist()==names
        result=dict(output=z['output'].copy(),components=z['component_output_counts'].copy(),
                    banks=z['banks_q16'].copy(),trajectory=z['trajectory'].copy(),saturations=0)
    detail=bench.read(folder/(name+'_bank_diagnostics.json'))
    assert not detail['guard_failures']
    return result,decisions,dict(archive=str(path),sha256=bench.sha(path),
        decisions_sha256=bench.sha(folder/(name+'_decisions.json')),config=decisions['config'],guard_failures=0)


def score(meta,names,rows,result,decisions,method,clocks_in,clocks_out,actual_clocks):
    stat,windows=bench.score(meta,names,rows,result,decisions,method,'same_q15_fir')
    stat.update(actual.settled_diagnostic(meta,rows,names,result))
    stat.update(control.false_active_diagnostic(meta,result))
    stat['response_events']=actual.event_responses(meta,windows,clocks_in,clocks_out)
    for event in stat['response_events']:
        event['timing']='Actual complete RTL output clocks at requested50MHz; not board measurement' if actual_clocks else 'Zero-service software timestamp assigned to same input clock; not1024 RTL output timing'
    primary=np.zeros(meta['samples'],bool);a,b=meta['primary_output_sample_range'];primary[a:b]=True
    stat['primary_per_jammer_attenuation_db']=evaluate_q15.attenuation(meta,names,rows,result,primary)
    for row in windows:
        row['evidence_available_clock']=int(clocks_out[row['end_sample']-1])
    return stat,windows


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cases',nargs='*',default=list(CASES))
    parser.add_argument('--suffix',default='eap_actual');parser.add_argument('--holdout-reference-suffix',default='v27_actual')
    parser.add_argument('--reference-map',type=Path);parser.add_argument('--output',type=Path,default=HERE/'results');args=parser.parse_args()
    assert set(args.cases)<=set(CASES)
    refs=bench.read(args.reference_map) if args.reference_map else {}
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    summaries,windows,dynamic,pairs,checks,sources,snapshot_checks,observations,pending=[],[],[],[],[],[],[],[],[]
    for case in args.cases:
        old=Path(refs[case]) if case in refs else ((ROOT/'rtl_validation/results'/(case+'_'+args.holdout_reference_suffix)) if case==CASES[-1] else V27/'rtl_validation/results'/(case+'_adopted_final'))
        new=ROOT/'rtl_validation/results'/((case if case!='quiet_high' else 'quiet_high_c4')+'_'+args.suffix)
        if not all((folder/'verification.json').exists() for folder in (old,new)):
            pending.append(dict(case=case,status='ACTUAL_NOT_YET_EXPORTED',v27=str(old),candidate=str(new)));continue
        case_stats={};common=None
        for method,folder in [('actual_v27_q15',old),('actual_eap_q15',new)]:
            meta,x,names,rows,result,data,check=actual.load_actual(folder,fixture(case))
            assert meta['capacity']==4
            meta=dict(meta,name=case)
            stat,win=score(meta,names,rows,result,dict(updates=[]),method,data['input_clocks'],data['output_clocks'],True)
            stat.update(control.commit_pressure(folder));stat.update(kind='COMPLETE_CLOSED_LOOP_RTL_SIMULATION',board_measured=False,export=str(folder))
            snapshots=actual.validate_basis_snapshots(folder,4);assert snapshots['status']=='PASS'
            snapshot_checks.append(dict(case=case,method=method,**snapshots));checks.append(dict(case=case,method=method,**check))
            if method=='actual_eap_q15':sources.append(dict(case=case,**source_audit(old,new)))
            summaries.append(stat);windows+=win;case_stats[method]=stat
            dynamic+=fixed_comparison.dynamic_energy(meta,names,rows,result,method)
            if common is None:common=(meta,x,names,rows,data)
            else:assert np.array_equal(common[1],x) and np.array_equal(common[4]['input_clocks'],data['input_clocks'])
        meta,x,names,rows,data=common
        for db in (21,18):
            method='conventional1024_fixed'+str(db)+'_dtft'
            result,decisions,identity=baseline(case,db,x,names)
            stat,win=score(meta,names,rows,result,decisions,method,data['input_clocks'],data['input_clocks'],False)
            stat.update(fixed_comparison.pressure(decisions['updates']))
            stat.update(kind='FINITE_ARITHMETIC_SOFTWARE',zero_service=True,board_measured=False,baseline_identity=identity)
            summaries.append(stat);windows+=win;case_stats[method]=stat
            dynamic+=fixed_comparison.dynamic_energy(meta,names,rows,result,method)
        candidate=case_stats['actual_eap_q15']
        for method in ('actual_v27_q15','conventional1024_fixed21_dtft','conventional1024_fixed18_dtft'):
            other=case_stats[method]
            pairs.append(dict(case=case,reference=method,eap_primary_nrmse_percent=candidate['primary_nrmse_percent'],
                reference_primary_nrmse_percent=other['primary_nrmse_percent'],
                eap_error_reduction_relative_percent=100*(1-candidate['primary_nrmse_percent']/other['primary_nrmse_percent']),
                eap_settled_nrmse_percent=candidate['settled_diagnostic_nrmse_percent'],reference_settled_nrmse_percent=other['settled_diagnostic_nrmse_percent'],
                eap_target_nrmse_percent=candidate['primary_target_nrmse_percent'],reference_target_nrmse_percent=other['primary_target_nrmse_percent'],
                eap_full_worst_probe_db=candidate['worst_probe_amplitude_abs_change_db'],reference_full_worst_probe_db=other['worst_probe_amplitude_abs_change_db'],
                eap_full_probe_exceeds1db=candidate['worst_probe_amplitude_abs_change_db']>1,
                eap_commits=candidate['commits'],reference_updates_or_commits=other.get('commits',other.get('configuration_updates'))))
        if (old/'observations.csv').exists() and (new/'observations.csv').exists():
            observations.append(dict(case=case,**control.paired_main_observations(old,new)))
        print(json.dumps(dict(case=case,pairs=[p for p in pairs if p['case']==case]),indent=2),flush=True)
    result=dict(status='COMPLETE_FROZEN_C4_SUITE_AND_HOLDOUT' if not pending and set(args.cases)==set(CASES) else 'PARTIAL',
        candidate='EAP-C4',source_controller_sha256=EXPECTED,summaries=summaries,comparisons=pairs,
        verification=checks,source_identity=sources,q15_snapshot_checks=snapshot_checks,main_observation_checks=observations,
        pending=pending,completed_cases=sorted(set(s['case'] for s in summaries)),requested_cases=args.cases,
        plan_sha256=bench.sha(HERE/'plan.json'),source_policy_sha256=bench.sha(HERE/'source_whitelist/eap_patch.json'),
        scoring_source_sha256={str(p):bench.sha(p) for p in (Path(__file__),HERE/'epoch_permission.py',V27/'score_actual_q15_rtl.py',V27/'software/run_benchmark.py')},
        limits=['Actual closed-loop RTL at requested50MHz, not board measured. No P&R in this task.',
            'Full primary, stable, target, each jammer and all full-period probe failures reported separately.',
            'Original20%/three-window recovery is diagnostic;30ms is not a hard requirement.',
            'Conventional1024 is the unchanged fixed software21main/18sensitivity, zero frontend/config waiting; not pure-window causal proof.',
            'Frozen retrospective C4 cases plus one predeclared new holdout; no seed/rule/threshold selection after results.'])
    bench.write(out/'results.json',result);bench.csv_write(out/'summary.csv',summaries);bench.csv_write(out/'paired.csv',pairs)
    bench.csv_write(out/'windows.csv',windows);bench.csv_write(out/'dynamic_energy.csv',dynamic)
    bench.csv_write(out/'responses.csv',[dict(case=s['case'],method=s['method'],**e) for s in summaries for e in s['response_events']])
    print(json.dumps(dict(status=result['status'],pending=pending,results=str(out/'results.json')),indent=2))


if __name__=='__main__':main()

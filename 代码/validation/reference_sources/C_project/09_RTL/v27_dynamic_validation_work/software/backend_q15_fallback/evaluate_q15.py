"""Isolated Q15 coefficient-response backend counterfactual.

Detection, authorized snapshots, actual commit samples and fade trajectories
are frozen from dsp_final. This is not a new RTL implementation or timing run.
The basis, twelve Richardson iterations and final Q16 FIR are unchanged;
only response/weight arithmetic changes Q20 to Q15 with RNE at every step.
"""
from pathlib import Path
from functools import lru_cache
import json, sys
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'software'),str(ROOT/'hardware'),str(ROOT/'software/rowsum_fallback')]
import basis_probe
import basis_audit
import run_benchmark as bench
import score_actual_rtl as actual
import evaluate_rowsum as helpers
from fixed_builder import cosq
from fir_replay import quantized_frequencies,round_div_even

CASES=('c3_single_then_pair','c3_pair_then_single','c4_two_pairs_staggered',
       'c4_second_pair_simultaneous','quiet_high')
ONE=32768

@lru_cache(maxsize=32768)
def response_q15(row,col):
    values=basis_probe.basis_q16(col)
    raw=sum(int(values[t])*cosq((320-t)*row)*(1 if t==320 else 2) for t in range(321))
    return round_div_even(raw,1<<17),raw

def build_q15(frequencies_hz,return_diagnostics=False):
    frequencies=quantized_frequencies(frequencies_hz)
    if len(frequencies)>4:raise ValueError('unsupported_capacity')
    grids=[round(f/3.90625) for f in frequencies];n=len(grids)
    if not n:
        h,_=basis_probe.build_bank_basis([],True)
        info=dict(status='ok',centers_hz=[],G_q15=[],weights_q15=[],row_norm_q15=0,
                  residual_q15=[],maximum_dot_accumulator_abs=0,maximum_response_accumulator_abs=0)
        return (h,info) if return_diagnostics else h
    raw=[[response_q15(i,j) for j in grids] for i in grids]
    g=np.asarray([[v[0] for v in row] for row in raw],np.int64)
    if np.min(g)<-131072 or np.max(g)>131071:raise ArithmeticError('matrix_18bit_overflow')
    target=np.full(n,ONE,np.int64)
    norm=int(np.max(np.sum(abs(np.diag(target)-g),axis=1)))
    if norm>=16384:raise ArithmeticError(f'noncontractive_response row_norm_q15={norm}')
    maximum_dot=0
    def dot(w):
        nonlocal maximum_dot
        result=[]
        for row in g:
            accumulator=0
            for a,b in zip(row,w):
                accumulator+=int(a)*int(b)
                maximum_dot=max(maximum_dot,abs(accumulator))
                if not -(1<<32)<=accumulator<(1<<32):
                    raise ArithmeticError('dot_accumulator_33bit_overflow')
            result.append(round_div_even(accumulator,1<<15))
        return np.asarray(result,np.int64)
    weights=target.copy()
    for _ in range(12):
        weights+=target-dot(weights)
        if np.max(abs(weights))>65536:raise ArithmeticError('weight_overflow')
    residual=target-dot(weights)
    if np.max(abs(residual))>2:
        raise ArithmeticError(f'unconverged_response residual_q15={residual.tolist()}')
    bases=np.asarray([basis_probe.basis_q16(grid) for grid in grids],np.int64)
    half=np.asarray([-sum(round_div_even(int(bases[j,t])*int(weights[j]),1<<15)
                         for j in range(n)) for t in range(321)],np.int64)
    half[320]+=65536
    if np.min(half)<-131072 or np.max(half)>131071:raise ArithmeticError('coefficient_overflow')
    h=np.r_[half,half[-2::-1]]
    info=dict(status='ok',centers_hz=frequencies,G_q15=g.tolist(),weights_q15=weights.tolist(),
        row_norm_q15=norm,residual_q15=residual.tolist(),maximum_dot_accumulator_abs=maximum_dot,
        maximum_response_accumulator_abs=max(abs(v[1]) for row in raw for v in row),
        response_accumulator_note='Q32 basis/cosine response accumulation retains existing wide precision; only rounded G/dot/weights use Q15. The 33-bit claim is for joint-weight dot accumulators, not raw response sums.')
    return (h,info) if return_diagnostics else h

def attenuation(meta,names,rows,result,mask):
    if not meta['events']:return {}
    return helpers.component_attenuation(meta,names,rows,result,mask)

def one_case(case):
    folder=ROOT/'rtl_validation/results'/(case+'_dsp_final');fixture=ROOT/'fixtures'/case
    meta,x,names,rows,reference,data,check=actual.load_actual(folder,fixture)
    mappings=helpers.decode_snapshots(folder,meta['capacity'])
    records=[];banks=[];failures=[]
    for index,bank in enumerate(reference['banks']):
        possibilities=list(dict.fromkeys(mappings[bank.tobytes()]))
        try:
            built=[build_q15(fs,True) for fs in possibilities]
            assert all(np.array_equal(built[0][0],h) for h,info in built),'source bank ambiguously maps to different Q15 banks'
            h,info=built[0];banks.append(h)
            records.append(dict(bank=index,estimated_centers_hz=possibilities[0],diagnostics=info,
                max_coefficient_difference_lsb=int(np.max(abs(h-bank)))))
        except (ArithmeticError,ValueError,OverflowError) as exc:
            failures.append(dict(bank=index,estimated_centers_hz=possibilities[0],error=str(exc)))
    destination=HERE/case;destination.mkdir(exist_ok=True)
    if failures:
        result=dict(case=case,status='FAIL_GUARD_NO_REPLAY',guard_failures=failures,banks=records)
        bench.write(destination/'results.json',result)
        return result
    replay=helpers.frozen_replay(rows,x,np.asarray(banks),reference['trajectory'])
    primary=np.zeros(len(x),bool);a,b=meta['primary_output_sample_range'];primary[a:b]=True
    settled=helpers.settled_mask(meta)
    summaries=[];windows=[]
    for label,value in [('actual_q20_reference',reference),('frozen_q15_counterfactual',replay)]:
        stat,win=bench.score(meta,names,rows,value,dict(updates=[]),label,'frozen_actual_slots')
        stat.update(actual.settled_diagnostic(meta,rows,names,value))
        stat['primary_per_jammer_attenuation_db']=attenuation(meta,names,rows,value,primary)
        stat['settled_per_jammer_attenuation_db']=attenuation(meta,names,rows,value,settled)
        summaries.append(stat);windows+=win
    old,new=summaries
    paired=dict(case=case,status='PASS_GUARDS',reference_primary_nrmse_percent=old['primary_nrmse_percent'],
        q15_primary_nrmse_percent=new['primary_nrmse_percent'],
        primary_relative_increase_percent=100*(new['primary_nrmse_percent']/old['primary_nrmse_percent']-1),
        reference_stable_nrmse_percent=old['settled_diagnostic_nrmse_percent'],q15_stable_nrmse_percent=new['settled_diagnostic_nrmse_percent'],
        stable_increase_percentage_points=new['settled_diagnostic_nrmse_percent']-old['settled_diagnostic_nrmse_percent'] if meta['events'] else None,
        reference_stable_target_nrmse_percent=old['settled_target_nrmse_percent'],q15_stable_target_nrmse_percent=new['settled_target_nrmse_percent'],
        reference_probe_worst8ms_db=old['settled_worst8ms_midpoint_abs_change_db'],q15_probe_worst8ms_db=new['settled_worst8ms_midpoint_abs_change_db'],
        reference_primary_attenuation_db=old['primary_per_jammer_attenuation_db'],q15_primary_attenuation_db=new['primary_per_jammer_attenuation_db'],
        reference_stable_attenuation_db=old['settled_per_jammer_attenuation_db'],q15_stable_attenuation_db=new['settled_per_jammer_attenuation_db'],
        banks=len(records),guard_failures=[],max_coefficient_difference_lsb=max(r['max_coefficient_difference_lsb'] for r in records),
        maximum_dot_accumulator_abs=max(r['diagnostics']['maximum_dot_accumulator_abs'] for r in records),
        maximum_raw_response_accumulator_abs=max(r['diagnostics']['maximum_response_accumulator_abs'] for r in records),
        max_output_difference_counts=int(np.max(abs(replay['output'].astype(np.int32)-reference['output'].astype(np.int32)))),saturations=new['saturations'])
    bench.write(destination/'results.json',dict(summaries=summaries,paired=paired,banks=records,
        reference_verification=check,reference_snapshot_sha256=bench.sha(folder/'cfg_snapshots.csv'),
        scope='OFFLINE_COUNTERFACTUAL_FROZEN_ACTUAL_DSP_SLOTS: not new RTL; actual detected centers only; clean components used only by offline scoring'))
    bench.csv_write(destination/'windows.csv',windows)
    np.savez_compressed(destination/'counterfactual.npz',output=replay['output'],component_names=np.array(names),
        component_output_counts=replay['components'],banks_q16=replay['banks'],trajectory=replay['trajectory'])
    return paired

def development():
    plan=bench.read(ROOT/'software/basis_audit_results/plan.json')
    cases=[c for c in plan['cases'] if all(abs(c['centers_hz'][j]-c['centers_hz'][i]-125)<1e-8 for i,j in c['pairs'])]
    assert len(cases)==100
    records=[];failures=[]
    for case in cases:
        fs=case['centers_hz'];old,oi=basis_probe.build_bank_basis(fs,True)
        try:
            new,ni=build_q15(fs,True)
            records.append(dict(case=case['name'],capacity=case['capacity'],centers_hz=fs,
                q20=basis_audit.diagnostics(old,fs,case['pairs']),q15=basis_audit.diagnostics(new,fs,case['pairs']),
                q20_builder=oi,q15_builder=ni,max_coefficient_difference_lsb=int(np.max(abs(new-old)))))
        except (ArithmeticError,ValueError,OverflowError) as exc:failures.append(dict(case=case['name'],centers_hz=fs,error=str(exc)))
    summary=dict(cases=len(cases),passed=len(records),guard_failures=len(failures),
        minimum_q15_center_attenuation_db=min(v for r in records for v in r['q15']['center_attenuation_db']) if records else None,
        minimum_q15_supplied_offgrid_attenuation_db=min(v for r in records for v in r['q15']['supplied_offgrid_attenuation_db']) if records else None,
        maximum_q15_midpoint_abs_change_db=max(abs(v) for r in records for v in r['q15']['midpoint_amplitude_change_db']) if records else None,
        max_coefficient_difference_lsb=max((r['max_coefficient_difference_lsb'] for r in records),default=0),
        maximum_dot_accumulator_abs=max((r['q15_builder']['maximum_dot_accumulator_abs'] for r in records),default=0),
        maximum_raw_response_accumulator_abs=max((r['q15_builder']['maximum_response_accumulator_abs'] for r in records),default=0),
        maximum_residual_q15=max((abs(v) for r in records for v in r['q15_builder']['residual_q15']),default=0),
        original_plan_sha256=bench.sha(ROOT/'software/basis_audit_results/plan.json'))
    bench.write(HERE/'development_results.json',dict(summary=summary,records=records,failures=failures))
    return summary

def main():
    plan=dict(arithmetic='G=RNE(raw response/2^17); dot/product shifts15; ONE32768; 12 unchanged iterations',
        guards='row_norm<16384, abs(weight)<=65536, residual<=2 Q15, signed33 dot accumulator, signed18 finalQ16 FIR',
        cases=list(CASES),source_suffix='dsp_final',development='existing100exact125 coefficient development cases; not fresh independent test',
        predeclared_screen=dict(max_primary_relative_increase_percent=1.,max_stable_increase_percentage_points=.5,
            max_settled_probe_db=1.,min_dev_center_attenuation_db=40.,max_dev_midpoint_db=1.),
        scope='Isolated numerical backend fallback. Detector remains unchanged. Frozen actual slots do not measure Q15 hardware timing/resources.',
        response_accumulator='Existing wide Q32 response sum retained; 33-bit reduction applies only to G-times-weight accumulators.')
    bench.write(HERE/'experiment_plan.json',plan)
    dynamic=[one_case(c) for c in CASES];dev=development()
    dynamic_screen=all(r['status']=='PASS_GUARDS' and r['primary_relative_increase_percent']<=1 and
        (r['case']=='quiet_high' or (r['stable_increase_percentage_points']<=.5 and r['q15_probe_worst8ms_db']<=1 and
         min(r['q15_stable_attenuation_db'].values())>=20)) and r['saturations']==0 for r in dynamic)
    screen=bool(dynamic_screen and dev['guard_failures']==0 and dev['minimum_q15_center_attenuation_db']>=40 and dev['maximum_q15_midpoint_abs_change_db']<=1)
    result=dict(scope='ISOLATED_Q15_FALLBACK_ONLY; frozen actual slots are not a new actual RTL run',dynamic=dynamic,development=dev,
        predeclared_screen_pass=screen,recommendation='Numerical development screen PASS; no RTL area/cycle claim, needs explicit implementation and independent validation.' if screen else 'FAIL: do not adopt this Q15 fallback unchanged.',
        plan_sha256=bench.sha(HERE/'experiment_plan.json'),script_sha256=bench.sha(Path(__file__)),
        sources_sha256={str(p):bench.sha(p) for p in [ROOT/'basis_probe.py',ROOT/'software/rowsum_fallback/evaluate_rowsum.py',ROOT/'score_actual_rtl.py']})
    bench.write(HERE/'results.json',result);bench.csv_write(HERE/'paired.csv',dynamic)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

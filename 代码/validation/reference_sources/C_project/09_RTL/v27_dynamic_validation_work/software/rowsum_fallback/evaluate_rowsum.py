"""Isolated row-sum fallback; frozen actual slots are NOT a new RTL run.

No production, final-packed or report files are modified. The approximate
weight rule is w_i=1/sum_jG_ij, with exact integerRNE division. Detection and
the actual old/new-bank/alpha timeline remain frozen. Its future hardware cost
and service timing are unknown.
"""
from pathlib import Path
from functools import lru_cache
import json, sys
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'software'),str(ROOT/'hardware')]
import basis_probe
import basis_audit
import run_benchmark as bench
import score_actual_rtl as actual
from fir_replay import quantized_frequencies,round_div_even

CASES=('c3_single_then_pair','c3_pair_then_single','c4_two_pairs_staggered','c4_second_pair_simultaneous')

def build_rowsum(frequencies_hz,return_diagnostics=False):
    frequencies=quantized_frequencies(frequencies_hz)
    if len(frequencies)>4:raise ValueError('unsupported_capacity')
    grids=[round(f/3.90625) for f in frequencies];n=len(grids)
    if not n:
        h,info=basis_probe.build_bank_basis([],True)
        info.update(method='rowsum',existing_residual_guard_pass=True)
        return (h,info) if return_diagnostics else h
    g=np.asarray([[basis_probe.response_q20(i,j) for j in grids] for i in grids],np.int64)
    target=np.full(n,1<<20,np.int64)
    norm=int(np.max(np.sum(abs(np.diag(target)-g),axis=1)))
    if norm>=1<<19:raise ArithmeticError('noncontractive_response')
    sums=[sum(int(v) for v in row) for row in g]
    if min(sums)<=0:raise ArithmeticError('nonpositive_rowsum')
    weights=[round_div_even(1<<40,v) for v in sums]
    if max(abs(v) for v in weights)>(2<<20):raise ArithmeticError('weight_overflow')
    residual=[(1<<20)-round_div_even(sum(int(v)*w for v,w in zip(row,weights)),1<<20) for row in g]
    bases=np.asarray([basis_probe.basis_q16(grid) for grid in grids])
    half=np.asarray([-sum(round_div_even(int(bases[j,t])*weights[j],1<<20) for j in range(n)) for t in range(321)],np.int64)
    half[320]+=65536
    if np.min(half)<-131072 or np.max(half)>131071:raise ArithmeticError('coefficient_overflow')
    h=np.r_[half,half[-2::-1]]
    info=dict(method='rowsum',centers_hz=frequencies,weights_q20=weights,G_q20=g.tolist(),
        row_sums_q20=sums,row_norm_q20=norm,residual_q20=residual,
        existing_residual_guard_pass=bool(max(abs(v) for v in residual)<=64),
        residual_guard_note='Residual is measured but NOT used to omit this offline counterfactual; failure means existingbackendwouldreject and adoption needs explicitguardpolicydecision.',
        status='approximate_bank_built')
    return (h,info) if return_diagnostics else h

def frozen_replay(rows,mixed,banks,trajectory):
    n=len(mixed);numerator=np.zeros(n,np.int64);component=np.zeros_like(rows)
    pm=np.pad(mixed.astype(np.int64),(640,0));pr=np.pad(rows,((0,0),(640,0)))
    split=np.r_[0,np.flatnonzero(np.any(trajectory[1:,:2]!=trajectory[:-1,:2],axis=1))+1,n]
    for a,b in zip(split[:-1],split[1:]):
        old,new=trajectory[a,:2];weights={int(old):32-trajectory[a:b,2]}
        weights[int(new)]=weights.get(int(new),0)+trajectory[a:b,2]
        for index,weight in weights.items():
            if not np.any(weight):continue
            h=banks[index];numerator[a:b]+=np.convolve(pm[a:b+640],h,'valid')*weight
            for row in range(len(rows)):
                component[row,a:b]+=np.convolve(pr[row,a:b+640],h/65536,'valid')*weight/32
    output,sat=bench.fir_replay._rne_clip_array(numerator)
    assert np.max(abs(component.sum(axis=0)-numerator/2**21))<1e-7
    return dict(output=output,components=component,banks=banks,trajectory=trajectory,saturations=sat)

def decode_snapshots(folder,capacity):
    result={bench.fir_replay.build_bank([]).tobytes():[()]}
    for row in actual.table(folder/'cfg_snapshots.csv'):
        word=int(row['frequencies_hex'],16);mask=int(row['mask'])
        fs=tuple(((word>>(32*i))&0xffffffff)*32000/(2**32) for i in range(capacity) if mask&(1<<i))
        h=basis_probe.build_bank_basis(fs)
        result.setdefault(h.tobytes(),[]).append(fs)
    return result

def component_attenuation(meta,names,rows,result,mask):
    reference=bench.align(rows);records={}
    for center in range(len(meta['frequencies_hz'])):
        k=names.index('interference_'+str(center));r=reference[k,mask];y=result['components'][k,mask]
        records[str(center)]=float(10*np.log10(max(float(r@r),1e-30)/max(float(y@y),1e-30)))
    return records

def settled_mask(meta):
    n=meta['samples'];mask=np.zeros(n,bool)
    changes=sorted(set(v for e in meta['events'] for v in (e['start_sample'],e['end_sample'])))
    for a,b in zip(changes,changes[1:]+[n]):
        if any(e['start_sample']<=a<e['end_sample'] for e in meta['events']):
            first,last=a+1280+320,min(n,b)
            if first<last:mask[first:last]=True
    return mask

def dynamic_case(case):
    folder=ROOT/'rtl_validation/results'/(case+'_basis_packed');fixture=ROOT/'fixtures'/case
    meta,x,names,rows,original,data,check=actual.load_actual(folder,fixture)
    mappings=decode_snapshots(folder,meta['capacity']);approximations=[];bank_records=[]
    for index,bank in enumerate(original['banks']):
        if bank.tobytes() not in mappings:raise AssertionError(f'unmapped actualbank{index}')
        possibilities=list(dict.fromkeys(mappings[bank.tobytes()]))
        built=[build_rowsum(fs,True) for fs in possibilities]
        assert all(np.array_equal(built[0][0],h) for h,info in built),'ambiguous sourcebankmaps to differentrowbanks'
        h,info=built[0];approximations.append(h)
        bank_records.append(dict(bank=index,estimated_centers_hz=possibilities[0],diagnostics=info,
            max_coefficient_difference_lsb=int(np.max(abs(h-bank)))))
    result=frozen_replay(rows,x,np.asarray(approximations),original['trajectory'])
    primary=np.zeros(len(x),bool);a,b=meta['primary_output_sample_range'];primary[a:b]=True
    stable=settled_mask(meta);summaries=[];windows=[]
    for label,r in [('actual_packed_reference',original),('frozen_rowsum_counterfactual',result)]:
        stat,win=bench.score(meta,names,rows,r,dict(updates=[]),label,'frozen_actual_slots')
        stat.update(actual.settled_diagnostic(meta,rows,names,r))
        stat['primary_per_jammer_attenuation_db']=component_attenuation(meta,names,rows,r,primary)
        stat['settled_per_jammer_attenuation_db']=component_attenuation(meta,names,rows,r,stable)
        summaries.append(stat);windows+=win
    old,new=summaries
    paired=dict(case=case,reference_primary_nrmse_percent=old['primary_nrmse_percent'],rowsum_primary_nrmse_percent=new['primary_nrmse_percent'],
        primary_relative_increase_percent=100*(new['primary_nrmse_percent']/old['primary_nrmse_percent']-1),
        reference_stable_nrmse_percent=old['settled_diagnostic_nrmse_percent'],rowsum_stable_nrmse_percent=new['settled_diagnostic_nrmse_percent'],
        stable_increase_percentage_points=new['settled_diagnostic_nrmse_percent']-old['settled_diagnostic_nrmse_percent'],
        reference_stable_target_nrmse_percent=old['settled_target_nrmse_percent'],rowsum_stable_target_nrmse_percent=new['settled_target_nrmse_percent'],
        reference_probe_worst8ms_db=old['settled_worst8ms_midpoint_abs_change_db'],rowsum_probe_worst8ms_db=new['settled_worst8ms_midpoint_abs_change_db'],
        reference_primary_attenuation_db=old['primary_per_jammer_attenuation_db'],rowsum_primary_attenuation_db=new['primary_per_jammer_attenuation_db'],
        reference_stable_attenuation_db=old['settled_per_jammer_attenuation_db'],rowsum_stable_attenuation_db=new['settled_per_jammer_attenuation_db'],
        banks=len(bank_records),banks_failing_old64lsb_residual_guard=sum(not r['diagnostics']['existing_residual_guard_pass'] for r in bank_records),
        maximum_weight_residual_q20=max((abs(v) for r in bank_records for v in r['diagnostics']['residual_q20']),default=0),
        max_output_difference_counts=int(np.max(abs(result['output'].astype(np.int32)-original['output'].astype(np.int32)))),
        saturations=new['saturations'])
    out=HERE/case;out.mkdir(exist_ok=True)
    bench.write(out/'results.json',dict(summaries=summaries,paired=paired,banks=bank_records,
        reference_verification=check,reference_snapshot_sha256=bench.sha(folder/'cfg_snapshots.csv'),
        scope='OFFLINE_COUNTERFACTUAL_FROZEN_ACTUAL_SLOTS: not newRTL; centers only fromactualconfigsnapshots; sourcecomponentsonlyoffline'))
    bench.csv_write(out/'windows.csv',windows)
    np.savez_compressed(out/'counterfactual.npz',output=result['output'],component_names=np.array(names),
        component_output_counts=result['components'],banks_q16=result['banks'],trajectory=result['trajectory'])
    return paired

def development():
    plan=bench.read(ROOT/'software/basis_audit_results/plan.json')
    cases=[c for c in plan['cases'] if all(abs(c['centers_hz'][j]-c['centers_hz'][i]-125)<1e-8 for i,j in c['pairs'])]
    assert len(cases)==100
    records=[]
    for case in cases:
        fs=case['centers_hz'];old,oi=basis_probe.build_bank_basis(fs,True);new,ni=build_rowsum(fs,True)
        od=basis_audit.diagnostics(old,fs,case['pairs']);nd=basis_audit.diagnostics(new,fs,case['pairs'])
        records.append(dict(case=case['name'],capacity=case['capacity'],centers_hz=fs,
            old=od,rowsum=nd,old_builder=oi,rowsum_builder=ni,
            max_coefficient_difference_lsb=int(np.max(abs(old-new)))))
    summary=dict(cases=len(records),minimum_rowsum_center_attenuation_db=min(v for r in records for v in r['rowsum']['center_attenuation_db']),
        minimum_rowsum_supplied_offgrid_attenuation_db=min(v for r in records for v in r['rowsum']['supplied_offgrid_attenuation_db']),
        maximum_rowsum_midpoint_abs_change_db=max(abs(v) for r in records for v in r['rowsum']['midpoint_amplitude_change_db']),
        cases_failing_old64lsb_residual_guard=sum(not r['rowsum_builder']['existing_residual_guard_pass'] for r in records),
        maximum_residual_q20=max(abs(v) for r in records for v in r['rowsum_builder']['residual_q20']),
        maximum_coefficient_difference_lsb=max(r['max_coefficient_difference_lsb'] for r in records),
        exact125_original_plan_sha256=bench.sha(ROOT/'software/basis_audit_results/plan.json'))
    bench.write(HERE/'development_results.json',dict(summary=summary,records=records))
    return summary

def main():
    plan=dict(method='w_Q20=RNE(2^40/sumG_Q20)',cases=list(CASES),development='existing100exact125coefficientcases, not freshindependentperformance',
        admission='unchanged detector/actualsnapshots/queue/banktrajectory/alpha; onlyofflinebankreplacement',
        numerical_guards='retain rownorm<.5,weight<=2,18bit coefficients; old64LSBresidualrecorded,notusedtohideofflinecases',
        predeclared_screen=dict(maximum_primary_relative_increase_percent=1.,maximum_stable_increase_percentage_points=.5,
            maximum_stable_probe_db=1.,minimum_dev_center_attenuation_db=40.,maximum_dev_midpoint_db=1.),
        cost='No RTL, no resource saving claim; old actual service slots are frozen, not row-sum implementation latency')
    bench.write(HERE/'experiment_plan.json',plan)
    dynamic=[dynamic_case(c) for c in CASES];dev=development()
    screen=all(r['primary_relative_increase_percent']<=1 and r['stable_increase_percentage_points']<=.5
               and r['rowsum_probe_worst8ms_db']<=1 and min(r['rowsum_stable_attenuation_db'].values())>=20 for r in dynamic)
    screen=bool(screen and dev['minimum_rowsum_center_attenuation_db']>=40 and dev['maximum_rowsum_midpoint_abs_change_db']<=1)
    guard_failures=sum(r['banks_failing_old64lsb_residual_guard'] for r in dynamic)+dev['cases_failing_old64lsb_residual_guard']
    result=dict(scope='ISOLATED_FALLBACK_ONLY; frozen real slots are not anewactualRTLrun',dynamic=dynamic,development=dev,
        predeclared_performance_screen_pass=screen,old_residual_guard_failures=guard_failures,
        drop_in_under_existing_residual_guard=guard_failures==0,
        recommendation='Performance passes development screen; resource/cycles unmeasured and residualguardchangesexplicitlyrequired iffailurespresent.' if screen else 'FAIL_PERFORMANCE_SCREEN: stop approximatefallback; do not integrate.',
        plan_sha256=bench.sha(HERE/'experiment_plan.json'),script_sha256=bench.sha(Path(__file__)),
        source_sha256={str(p):bench.sha(p) for p in [ROOT/'basis_probe.py',ROOT/'hardware/fixed_builder.py',ROOT/'score_actual_rtl.py']})
    bench.write(HERE/'results.json',result);bench.csv_write(HERE/'paired.csv',dynamic)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

"""Second and final bounded retirement diagnostic, not a complete new RTL run."""
from pathlib import Path
import argparse
import json
import os
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
import numpy as np
import retire64_counterfactual as original
HERE=Path(__file__).resolve().parent
bench,old,BUILD=original.bench,original.old,original.BUILD


def config_updates(folder,clocks):
    previous={};result=[]
    for row in original.table(folder/'cfg_snapshots.csv'):
        members={slot:original.token(row,slot) for slot in range(4) if int(row['mask'])&(1<<slot)}
        pure=bool(len(members)<len(previous) and all(previous.get(slot)==value for slot,value in members.items()))
        result.append(dict(sample=int(np.searchsorted(clocks,int(row['clock']))),
            frequencies_hz=[item[2]*32000/(1<<32) for _,item in sorted(members.items())],
            pure_removal=pure,removed_slots=sorted(set(previous)-set(members)),
            members=members,original_commit_clock=int(row['clock'])))
        previous=members
    return result


def replay(rows,mixed,updates,stretch):
    n=len(mixed);banks=[BUILD([])];ids={banks[0].tobytes():0}
    current=0;target=None;pending=None;alpha=0;repeat=1;subtick=0;j=0
    trajectory=np.zeros((n,3),np.int32)
    stats=dict(extended_retirement_started=0,accelerated_by_nonremoval=0,
               maximum_pending_pure_removal_wait_samples=0)
    trace=[]
    for sample in range(n):
        while j<len(updates) and updates[j]['sample']<=sample:
            update=updates[j];h=BUILD(update['frequencies_hz']);key=h.tobytes()
            if key not in ids:ids[key]=len(banks);banks.append(h)
            bank=ids[key]
            if stretch and target is not None and repeat==3 and not update['pure_removal']:
                trace.append(dict(sample=sample,event='new_nonremoval_priority_accelerates_retirement',
                    previous_alpha=alpha,completed_target_bank=target,incoming_bank=bank))
                current,target,alpha,subtick=target,None,0,0
                stats['accelerated_by_nonremoval']+=1
            pending=(bank,3 if stretch and update['pure_removal'] else 1,sample)
            j+=1
        if target is None and pending is not None:
            bank,newrepeat,arrived=pending
            if bank!=current:
                target=bank;alpha=1;repeat=newrepeat;subtick=0
                if repeat==3:
                    stats['extended_retirement_started']+=1
                    stats['maximum_pending_pure_removal_wait_samples']=max(
                        stats['maximum_pending_pure_removal_wait_samples'],sample-arrived)
            pending=None
        if target is None:trajectory[sample]=(current,current,0)
        else:
            trajectory[sample]=(current,target,alpha);subtick+=1
            if subtick==repeat:
                subtick=0
                if alpha==32:current,target,alpha=target,None,0
                else:alpha+=1
    numerator=np.zeros(n,np.int64);components=np.zeros_like(rows)
    padded=np.pad(rows,((0,0),(640,0)));pm=np.pad(mixed.astype(np.int64),(640,0))
    split=np.r_[0,np.flatnonzero(np.any(trajectory[1:,:2]!=trajectory[:-1,:2],axis=1))+1,n]
    for a,b in zip(split[:-1],split[1:]):
        first,second=trajectory[a,:2];weights={int(first):32-trajectory[a:b,2]}
        weights[int(second)]=weights.get(int(second),0)+trajectory[a:b,2]
        for bank,weight in weights.items():
            if not np.any(weight):continue
            h=banks[bank];numerator[a:b]+=np.convolve(pm[a:b+640],h,'valid')*weight
            for lane in range(len(rows)):
                components[lane,a:b]+=np.convolve(padded[lane,a:b+640],h/65536,'valid')*weight/32
    output,saturation=bench.fir_replay._rne_clip_array(numerator)
    return dict(output=output,components=components,banks=np.asarray(banks),trajectory=trajectory,saturations=saturation),dict(stats=stats,events=trace)


def baseline(case,db,x,names):
    if case!='c4_temporal_gap2ms_dev':return old.baseline(case,db,x,names)[:2]
    path=HERE/'baseline_results'/case/('conventional1024_fixed'+str(db)+'_dtft')
    decisions=bench.read(path.with_name(path.name+'_decisions.json'))
    detail=bench.read(path.with_name(path.name+'_bank_diagnostics.json'));assert not detail['guard_failures']
    with np.load(path.with_suffix('.npz')) as z:
        assert np.array_equal(z['input'],x) and z['component_names'].tolist()==names
        result=dict(output=z['output'],components=z['component_output_counts'],banks=z['banks_q16'],trajectory=z['trajectory'],saturations=0)
    return result,decisions


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cases',nargs='*',default=list(original.CASES));args=parser.parse_args()
    assert set(args.cases)<=set(original.CASES)
    policy=bench.read(HERE/'retire96_policy.json');assert policy['name']=='RETIRING_ONLY96FADE'
    out=HERE/'retire96_diagnostic';out.mkdir(exist_ok=True)
    summaries=[];windows=[];energy=[];checks=[];pending=[]
    for case in args.cases:
        folder=original.export(case)
        if not (folder/'verification.json').exists():pending.append(case);continue
        meta,x,names,rows,current,data,check=old.actual.load_actual(folder,original.fixture(case))
        updates=config_updates(folder,data['input_clocks'])
        before,_=replay(rows,x,updates,False)
        delta=int(np.max(abs(before['output'].astype(np.int32)-current['output'].astype(np.int32))))
        assert delta==0
        candidate,trace=replay(rows,x,updates,True)
        target=out/case;target.mkdir(exist_ok=True)
        bench.write(target/'updates.json',dict(updates=updates,trace=trace))
        np.savez_compressed(target/'candidate.npz',input=x,output=candidate['output'],component_names=names,
            component_output_counts=candidate['components'],banks_q16=candidate['banks'],trajectory=candidate['trajectory'])
        checks.append(dict(case=case,base_replay_max_lsb=delta,actual_verification=check,trace=trace))
        arms={'actual_v27_q15':(current,dict(updates=[])),
              'retiring_only96fade_fixedtrajectory':(candidate,dict(updates=updates))}
        for db in (21,18):arms['conventional1024_fixed'+str(db)+'_dtft']=baseline(case,db,x,names)
        for method,(result,decisions) in arms.items():
            stat,win=bench.score(meta,names,rows,result,decisions,method,'same_q15_fir')
            stat.update(old.actual.settled_diagnostic(meta,rows,names,result))
            stat.update(old.control.false_active_diagnostic(meta,result))
            mask=np.zeros(len(x),bool);a,b=meta['primary_output_sample_range'];mask[a:b]=True
            stat['primary_per_jammer_attenuation_db']=old.evaluate_q15.attenuation(meta,names,rows,result,mask)
            summaries.append(stat);windows+=win;energy+=old.fixed_comparison.dynamic_energy(meta,names,rows,result,method)
        print(json.dumps([dict(case=s['case'],method=s['method'],nrmse=s['primary_nrmse_percent'],target=s['primary_target_nrmse_percent'],probe=s['worst_probe_amplitude_abs_change_db']) for s in summaries if s['case']==case],indent=2),flush=True)
    bench.write(out/'results.json',dict(status='FIXED_TRAJECTORY_DIAGNOSTIC',summaries=summaries,
        verification=checks,pending=pending,policy_sha256=bench.sha(HERE/'retire96_policy.json'),source_sha256=bench.sha(Path(__file__)),
        limits=['No new completeRTL: original configuration availability remains fixed; manager backpressure changes are not simulated.',
                'New non-removal priority can force an unfinished retirement fade to its target before starting a new32fade; acceleration and discontinuity are included in output scoring.',
                'No length sweep, no labels in policy, no change to Q15banks/18Q16FIR/21gate or original primary metric.',
                'New V30 gap8ms heldout remains untouched until policy selection.']))
    bench.csv_write(out/'summary.csv',summaries);bench.csv_write(out/'windows.csv',windows);bench.csv_write(out/'dynamic_energy.csv',energy)


if __name__=='__main__':main()

"""Frozen COMMITTED_SLOT_RETIRE64 on actual control/config trajectories.

This is an offline fixed-trajectory diagnostic, not a new complete RTL run.
Only logical authorization and committed-slot metadata are read by the policy.
Component truth is used after the configuration schedule is frozen, for scoring.
"""
from pathlib import Path
import argparse
import csv
import json
import os
import sys
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
import numpy as np

HERE=Path(__file__).resolve().parent
RTL=HERE.parents[1]
V29=RTL/'v29_c4_control_repair_work'
sys.path.insert(0,str(V29/'software'))
import score_actual as old
bench=old.bench
BUILD=old.evaluate_q15.build_q15
OLD_CASES=('c4_two_pairs_staggered','c4_second_pair_simultaneous','c4_both_pairs_enter_together_holdout')
CASES=OLD_CASES+('quiet_high','c4_temporal_gap2ms_dev')


def table(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def fixture(case):
    return HERE/'fixtures'/case if case=='c4_temporal_gap2ms_dev' else old.fixture(case)


def export(case):
    if case=='c4_temporal_gap2ms_dev':return HERE.parent/'rtl_validation/results'/(case+'_v27_actual')
    if case==OLD_CASES[-1]:return V29/'rtl_validation/results'/(case+'_v27_actual')
    return old.V27/'rtl_validation/results'/(case+'_adopted_final')


def token(row,slot):
    return (slot,(int(row['identities_hex'],16)>>(32*slot))&0xffffffff,
            (int(row['frequencies_hex'],16)>>(32*slot))&0xffffffff)


def schedule(folder,clocks,retirement):
    configs=table(folder/'cfg_snapshots.csv')
    auth=table(folder/'authorizations.csv')
    events=[]
    for row in configs:events.append((int(np.searchsorted(clocks,int(row['clock']))),1,'config',row))
    for row in auth:events.append((int(np.searchsorted(clocks,int(row['clock']))),0,'auth',row))
    desired={};effective={};retiring={};ever_armed=set();updates=[];audit=[]
    stats=dict(arms=0,expiries=0,same_slot_new_alive_priority=0,same_slot_new_commit_priority=0,
               ordinary_config_while_retiring=0,maximum_effective_centers=0)
    # Dynamic expiry insertion is deterministic; only four slots can be pending.
    events.sort(key=lambda v:(v[0],v[1]));position=0
    def publish(sample,why):
        nonlocal effective
        proposed=dict(desired)
        for slot,item in retiring.items():
            if sample < item['deadline'] and slot not in proposed:
                proposed[slot]=item['token']
        assert len(proposed)<=4
        stats['maximum_effective_centers']=max(stats['maximum_effective_centers'],len(proposed))
        if proposed!=effective:
            signature=sorted(proposed.values())
            updates.append(dict(sample=sample,frequencies_hz=[t[2]*32000/(1<<32) for t in signature],
                tokens=signature,reason=why,retiring_slots=sorted(retiring)))
            effective=proposed
    while position<len(events) or retiring:
        next_event=events[position][0] if position<len(events) else len(clocks)+1
        next_expiry=min((item['deadline'] for item in retiring.values()),default=len(clocks)+1)
        sample=min(next_event,next_expiry)
        if sample>=len(clocks):break
        # A new token arriving on the same sample wins over stale retirement expiry.
        current=[]
        while position<len(events) and events[position][0]==sample:
            current.append(events[position]);position+=1
        for _,_,kind,row in current:
            if kind=='auth':
                alive=int(row['alive'])
                if retirement:
                    for slot in range(4):
                        if alive&(1<<slot):
                            if slot in retiring:
                                audit.append(dict(sample=sample,event='same_slot_new_alive_priority',slot=slot,
                                                  old=retiring[slot]['token'],incoming=token(row,slot)))
                                stats['same_slot_new_alive_priority']+=1
                                del retiring[slot]
                            continue
                        if slot not in effective:continue
                        item=effective[slot]
                        # Lifecycle/ID key prevents a weak/missing sequence renewing its fixed deadline.
                        key=(slot,item[1])
                        if key in ever_armed:continue
                        assert item[1]==token(row,slot)[1], 'Retirement source must match the removed committed ID'
                        ever_armed.add(key);retiring[slot]=dict(token=item,deadline=sample+64)
                        stats['arms']+=1
                        audit.append(dict(sample=sample,event='arm',slot=slot,token=item,deadline=sample+64,
                            arm_clock=int(row['clock']),logical_window_end=int(row['window_end'])))
            else:
                incoming={slot:token(row,slot) for slot in range(4) if int(row['mask'])&(1<<slot)}
                if retiring:stats['ordinary_config_while_retiring']+=1
                for slot,item in incoming.items():
                    if slot in retiring and item!=retiring[slot]['token']:
                        audit.append(dict(sample=sample,event='same_slot_new_commit_priority',slot=slot,
                            old=retiring[slot]['token'],incoming=item))
                        stats['same_slot_new_commit_priority']+=1;del retiring[slot]
                desired=incoming
                publish(sample,'original_actual_config')
        for slot in list(retiring):
            if retiring[slot]['deadline']<=sample:
                audit.append(dict(sample=sample,event='expiry',slot=slot,token=retiring[slot]['token']))
                stats['expiries']+=1;del retiring[slot]
        publish(sample,'retirement_expiry_or_priority')
    return updates,dict(stats=stats,events=audit,
        limits='Published authorization edge is an earliest-visible logical edge, not actual unlocked-manager arm. Original commits retained; synthetic expiry builds have zero extra service. All original inputs/observations fixed.')


def replay(rows,mixed,updates):
    n=len(mixed);banks=[BUILD([])];ids={banks[0].tobytes():0};infos=[]
    current=0;target=None;pending=None;alpha=0;j=0
    trajectory=np.zeros((n,3),np.int32)
    events=sorted(enumerate(updates),key=lambda p:(p[1]['sample'],p[0]))
    for sample in range(n):
        while j<len(events) and events[j][1]['sample']<=sample:
            update=events[j][1];h,info=BUILD(update['frequencies_hz'],True)
            key=h.tobytes()
            if key not in ids:ids[key]=len(banks);banks.append(h);infos.append(info)
            pending=ids[key];j+=1
        if target is None and pending is not None:
            if pending!=current:target=pending;alpha=1
            pending=None
        if target is None:trajectory[sample]=(current,current,0)
        else:
            trajectory[sample]=(current,target,alpha)
            if alpha==32:current,target,alpha=target,None,0
            else:alpha+=1
    components=np.zeros_like(rows);numerator=np.zeros(n,np.int64)
    padded=np.pad(rows,((0,0),(640,0)));pm=np.pad(mixed.astype(np.int64),(640,0))
    split=np.r_[0,np.flatnonzero(np.any(trajectory[1:,:2]!=trajectory[:-1,:2],axis=1))+1,n]
    for a,b in zip(split[:-1],split[1:]):
        oldbank,newbank=trajectory[a,:2];weights={int(oldbank):32-trajectory[a:b,2]}
        weights[int(newbank)]=weights.get(int(newbank),0)+trajectory[a:b,2]
        for bank,weight in weights.items():
            if not np.any(weight):continue
            h=banks[bank]
            numerator[a:b]+=np.convolve(pm[a:b+640],h,'valid')*weight
            for lane in range(len(rows)):
                components[lane,a:b]+=np.convolve(padded[lane,a:b+640],h/65536,'valid')*weight/32
    output,saturation=bench.fir_replay._rne_clip_array(numerator)
    return dict(output=output,components=components,banks=np.asarray(banks),trajectory=trajectory,saturations=saturation),infos


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cases',nargs='*',default=list(CASES))
    args=parser.parse_args();assert set(args.cases)<=set(CASES)
    policy=bench.read(HERE/'retire64_policy.json');assert policy['additional_retirement_samples']==64
    out=HERE/'retire64_diagnostic';out.mkdir(exist_ok=True)
    summaries=[];checks=[];pending=[];windows=[];energy=[]
    for case in args.cases:
        folder=export(case)
        if not (folder/'verification.json').exists():pending.append(case);continue
        meta,x,names,rows,current,data,check=old.actual.load_actual(folder,fixture(case))
        base_updates,_=schedule(folder,data['input_clocks'],False)
        self_result,_=replay(rows,x,base_updates)
        delta=int(np.max(np.abs(self_result['output'].astype(np.int32)-current['output'].astype(np.int32))))
        assert delta==0,(case,delta)
        changes,audit=schedule(folder,data['input_clocks'],True)
        candidate,infos=replay(rows,x,changes)
        target=out/case;target.mkdir(exist_ok=True)
        bench.write(target/'updates.json',dict(policy_sha256=bench.sha(HERE/'retire64_policy.json'),updates=changes,audit=audit))
        np.savez_compressed(target/'candidate.npz',input=x,component_names=names,
            component_output_counts=candidate['components'],output=candidate['output'],
            banks_q16=candidate['banks'],trajectory=candidate['trajectory'])
        checks.append(dict(case=case,base_replay_max_output_difference_lsb=delta,actual_check=check,
            configuration_rebuilds=len(changes),original_commits=len(base_updates),policy=audit))
        arms={'actual_v27_q15':(current,dict(updates=[])),
              'retire64_fixedtrajectory_zeroextraservice':(candidate,dict(updates=changes))}
        if case in OLD_CASES or case=='quiet_high':
            for db in (21,18):
                result,decisions,_=old.baseline(case,db,x,names)
                arms['conventional1024_fixed'+str(db)+'_dtft']=(result,decisions)
        for method,(result,decisions) in arms.items():
            stat,win=bench.score(meta,names,rows,result,decisions,method,'same_q15_fir')
            stat.update(old.actual.settled_diagnostic(meta,rows,names,result))
            stat.update(old.control.false_active_diagnostic(meta,result))
            mask=np.zeros(len(x),bool);a,b=meta['primary_output_sample_range'];mask[a:b]=True
            stat['primary_per_jammer_attenuation_db']=old.evaluate_q15.attenuation(meta,names,rows,result,mask)
            summaries.append(stat);windows+=win
            energy+=old.fixed_comparison.dynamic_energy(meta,names,rows,result,method)
        print(json.dumps([dict(case=s['case'],method=s['method'],nrmse=s['primary_nrmse_percent'],
            target=s['primary_target_nrmse_percent'],probe=s['worst_probe_amplitude_abs_change_db'])
            for s in summaries if s['case']==case],indent=2),flush=True)
    bench.write(out/'results.json',dict(status='FIXED_TRAJECTORY_DIAGNOSTIC',summaries=summaries,
        self_replay_checks=checks,pending=pending,policy_sha256=bench.sha(HERE/'retire64_policy.json'),
        source_sha256=bench.sha(Path(__file__)),limits=[
            'Not a new complete RTL run. No actual retirement timer/manager/new coefficient service schedule is simulated.',
            'Logical published alive transitions are used as earliest manager-visible arm; actual locked manager may arm later.',
            'Extra expiry rebuilds are instantaneous before original32sample fade; actual Q15 service may increase delay and damage.',
            'No generator event/frequency truth enters retirement policy. Components enter frozen-output scoring only.',
            'V29 previous holdout is now a development diagnostic, not the V30 heldout.']))
    bench.csv_write(out/'summary.csv',summaries);bench.csv_write(out/'windows.csv',windows);bench.csv_write(out/'dynamic_energy.csv',energy)


if __name__=='__main__':main()

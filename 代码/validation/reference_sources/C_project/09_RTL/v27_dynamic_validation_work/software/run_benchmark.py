"""Frozen warm-start comparison; exact arithmetic detector, behavioral slots.

Only signed mixed samples enter the detector. Component labels enter scoring
after all updates have been frozen. This is NOT a cycle-accurate whole FPGA.
"""
from pathlib import Path
import argparse, csv, hashlib, importlib.util, json, os, sys, time
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
RTL=ROOT.parent
sys.path.insert(0,str(RTL/'pro_v2/model'))
import fft_reference, gate_reference
sys.path.insert(0,str(RTL/'v12_work/aux_tracker'))
import tracker_reference
sys.path.insert(0,str(RTL/'pro_v2/reference/strong_only_v1_4_candidate'))
import strong_detector, short_detector
sys.path.insert(0,str(RTL/'v23_conventional_comparison_work'))
import baseline, fir_replay
sys.path.insert(0,str(RTL/'v25_coefficient_rescue_work'))
import rescue

MODELS={3:RTL/'v12_work/auto_validation/revision4_compatible_pair_single_c3/candidate_capacity_reference.py',
        4:RTL/'v12_work/auto_validation/revision2/candidate_capacity_reference.py'}

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load_model(cap):
    spec=importlib.util.spec_from_file_location('current_cap'+str(cap),MODELS[cap])
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
def write(p,v):
    Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
def csv_write(p,rows):
    if not rows:Path(p).write_text('',encoding='utf8');return
    with Path(p).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));w.writeheader()
        for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list,tuple)) else v for k,v in r.items()})

def check_archived_numeric():
    folder=RTL/'v21_quality_area_work/quality/results/full_auto/dynamic/raw_trace'
    x=np.load(folder/'input.npy');model=load_model(4)
    with (folder/'observations.csv').open(encoding='utf-8-sig') as f:actual=list(csv.DictReader(f))
    checks=[]
    for row in actual:
        end=int(row['window_end']);re,im,power=fft_reference.transform(x[end-512:end])
        peaks,stats=model.estimate_q24_from_fft(re,im)
        fitted=[p for p in peaks if not p.get('diagnostic_only')]
        grids=[int(round(p['frequency_hz']/3.90625)) for p in fitted]
        want=[int(row['grid'+str(i)]) for i in range(int(row['count']))]
        # Actual telemetry's count includes only final extracted, outer055 peaks.
        fitted=[p for p in fitted if p['outer055_pass']]
        grids=[int(round(p['frequency_hz']/3.90625)) for p in fitted]
        bits=sum((int(bool(p['strong']))<<i) for i,p in enumerate(fitted))
        checks.append(dict(end=end,grids=grids,actual_grids=want,strong_bits=bits,
                           actual_strong_bits=int(row['strong_bits']),numeric_failure=stats['numeric_failure'],
                           match=grids==want and bits==int(row['strong_bits'])))
    result=dict(observations=len(checks),matches=sum(c['match'] for c in checks),checks=checks,
                note='Finite main arithmetic check only. Behavioral controller/timing not checked here.',
                model_sha256=sha(MODELS[4]),input_sha256=sha(folder/'input.npy'))
    write(HERE/'archived_numeric_check.json',result)
    if result['matches']!=len(checks):raise AssertionError('Archived main detector mismatch; inspect archived_numeric_check.json')
    return result

def current_updates(x,capacity):
    """Exact FFT/fitter/gate; shared behavioral control at zero compute delay.

    The inherited Python controller is explicitly NOT RTL queue scheduling.
    Capacity, local auxiliary, age and diagnostic overflow are imposed here.
    """
    short_detector.SETTINGS['max_tracks']=capacity
    strong_detector.legacy.SETTINGS['max_tracks']=capacity
    controller=strong_detector.StrongController();model=load_model(capacity)
    observations=[];updates=[];previous=();aux=[];aux_end=-99999
    for frame_no,end in enumerate(range(256,len(x)+1,64)):
        if end<512:continue
        re,im,power=fft_reference.transform(x[end-512:end]);peaks,stats=model.estimate_q24_from_fft(re,im)
        diagnostic=[p for p in peaks if p.get('diagnostic_only')]
        peaks=[p for p in peaks if not p.get('diagnostic_only') and p.get('outer055_pass',False)]
        evidence=[]
        for p in peaks:
            e=gate_reference.decision(power,round(p['frequency_hz']/62.5))
            evidence.append(dict(frequency_hz=p['frequency_hz'],strong_eligible=e['strong'],
                                 raw_peak_power=e.get('peak_power',0),background_power=e['background_twice']/2))
        # Production local auxiliary observes existing strong authorization;
        # this abstraction takes that snapshot at its causal window boundary.
        if end>=1024 and frame_no%4==0:
            refs=[(t['id'],round(t['frequency_hz']/3.90625)) for t in controller.tracks if t['strong_eligible']]
            pre, pim, pp=fft_reference.transform(x[end-1024:end])
            aux,_=tracker_reference.detect(pp,refs);aux_end=end
        usable_aux=aux if end-aux_end<=192 else []
        frame=controller.step(peaks,evidence,frame_no,end,usable_aux)
        block_extra=False
        diagnostic_rows=[]
        for p in diagnostic:
            e=gate_reference.decision(power,round(p['frequency_hz']/62.5))
            # Diagnostic amplitude uses the original fitter's Q24 threshold.
            outer=sum(int(v)**2 for v in p['coefficient_q24'])>=851461804550
            block_extra|=bool(outer and e['strong'])
            diagnostic_rows.append(dict(frequency_hz=p['frequency_hz'],outer055_pass=bool(outer),strong=e['strong']))
        auth=frame['authorizations']
        # Preserve supported old members under an illegal new proposal. This
        # models member retention without imitating coefficient-cache service.
        legal=[]
        for members in frame['proposals'].values():legal.extend((p['id'],p['frequency_hz']) for p in members)
        legal_ids={i for i,f in legal}
        prior=dict(getattr(controller,'_active',{}))
        retained={i:f for i,f in prior.items() if str(i) in auth and auth[str(i)]['misses']<2}
        if not block_extra:
            for i,f in legal:retained[i]=f
        controller._active=retained
        centers=tuple(sorted(retained.values()))
        if centers!=previous:
            updates.append(dict(sample=end,frequencies_hz=list(centers),observation=frame_no));previous=centers
        observations.append(dict(observation=frame_no,window_start_sample=end-512,window_end_sample=end,
            result_available_sample=end,candidates=peaks,diagnostic=diagnostic_rows,
            evidence=evidence,authorized_tracks=list(auth.values()),
            groups=frame['desired'],active_centers_hz=list(centers),aux_window_end=aux_end if usable_aux else None,
            auxiliary_centers_hz=[p['frequency_hz'] for p in usable_aux],
            ambiguity=bool(frame['raw_unsupported_clusters'] or frame['unsupported_clusters']),
            overflow_block=block_extra,numeric_failure=stats['numeric_failure'],
            fit_statistics={k:stats[k] for k in ('grid_trials','searches','joint_refits','largest_coupled_dimension','reciprocal_calls','real_multiplications')},
            source='exact integer main arithmetic + behavioral controller; zero service slots'))
    return dict(updates=updates,observations=observations,model_path=str(MODELS[capacity]),model_sha256=sha(MODELS[capacity]),
        stats=dict(observations=len(observations),updates=len(updates),numeric_failures=sum(o['numeric_failure'] for o in observations),
                   ambiguous_observations=sum(o['ambiguity'] for o in observations),overflow_observations=sum(o['overflow_block'] for o in observations),
                   maximum_active_centers=max((len(o['active_centers_hz']) for o in observations),default=0)),
        limitations=['Main FFT/fitter/gate finite-bit arithmetic checked on archived V21; controller is behavioral, not whole-RTL equivalence.',
                     'Zero compute/configuration latency, no coefficient task queue or hardware backpressure is modeled.',
                     'Auxiliary snapshot/publication boundary idealized; measured RTL scheduling remains separate.',
                     'Old StrongController history/matching rules retained, capacity and local auxiliary adapted.'])

def replay_fast(rows,updates,mixed,depth):
    """Same Q16 FIR/fade arithmetic; short valid convolutions per segment."""
    n=len(mixed);banks=[fir_replay.build_bank([])];ids={banks[0].tobytes():0};bank_info=[]
    trajectory=np.zeros((n,3),np.int32);current=0;target=None;pending=None;alpha=0;j=0
    events=sorted(enumerate(updates),key=lambda p:(p[1]['sample'],p[0]))
    def get_bank(u):
        if depth is None:h=fir_replay.build_bank(u['frequencies_hz']);info=None
        else:h,info=rescue.build_weighted_bank(u['frequencies_hz'],depth,True)
        key=h.tobytes()
        if key not in ids:ids[key]=len(banks);banks.append(h);bank_info.append(info)
        return ids[key]
    for i in range(n):
        while j<len(events) and events[j][1]['sample']<=i:pending=get_bank(events[j][1]);j+=1
        if target is None and pending is not None:
            if pending!=current:target=pending;alpha=1
            pending=None
        if target is None:trajectory[i]=current,current,0
        else:
            trajectory[i]=current,target,alpha
            if alpha==32:current,target,alpha=target,None,0
            else:alpha+=1
    components=np.zeros_like(rows);numerator=np.zeros(n,np.int64)
    padded=np.pad(rows,((0,0),(640,0)));pm=np.pad(mixed.astype(np.int64),(640,0))
    split=np.r_[0,np.flatnonzero(np.any(trajectory[1:,:2]!=trajectory[:-1,:2],axis=1))+1,n]
    for a,b in zip(split[:-1],split[1:]):
        old,new=trajectory[a,:2];weights={int(old):32-trajectory[a:b,2]}
        if new in weights:weights[int(new)]=weights[int(new)]+trajectory[a:b,2]
        else:weights[int(new)]=trajectory[a:b,2]
        for bank,weight in weights.items():
            if not np.any(weight):continue
            h=banks[bank]
            integer=np.convolve(pm[a:b+640],h,mode='valid');numerator[a:b]+=integer*weight
            for row in range(len(rows)):
                components[row,a:b]+=np.convolve(padded[row,a:b+640],h/65536,mode='valid')*weight/32
    y,sat=fir_replay._rne_clip_array(numerator)
    assert np.max(abs(components.sum(axis=0)-numerator/2**21),initial=0)<1e-7
    return dict(output=y,components=components,banks=np.array(banks),trajectory=trajectory,saturations=sat,
                bank_info=bank_info)

def align(x):return np.pad(x,((0,0),(320,0)))[:,:x.shape[1]] if x.ndim==2 else np.pad(x,(320,0))[:len(x)]
def error(ref,y):
    den=float(ref@ref);return float(100*np.linalg.norm(y-ref)/np.sqrt(den)) if den else None

def score(meta,names,rows,result,decisions,method,arm):
    refrows=align(rows);clean=[i for i,k in enumerate(names) if k=='target' or k.startswith('probe_')]
    ref=refrows[clean].sum(axis=0);target=names.index('target');y=result['output'].astype(float)
    a,b=meta['primary_output_sample_range'];n=len(y);windows=[]
    for end in range(576,n+1,64):
        start=end-256;r=ref[start:end];out=y[start:end]
        target_r=refrows[target,start:end];target_y=result['components'][target,start:end]
        amps={}
        for j,pair in enumerate(meta['pairs']):
            k=names.index('probe_'+str(j));rp=refrows[k,start:end];op=result['components'][k,start:end]
            amps[str(j)]=float(10*np.log10(max(float(op@op),1e-30)/max(float(rp@rp),1e-30)))
        atten={}
        present=[]
        for i in range(len(meta['frequencies_hz'])):
            spans=[e for e in meta['events'] if e['center']==i]
            if any(start-320>=e['start_sample'] and end-320<=e['end_sample'] for e in spans):
                k=names.index('interference_'+str(i));ri=refrows[k,start:end];oi=result['components'][k,start:end]
                atten[str(i)]=float(10*np.log10(max(float(ri@ri),1e-30)/max(float(oi@oi),1e-30)));present.append(i)
        windows.append(dict(case=meta['name'],method=method,arm=arm,end_sample=end,time_ms=end/32,
            nrmse_percent=error(r,out),target_nrmse_percent=error(target_r,target_y),
            target_energy_change_db=float(10*np.log10(max(float(target_y@target_y),1e-30)/max(float(target_r@target_r),1e-30))),
            probe_amplitude_change_db=amps,interference_attenuation_db=atten,fully_present_centers=present))
    responses=[]
    for onset in sorted(set(e['start_sample'] for e in meta['events'])):
        next_event=min([v for e in meta['events'] for v in (e['start_sample'],e['end_sample']) if v>onset],default=n)
        streak=0;first=None
        for w in windows:
            if w['end_sample']-256-320<onset:continue
            if w['end_sample']-320>next_event:break
            streak=streak+1 if w['nrmse_percent']<=20 else 0
            if streak>=3:first=(w['end_sample']-onset)/32;break
        newly=[e for e in meta['events'] if e['start_sample']==onset]
        first_configs=[]
        for event in newly:
            matching=[u['sample'] for u in decisions['updates'] if u['sample']>=onset and
                      any(abs(f-event['frequency_hz'])<=46.875 for f in u['frequencies_hz'])]
            sample=min(matching,default=None)
            first_configs.append(dict(center=event['center'],sample=sample,
                latency_ms=(sample-onset)/32 if sample is not None else None,
                note='software decision/config boundary, not output-quality recovery or measured RTL'))
        responses.append(dict(onset_sample=onset,next_event_sample=next_event,completion_ms=first,
                              complete_before_next_event=first is not None,first_configurations=first_configs))
    bank_active=np.any(result['banks']!=fir_replay.build_bank([])[None,:],axis=1)
    active=bank_active[result['trajectory'][:,0]] | (result['trajectory'][:,2]!=0)
    quiet_mask=np.ones(n,dtype=bool)
    for e in meta['events']:quiet_mask[max(0,e['start_sample']):min(n,e['end_sample']+1024)]=False
    quiet_mask[:512]=False
    bypass=refrows.sum(axis=0)
    bypass_error=error(ref[a:b],bypass[a:b]);output_error=error(ref[a:b],y[a:b])
    stats=dict(case=meta['name'],capacity=meta['capacity'],method=method,arm=arm,primary_start=a,primary_stop=b,
        primary_nrmse_percent=error(ref[a:b],y[a:b]),whole_nrmse_percent=error(ref[320:],y[320:]),
        bypass_primary_nrmse_percent=bypass_error,
        primary_recovery_gain_over_bypass_db=float(20*np.log10(bypass_error/output_error)) if bypass_error and output_error else None,
        primary_target_nrmse_percent=error(refrows[target,a:b],result['components'][target,a:b]),
        worst_8ms_target_nrmse_percent=max((w['target_nrmse_percent'] for w in windows),default=0),
        worst_probe_amplitude_abs_change_db=max((abs(v) for w in windows for v in w['probe_amplitude_change_db'].values()),default=0),
        saturations=result['saturations'],updates=len(decisions['updates']),banks=len(result['banks']),
        false_active_samples=int(np.sum(active&quiet_mask)),quiet_evaluable_samples=int(quiet_mask.sum()),
        response_events=responses)
    return stats,windows

def run_case(folder):
    meta=read(folder/'fixture.json');x=np.load(folder/'input.npy')
    out=HERE/'results'/meta['name'];out.mkdir(parents=True,exist_ok=True)
    current_path=out/'current_decisions.json'
    if current_path.exists():current=read(current_path)
    else:
        t=time.perf_counter();current=current_updates(x,meta['capacity']);write(current_path,current)
        print(meta['name'],'current detector',round(time.perf_counter()-t,2),'s',current['stats'],flush=True)
    cfg=read(RTL/'v23_conventional_comparison_work/frozen_configs.json')['1024'];cfg['max_centers']=meta['capacity']
    baseline_path=out/'conventional1024_decisions.json'
    if baseline_path.exists():long=read(baseline_path)
    else:long=baseline.detect_updates(x,cfg);write(baseline_path,long)
    with np.load(folder/'components.npz') as z:names=z.files;rows=np.vstack([z[k]*32768 for k in names])
    assert np.max(abs(rows.sum(axis=0)-x),initial=0)<1e-7
    summaries=[];windows=[]
    for method,d in [('current',current),('conventional1024',long)]:
        for arm,depth in [('production',None),('repair098',.98),('repair100',1.)]:
            t=time.perf_counter();result=replay_fast(rows,d['updates'],x,depth)
            stat,win=score(meta,names,rows,result,d,method,arm);summaries.append(stat);windows+=win
            np.savez_compressed(out/(method+'_'+arm+'.npz'),input=x,output=result['output'],
                component_names=np.array(names),component_output_counts=result['components'],
                banks_q16=result['banks'],trajectory=result['trajectory'])
            write(out/(method+'_'+arm+'_bank_diagnostics.json'),result['bank_info'])
            print(meta['name'],method,arm,'error',round(stat['primary_nrmse_percent'],3),
                  'replay',round(time.perf_counter()-t,2),'s',flush=True)
    write(out/'summary.json',dict(summaries=summaries,fixture_sha256=sha(folder/'fixture.json'),input_sha256=sha(folder/'input.npy'),
            limitations=current['limitations'],script_sha256=sha(Path(__file__))))
    csv_write(out/'summary.csv',summaries);csv_write(out/'windows.csv',windows)
    return summaries

def main():
    p=argparse.ArgumentParser();p.add_argument('--cases',nargs='*');p.add_argument('--check-only',action='store_true');p.add_argument('--skip-check',action='store_true');a=p.parse_args()
    if not a.skip_check:
        check=check_archived_numeric();print('Archived numeric check',check['matches'],'/',check['observations'],flush=True)
    if a.check_only:return
    plan=read(ROOT/'experiment_plan.json');summaries=[]
    for c in plan['cases']:
        if a.cases and c['name'] not in a.cases:continue
        summaries+=run_case(ROOT/'fixtures'/c['name'])
    write(HERE/'results.json',dict(summaries=summaries,plan_sha256=sha(ROOT/'experiment_plan.json'),
        scope='new frozen warm-start software experiment; mixed input only, exact fixed main arithmetic; behavioral controller at zero service delay, no FPGA performance claim'))
    csv_write(HERE/'summary.csv',summaries)

if __name__=='__main__':main()

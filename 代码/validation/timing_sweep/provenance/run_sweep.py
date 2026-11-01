"""Two frozen 1D onset-spacing sweeps: actual complete C4 RTL vs software1024."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import numpy as np

from verify_runtime import HERE, WORK, BUILD, RELEASE, EXE_SHA, sha, dump

ENGINE = Path('D:/FPGA_Competition/支撑材料/项目资料/04_PC_Software/20261027_input_comparison/engine/reference_1024.py')
MODEL = RELEASE/'validation/recorded_1024_reference'
VALUES = [0, 1, 2, 4, 8, 16, 32, 64]
FS, N = 32000, 16000
WINDOWS = {'full_common': (0, 15680), 'entry': (2880, 7360), 'stable': (7680, 10240)}
LOCK = threading.Lock()
PROGRESS = {'phase': 'starting', 'done_unique_cases': 0, 'failed_unique_cases': 0, 'total_unique_cases': 15, 'cases': {}}

def payload(x): return hashlib.sha256(np.asarray(x).astype('<i2').tobytes()).hexdigest()
def now(): return datetime.now(timezone(timedelta(hours=8))).isoformat()
def emit():
    value = dict(PROGRESS, updated_at=now())
    tmp = HERE/'progress.tmp.json'
    dump(tmp, value)
    os.replace(tmp, HERE/'progress.json')

def freeze():
    sys.path.insert(0, str(RELEASE/'host_ui'))
    from scene import default_config, generate_scene
    cfg = default_config(4)
    cfg.update(seconds=.5, seed=26102131, target_rms=.01, noise_rms=.001, use_probes=False)
    for event in cfg['events']: event['enabled'] = False
    _, _, parts = generate_scene(cfg)
    target, noise = parts['target'].copy(), parts['noise'].copy()
    frequencies, phases = [2311.3, 2436.3, 4972.7, 5097.7], [-.3, .8, 1.2, -1.0]
    amplitude = np.sqrt(2*np.mean(target**2)*100)
    stop = np.array([11200, 11520, 11840, 12160])
    time_axis = np.arange(N)/FS
    proposals = []
    for axis in ('internal_delta', 'pair_gap'):
        for value in VALUES:
            delta, gap = (value, 8) if axis == 'internal_delta' else (1, value)
            start = np.array([3200, 3232, 3200+gap*32, 3200+gap*32+delta*32])
            tones = np.array([amplitude*np.sin(2*np.pi*f*(time_axis-s/FS)+phase)
                              *((np.arange(N)>=s)&(np.arange(N)<e))
                              for f,s,e,phase in zip(frequencies,start,stop,phases)])
            proposals.append(dict(axis=axis, delay_ms=value, internal_delta_ms=delta, pair_gap_ms=gap,
                start_samples=start.tolist(), stop_samples=stop.tolist(), floating=target+noise+tones.sum(axis=0),
                actual_active_js_db=[float(10*np.log10(np.mean(tone[s:e]**2)/np.mean(target**2)))
                                     for tone,s,e in zip(tones,start,stop)]))
    gain = min(1., .95/max(float(np.max(np.abs(p['floating']))) for p in proposals))
    target, noise = target*gain, noise*gain
    plan_cases, unique = [], {}
    for p in proposals:
        data = np.rint(p.pop('floating')*gain*32768).astype(np.int16)
        digest = payload(data)
        if digest not in unique:
            case_id = f'case_{len(unique):02d}'
            folder = HERE/'cases'/case_id
            folder.mkdir(parents=True, exist_ok=True)
            if (folder/'input.npy').exists(): assert np.array_equal(np.load(folder/'input.npy'), data)
            else: np.save(folder/'input.npy', data)
            unique[digest] = dict(case_id=case_id, input_payload_sha256=digest, input_file=str(folder/'input.npy'),
                                  max_abs_input_counts=int(np.max(np.abs(data.astype(np.int32)))))
        plan_cases.append(dict(**p, **unique[digest]))
    assert len(unique) == 15
    plan = dict(schema='current-c4-onset-spacing-sweep-1', fs_hz=FS, sample_count=N, seed=26102131,
        target_generation='Existing host raised-cosine PSD Gaussian bandpass target600..7400Hz, RMS0.01; independent fixed noise RMS0.001',
        frequencies_hz=frequencies, onset_phase_rad=phases, fixed_jammer_peak_before_gain=float(amplitude),
        phase_convention='sin(2*pi*f*(t-onset)+phi); per-source onset phase fixed, global phase changes with onset.',
        first_pair_internal_ms=1, nominal_each_jammer_js_db=20, js_note='One fixed sine amplitude across every input; finite active-segment RMS J/S is recorded, not renormalized.',
        common_frontend_gain=gain, target_array_sha256=hashlib.sha256(target.astype('<f8').tobytes()).hexdigest(),
        noise_array_sha256=hashlib.sha256(noise.astype('<f8').tobytes()).hexdigest(),
        axis_values_ms=VALUES, plan_cases=plan_cases, unique_cases=list(unique.values()),
        score_windows={k:{'first_sample':a,'stop_sample_exclusive':b,'start_s':a/FS,'stop_s':b/FS} for k,(a,b) in WINDOWS.items()},
        primary='Full common interval0..490ms; R=100*(E1024-E512)/E1024. Entry/stable are secondary; negative values retained.',
        online_information='Only mixed signed-int16 samples and sample cadence; no labels/target/frequencies sent to DUT or software detector.',
        layers={'current512':'Actual complete V32-equivalent native RTL,50MHz/32kHz,live1024aux,queue,coefficient preparation,fade and FIR',
                'reference1024':'Frozen finite-arithmetic independent estimator with zero frontend/coefficient preparation waiting; same641/Q16/32fade numeric backend, software latest-deferred update policy'},
        architecture_limits='Not a pure-window ablation or two board measurements; 1024 software timing differs. Sample-onset gap does not guarantee queue waiting.',
        native_executable_sha256=EXE_SHA, engine_source_sha256=sha(ENGINE),
        runtime_identity_sha256=sha(HERE/'runtime_identity.json'), generator_source_sha256=sha(RELEASE/'host_ui/scene.py'))
    path = HERE/'experiment_plan.json'
    if path.exists(): assert json.loads(path.read_text(encoding='utf-8')) == plan, 'Frozen experiment plan differs'
    else: dump(path, plan)
    base = HERE/'base_reference.npz'
    if not base.exists(): np.savez_compressed(base, target=target, noise=noise)
    else:
        with np.load(base) as z: assert np.array_equal(z['target'],target) and np.array_equal(z['noise'],noise)
    return plan

def load_engine():
    module = type(sys)('sweep_reference1024')
    module.__file__ = str(ENGINE)
    sys.modules[module.__name__] = module
    exec(compile(ENGINE.read_text(encoding='utf-8'), str(ENGINE), 'exec'), module.__dict__)
    return module

def score(x, y, target, first, stop):
    ref = target[first:stop]
    output = y[first+320:stop+320].astype(float)/32768
    assert len(ref) == len(output)
    return float(100*np.linalg.norm(output-ref)/np.linalg.norm(ref))

def run_case(case, engine):
    name = case['case_id']; folder = HERE/'cases'/name
    result_file = folder/'case_result.json'
    if result_file.exists():
        old = json.loads(result_file.read_text(encoding='utf-8'))
        if old.get('status') == 'PASS' and old['input_payload_sha256'] == case['input_payload_sha256']:
            return old
    raw = WORK/name/('attempt_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    raw.mkdir(parents=True, exist_ok=False)
    began = time.perf_counter()
    with LOCK: PROGRESS['cases'][name] = dict(stage='software1024', started_at=now()); emit()
    try:
        x = np.load(case['input_file'], allow_pickle=False)
        assert x.shape == (N,) and x.dtype == np.dtype('int16') and payload(x) == case['input_payload_sha256']
        reference = engine.run_reference(x, MODEL)
        np.save(folder/'output_1024_software.npy', reference['output'])
        dump(folder/'software_decisions.json', reference['decisions'])
        dump(folder/'software_stats.json', reference['stats'])
        (raw/'input.mem').write_text(''.join(f'{int(v)&65535:04x}\n' for v in x), encoding='ascii')
        command = [str(BUILD/'native/Release/fullrtl.exe'), f'+SAMPLES={N}', f'+INPUT={(raw/"input.mem").as_posix()}', f'+FOLDER={raw.as_posix()}/']
        with LOCK: PROGRESS['cases'][name].update(stage='complete_rtl512', raw_trace=str(raw)); emit()
        with (raw/'run.log').open('w', encoding='utf-8') as log:
            process = subprocess.run(command, cwd=BUILD, stdout=log, stderr=subprocess.STDOUT, timeout=900)
        text = (raw/'run.log').read_text(encoding='utf-8', errors='replace')
        assert process.returncode == 0 and 'PASS FULL_AUTO_QUALITY_TRACE' in text, 'Native RTL integrity failed; inspect raw run.log'
        assert not any(word in text.lower() for word in ('unable to open','cannot open','readmem file not found','failed to open'))
        with (raw/'outputs.csv').open() as f: out_rows = list(csv.DictReader(f))
        assert [int(r['sample']) for r in out_rows] == list(range(N))
        y = np.asarray([int(r['output']) for r in out_rows], np.int16)
        np.save(folder/'output_512_rtl.npy', y)
        final, = [line for line in text.splitlines() if line.startswith('FINAL case=')]
        status = {k:int(v,16 if k in ('active','error_code') else 10) for k,v in re.findall(r'(\w+)=([0-9a-fA-F]+)',final)}
        assert all(status[k] == 0 for k in ('dropped','sat','numeric','deadline','seqfault','errors','error_code'))
        assert status['sent'] == status['outputs'] == N and status['max_fir'] <= 1562
        with (raw/'samples.csv').open() as f: sample_rows = list(csv.DictReader(f))
        assert len(sample_rows) == N and np.array_equal(np.asarray([int(r['input']) for r in sample_rows],np.int16),x)
        waiting_max = max(int(row['waiting']) for row in sample_rows)
        with np.load(HERE/'base_reference.npz', allow_pickle=False) as z: target = z['target']
        metrics = {}
        for window,(a,b) in WINDOWS.items():
            e512, e1024 = score(x,y,target,a,b), score(x,reference['output'],target,a,b)
            metrics[window] = dict(nrmse_512_pct=e512,nrmse_1024_pct=e1024,
                relative_reduction_pct=100*(e1024-e512)/e1024,score_start_s=a/FS,score_stop_s=b/FS,common_samples=b-a)
        traces = folder/'rtl_trace'; traces.mkdir(exist_ok=True)
        for p in raw.iterdir():
            if p.is_file(): shutil.copy2(p,traces/p.name)
        result = dict(status='PASS',case_id=name,input_payload_sha256=payload(x),
            output_512_payload_sha256=payload(y),output_1024_payload_sha256=payload(reference['output']),
            rtl_status=status,waiting_max=waiting_max,software_stats=reference['stats'],metrics=metrics,
            plan_sha256=sha(HERE/'experiment_plan.json'),command=command,raw_trace=str(raw),
            native_exe_sha256=sha(BUILD/'native/Release/fullrtl.exe'),wall_seconds_not_fpga_latency=time.perf_counter()-began)
    except Exception as error:
        result = dict(status='FAIL',case_id=name,input_payload_sha256=case['input_payload_sha256'],
            error=f'{type(error).__name__}: {error}',traceback=traceback.format_exc(),raw_trace=str(raw),
            wall_seconds_not_fpga_latency=time.perf_counter()-began)
    dump(result_file,result)
    with LOCK:
        PROGRESS['cases'][name] = dict(stage=result['status'],result_file=str(result_file),finished_at=now())
        if result['status']=='PASS': PROGRESS['done_unique_cases'] += 1
        else: PROGRESS['failed_unique_cases'] += 1
        emit()
    print(name,result['status'],result.get('metrics',result.get('error')),flush=True)
    return result

def tables(plan, results):
    rows = []
    for p in plan['plan_cases']:
        result = results.get(p['case_id'])
        if result is None: continue
        for window,(a,b) in WINDOWS.items():
            metric = result.get('metrics',{}).get(window,{})
            status = result.get('rtl_status',{})
            rows.append(dict(axis=p['axis'],delay_ms=p['delay_ms'],internal_delta_ms=p['internal_delta_ms'],
                pair_gap_ms=p['pair_gap_ms'],case_id=p['case_id'],input_payload_sha256=p['input_payload_sha256'],window=window,
                score_start_s=a/FS,score_stop_s=b/FS,common_samples=b-a,
                nrmse_512_pct=metric.get('nrmse_512_pct'),nrmse_1024_pct=metric.get('nrmse_1024_pct'),
                relative_reduction_pct=metric.get('relative_reduction_pct'),rtl_status=result['status'],
                drop=status.get('dropped'),numeric=status.get('numeric'),deadline=status.get('deadline'),sat=status.get('sat'),
                commits=status.get('commits'),waiting_max=result.get('waiting_max'),error=result.get('error','')))
    with (HERE/'sweep_metrics.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]) if rows else ['axis','delay_ms','window'])
        writer.writeheader();writer.writerows(rows)
    dump(HERE/'results.json',dict(status='COMPLETE' if len(results)==15 else 'RUNNING',
        plan_sha256=sha(HERE/'experiment_plan.json'),unique_results=results,
        completed=len(results),passed=sum(r['status']=='PASS' for r in results.values()),
        failures=sum(r['status']!='PASS' for r in results.values()),
        comparison_layers=plan['layers'],main_window='full_common0..490ms',negative_results_retained=True))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    plan=freeze()
    if args.prepare_only:
        print('PLAN FROZEN',sha(HERE/'experiment_plan.json'),flush=True);return
    assert json.loads((HERE/'golden_calibration.json').read_text(encoding='utf-8'))['status']=='GOLDEN_CALIBRATION_PASS'
    engine=load_engine()
    engine.run_reference(np.zeros(32,np.int16),MODEL)
    results={}
    with LOCK: PROGRESS['phase']='sweeping';emit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending={pool.submit(run_case,case,engine):case for case in plan['unique_cases']}
        for future in as_completed(pending):
            value=future.result();results[value['case_id']]=value;tables(plan,results)
    with LOCK: PROGRESS['phase']='complete';emit()
    print('SWEEP COMPLETE',len(results),'failed',sum(r['status']!='PASS' for r in results.values()),flush=True)

if __name__=='__main__':main()

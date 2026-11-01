"""Freeze V30's single bounded policy and two new inputs before testing it."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
RTL = HERE.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    assert not path.exists(), f'Do not replace the predeclared freeze: {path}'
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf8')


def main():
    policy = dict(name='COMMITTED_SLOT_RETIRE64', policy_version=1,
        base='V27 adopted C4, unchanged controller; not EAP/FF',
        controller_sha256='902174',
        additional_retirement_samples=64,
        derivation='641tap FIR delay320 minus512main halfwindow256; fixed64, not searched.',
        arm='At first eligible manager observation of an alive->absent logical member, if that slot still contains the same committed ID, capture its original committed correction/cache and arm once.',
        permission='Original2missing withdrawal, task invalidation, ID/version/observation checks occur immediately; retirement is never an execution authorization for a candidate.',
        expiry='One immutable64sample deadline; missing/weak observations cannot renew it. Normal Q15 merged-bank rebuild/removal after expiry.',
        replacement='A new alive/prepared strong token in the same slot takes priority and cancels old retirement immediately. This may shorten tail protection; report this boundary. It cannot inherit old authorization.',
        unaffected='Other slots new members and frequency updates proceed normally and may include still-retiring committed slots. No candidate admission/threshold/FFT/fitter/precision/FIR length change.',
        capacity='At most one committed/retiring correction per original slot; maximum4. No5th/6thcenter or larger matrix.',
        backend='Same Q15_MAC12round generator,18Q16 merged641tap FIR,continuous history/three banks/32sample fade.',
        diagnostic_limits=['Fixed-trajectory counterfactual is not closed-loop RTL or measured hardware timing.',
            'Diagnostic uses published logical authorization edge; actual manager observes it only when unlocked.',
            'Extra synthetic expiry reconfiguration can have zero additional service in reference; actual Q15 build and queue costs must be evaluated later.',
            'Fixed64 may worsen target damage or tail ringing where old removal was already late.',
            'Repeated partial removals, same-slot reuse and new-task overlap are recorded, not silently discarded.'],
        primary='Original full-primary NRMSE vs same21dB fixed1024; target at least20% relative reduction. No metric substitution.',
        other_metrics='All change0..50ms energy/censoring, original three-window20% recovery diagnostic, perjam20dB attenuation, full/settled1dB midpoint protection, target damage, quiet activation and configuration pressure.',
        stop_rule='No64duration sweep; main256 and3bin interpolation deferred this round. No tuning to heldout labels.')
    source = RTL/'v27_dynamic_validation_work/rtl_validation/final_sources/q15_mac_c4/rtl/group_controller_v14.v'
    if not source.exists():
        manifest_path = RTL/'v27_dynamic_validation_work/rtl_validation/results/c4_two_pairs_staggered_adopted_final/build_manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        source = next(Path(name) for name in manifest['source_sha256'] if Path(name).name=='group_controller_v14.v')
    policy['controller_sha256'] = sha(source)
    policy['controller_path'] = str(source)
    write(HERE/'retire64_policy.json', policy)
    cases=[]
    definitions=[('c4_temporal_gap2ms_dev',26102130,.90,[2177.3,2302.3,5173.7,5298.7],5213,[0,32,64,96],[20317,20349,23501,23533]),
                 ('c4_temporal_gap8ms_heldout',26102131,.95,[1639.7,1764.7,5831.3,5956.3],6179,[0,32,256,288],[22613,22645,26387,26419])]
    for name,seed,seconds,frequencies,start,offsets,stops in definitions:
        cases.append(dict(name=name,capacity=4,seed=seed,seconds=seconds,
            frequencies_hz=frequencies,pairs=[[0,1],[2,3]],
            segments=[[i,(start+offsets[i])/32000,stops[i]/32000] for i in range(4)],
            role='heldout_no_policy_tuning' if 'heldout' in name else 'development',
            pair_start_gap_samples=offsets[2],member_stagger_samples=32))
    plan=dict(schema='v30-temporal-close-c4-1',fs=32000,work_clock_hz=50000000,
        policy_sha256=sha(HERE/'retire64_policy.json'),cases=cases,
        fixed_references='Previously frozen integerDTFT1024:21main/18sensitivity; same Q15backend and full-primary scoring. No new threshold search.',
        heldout_policy='Both input definitions frozen once now. Candidate chosen before heldout scoring. Every negative result retained.',
        online_access='Mixed signed16 samples and causal internal state only; generator metadata is offline scoring only.',
        temporal_scope='Two spectral pairs each125Hz apart, pair groups >750Hz apart. Different source start times,2ms dev/8ms heldout,1ms individual stagger. No guarantee this causes running+waiting; use actual logs.')
    write(HERE/'experiment_plan.json',plan)
    generator=RTL/'v27_dynamic_validation_work/generate_fixtures.py'
    destination=HERE/'generate_fixtures.py'
    assert not destination.exists()
    shutil.copyfile(generator,destination)
    assert sha(generator)==sha(destination)
    subprocess.run([sys.executable,str(destination)],cwd=HERE,check=True)
    write(HERE/'freeze_manifest.json',dict(status='FROZEN_BEFORE_COUNTERFACTUAL_PERFORMANCE',
        policy_sha256=sha(HERE/'retire64_policy.json'),plan_sha256=sha(HERE/'experiment_plan.json'),
        generator_sha256=sha(destination),fixture_manifest_sha256=sha(HERE/'fixtures/manifest.json'),
        cases=[dict(name=c['name'],input_sha256=sha(HERE/'fixtures'/c['name']/'input.npy'),
                    metadata_sha256=sha(HERE/'fixtures'/c['name']/'fixture.json')) for c in cases]))


if __name__=='__main__':main()

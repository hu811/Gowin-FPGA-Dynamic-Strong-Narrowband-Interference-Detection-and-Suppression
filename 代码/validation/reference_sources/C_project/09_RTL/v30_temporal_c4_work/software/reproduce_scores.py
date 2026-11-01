"""Re-score existing frozen captures only; no detector/RTL/parameter experiment."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
MANIFEST=HERE/'evidence_manifest.json'


def read(path):return json.loads(path.read_text(encoding='utf8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def check(files):
    for name,digest in files.items():
        path=Path(name);assert path.is_file(),f'Missing frozen file:{path}'
        assert sha(path)==digest,f'Frozen evidence changed:{path}'


def capture_outputs():
    paths=[p for p in (HERE/'results').iterdir() if p.is_file()]
    paths += [p for folder in (HERE/'hybrid_results').iterdir() for p in folder.iterdir() if p.is_file()]
    return {str(p):sha(p) for p in sorted(paths)}


def capture_inputs():
    import score_selected as selected
    paths=set(HERE.glob('*.py'))
    paths.update(HERE/name for name in ('experiment_plan.json','freeze_manifest.json','selected_variant.json',
        'retire64_policy.json','retire96_policy.json','retire96_freeze_manifest.json','README.md'))
    paths.add(ROOT/'hardware/independent_audit.json')
    for name in ('retire64_diagnostic','retire96_diagnostic','diagnosis'):
        paths.update(p for p in (HERE/name).rglob('*') if p.is_file() and '__pycache__' not in str(p))
    for module in list(sys.modules.values()):
        value=getattr(module,'__file__',None)
        if value:
            path=Path(value).resolve()
            if path.is_file() and path.suffix=='.py' and 'FPGA_Competition' in str(path):paths.add(path)
    exports=[]
    for case in selected.CASES:
        paths.update(selected.fixture(case).iterdir());exports.append(selected.folder(case))
        for db in (21,18):
            folder=(HERE/'baseline_results'/case if case in selected.NEW else
                selected.old.HERE/'baseline_results'/case if case in ('quiet_high',selected.helpers.OLD_CASES[-1]) else
                selected.old.V28/'software/comparison_1024/results'/case)
            paths.update(folder.glob('conventional1024_fixed'+str(db)+'_dtft*'))
    for case in selected.NEW:
        for suffix in ('current','fixed21'):
            exports.append(ROOT/'rtl_validation/hybrid_backend/results'/(case+'_'+suffix))
    for folder in exports:
        paths.update(folder.iterdir())
        manifest=read(folder/'build_manifest.json')
        for name,digest in manifest['source_sha256'].items():
            path=Path(name);assert sha(path)==digest;paths.add(path)
        verification=read(folder/'verification.json')
        if 'policy_manifest' in verification:
            policy_path=Path(verification['policy_manifest']);paths.add(policy_path);policy=read(policy_path)
            for key in ('pre_service_path','detector_decisions'):
                if key in policy:paths.add(Path(policy[key]))
        # Native build identity and receipts are independently checked by the audit.
    return {str(p.resolve()):sha(p) for p in sorted(paths) if p.is_file()}


def main():
    parser=argparse.ArgumentParser()
    modes=parser.add_mutually_exclusive_group();modes.add_argument('--freeze-manifest',action='store_true');modes.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[key]='1'
    if args.freeze_manifest:
        assert not MANIFEST.exists(),'Do not replace final evidence freeze.'
        value=dict(status='FROZEN',purpose='Offline re-scoring existing actual/HYBRID outputs;no new simulation or tuning.',
            command="& '../../.venv/Scripts/python.exe' software/reproduce_scores.py",cwd=str(ROOT),
            selected_variant_sha256=sha(HERE/'selected_variant.json'),inputs=capture_inputs(),outputs=capture_outputs(),
            python=sys.version,limits=['OriginalV27unchanged;notnewalgorithmimprovement.',
                'CommonbackendHYBRID is not fullonline1024RTL or a boardmeasurement.',
                'Fullmidpoint failures,oldnegativeinputs and18software sensitivities remain.',
                'NoP&R/newfs inV30.'])
        MANIFEST.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    else:
        value=read(MANIFEST);check(value['inputs'])
        if not args.verify_only:
            def run(script,*extra):subprocess.run([sys.executable,str(HERE/script),*map(str,extra)],cwd=ROOT,check=True)
            run('score_selected.py')
            for case in ('c4_temporal_gap2ms_dev','c4_temporal_gap8ms_heldout'):
                base=ROOT/'rtl_validation/hybrid_backend/results'
                run('score_hybrid_c4.py','--case',case,'--current',base/(case+'_current'),'--baseline',base/(case+'_fixed21'))
            run('summarize_selected.py');run('plot_temporal_hybrid.py')
        check(value['outputs'])
    print(json.dumps(dict(status='PASS_FROZEN_EVIDENCE',mode='freeze' if args.freeze_manifest else 'verify-only' if args.verify_only else 're-score',
        manifest_sha256=sha(MANIFEST),inputs=len(value['inputs']),outputs=len(value['outputs'])),indent=2))


if __name__=='__main__':main()

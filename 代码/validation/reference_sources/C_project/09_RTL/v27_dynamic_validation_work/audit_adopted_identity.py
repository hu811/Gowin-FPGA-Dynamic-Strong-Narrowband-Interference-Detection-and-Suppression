"""Read adopted actual exports and audit each export's own source identity.

Capacities may adopt different physical variants. A source identity is never
borrowed from another variant or capacity. No P&R pass is inferred here.
"""
from pathlib import Path
import argparse, json, sys
import numpy as np

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'software'))
import run_benchmark as bench
from audit_service_identity import source_bridge,canonical_trajectory,table

CASES=('c3_single_then_pair','c3_pair_then_single','c4_two_pairs_staggered',
       'c4_second_pair_simultaneous','quiet_high')

def source_bundle(folder):
    path=folder/'source_manifest.json'
    manifest=bench.read(path);files=[]
    for row in manifest['files']:
        file=folder/row['path'];measured=bench.sha(file) if file.exists() else None
        files.append(dict(path=row['path'],expected_sha256=row['sha256'],measured_sha256=measured,
                          match=measured==row['sha256']))
    original=folder/'original_project.gprj';relocated=folder/'pro_v2.gprj'
    original_match=bench.sha(original)==manifest['original_gpr_sha256']
    relocated_match=bench.sha(relocated)==manifest['relocated_gpr_sha256']
    return dict(status='PASS' if files and all(v['match'] for v in files) and original_match and relocated_match else 'FAIL',
        bundle=str(folder),variant=folder.name,manifest_sha256=bench.sha(path),
        original_project_manifest_match=original_match,relocated_project_manifest_match=relocated_match,
        original_gpr_sha256=bench.sha(original),relocated_gpr_sha256=bench.sha(relocated),
        files_checked=len(files),files=files)

def one(case,suffix):
    folder=HERE/'rtl_validation/results'/(case+'_'+suffix)
    if not (folder/'verification.json').exists():return dict(case=case,status='PENDING_EXPORT')
    fixture=HERE/'fixtures'/case;meta=bench.read(fixture/'fixture.json')
    manifest=bench.read(folder/'build_manifest.json');verification=bench.read(folder/'verification.json')
    state=bench.read(folder/'run_state.json');identity=source_bridge(folder)
    assert verification['verification']['status']=='PASS'
    assert verification['verification']['mixed_output_bit_mismatches']==0
    assert bench.sha(fixture/'input.npy')==state['input_sha256']
    bundle_path=Path(manifest['frozen_source_bundle'])
    if not bundle_path.is_absolute():bundle_path=(HERE/bundle_path).resolve()
    bundle=source_bundle(bundle_path)
    assert bench.sha(Path(manifest['authoritative_gpr']))==bundle['original_gpr_sha256'], 'frozen source bundle is for another actual GPR'
    variant=manifest.get('variant',bundle_path.name)
    with np.load(folder/'frozen_operator_and_components.npz') as z:final={k:z[k].copy() for k in z.files}
    prior_folder=HERE/'rtl_validation/results'/(case+'_basis_packed')
    prior_manifest=bench.read(prior_folder/'build_manifest.json')
    if int(prior_manifest['capacity'])==int(manifest['capacity']):
        with np.load(prior_folder/'frozen_operator_and_components.npz') as z:prior={k:z[k].copy() for k in z.files}
        assert final['component_names'].tolist()==prior['component_names'].tolist()
        differences={name:int(np.count_nonzero(final[name]!=prior[name])) for name in ('actual_output','input_clocks','output_clocks','component_input_counts','component_output_counts')}
        differences['trajectory']=canonical_trajectory(final['banks_q16'],final['trajectory'],prior['banks_q16'],prior['trajectory'])
        logs={name:dict(records=len(table(folder/name)),identical_records=table(folder/name)==table(prior_folder/name))
              for name in ('events.csv','observations.csv','cfg_snapshots.csv')}
        equal=not any(differences.values()) and all(v['identical_records'] for v in logs.values())
        comparison=dict(mode='same-capacity prior packed complete trace and absolute clocks',
            reference_sha256=bench.sha(prior_folder/'frozen_operator_and_components.npz'),mismatches=differences,logs=logs)
    else:
        # A newly adopted C3 quiet may have an old C4 comparator. Do not compare
        # capacity-dependent internal cycles or masquerade it as C4 hardware.
        assert not meta['events'],'dynamic comparison across capacities is forbidden'
        x=np.load(fixture/'input.npy').astype(np.int16)
        expected=np.r_[np.zeros(320,np.int16),x[:-320]]
        mismatch=int(np.count_nonzero(final['actual_output']!=expected))
        events=table(folder/'events.csv');commits=sum(v['event']=='commit' for v in events)
        equal=mismatch==0 and commits==0
        comparison=dict(mode='independent quiet bypass check; old comparator has different capacity and its clocks are not compared',
            reference_capacity=prior_manifest['capacity'],actual_capacity=manifest['capacity'],output_mismatches=mismatch,commits=commits)
    return dict(case=case,status='PASS' if equal and identity['status']=='PASS' and bundle['status']=='PASS' else 'FAIL',
        actual_capacity=manifest['capacity'],actual_variant=variant,export_suffix=suffix,samples=len(final['actual_output']),
        input_sha256=state['input_sha256'],source_identity=identity,frozen_source_bundle=bundle,comparison=comparison,
        final_archive_sha256=bench.sha(folder/'frozen_operator_and_components.npz'),
        verification_sha256=bench.sha(folder/'verification.json'))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--suffix',default='adopted_final')
    parser.add_argument('--cases',nargs='*',default=list(CASES))
    parser.add_argument('--output',type=Path,default=HERE/'rtl_scores/adopted_final/source_identity.json');args=parser.parse_args()
    records=[one(c,args.suffix) for c in args.cases]
    status='PASS' if all(v['status']=='PASS' for v in records) else 'FAIL' if any(v['status']=='FAIL' for v in records) else 'PENDING'
    result=dict(status=status,cases=records,actual_samples_checked=sum(v.get('samples',0) for v in records),
        script_sha256=bench.sha(Path(__file__)),scope='Per-export actual source identity and frozen-bundle audit; numerical/clocks comparison only for equal capacities. Not a P&R or new usable bitstream claim.')
    args.output.parent.mkdir(exist_ok=True,parents=True);bench.write(args.output,result)
    print(json.dumps(dict(status=status,cases=[dict(case=v['case'],status=v['status']) for v in records],actual_samples_checked=result['actual_samples_checked']),indent=2))

if __name__=='__main__':main()

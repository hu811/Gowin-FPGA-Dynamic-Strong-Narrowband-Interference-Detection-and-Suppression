"""Read final real traces and bind them to the actual full synthesis sources.

This does not substitute an older trace for final data. Old packed files are
used only as an independent equality comparator, after final output scoring.
"""
from pathlib import Path
import argparse, csv, hashlib, json, sys, xml.etree.ElementTree as ET
import numpy as np

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'software'))
import run_benchmark as bench
CASES=('c3_single_then_pair','c3_pair_then_single','c4_two_pairs_staggered','c4_second_pair_simultaneous','quiet_high')

def table(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as file:return list(csv.DictReader(file))

def key(path):return str(Path(path).resolve()).replace('\\','/').lower()

def source_bridge(folder):
    manifest_path=folder/'build_manifest.json';manifest=bench.read(manifest_path)
    gpr=Path(manifest['authoritative_gpr'])
    original={key(p):v for p,v in manifest['source_sha256'].items()}
    entries=[]
    for element in ET.parse(gpr).getroot().find('FileList'):
        if element.attrib.get('type')!='file.verilog' or element.attrib.get('enable','1')=='0':continue
        file=(gpr.parent/element.attrib['path']).resolve()
        actual=bench.sha(file);recorded=original.get(key(file))
        entries.append(dict(path=str(file),current_synthesis_source_sha256=actual,
            final_simulation_original_source_sha256=recorded,match=actual==recorded))
    copied=manifest.get('sim_copies_sha256',{})
    return dict(status='PASS' if entries and all(e['match'] for e in entries) else 'FAIL',
        capacity=manifest['capacity'],authoritative_gpr=str(gpr),gpr_sha256=bench.sha(gpr),
        build_manifest_sha256=bench.sha(manifest_path),source_files_checked=len(entries),
        source_files=entries,simulation_copy_manifest_sha256=hashlib.sha256(json.dumps(copied,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        declared_simulation_only_changes=manifest.get('simulation_only_changes'),
        limits='Bridges originalVerilogsourcehashes only. Simulation-onlyprimitive/RAMtransformationsaredeclaredseparately; resource/P&R/constraints/bitstreamvalidityareseparateevidence.')

def canonical_trajectory(banks_a,traj_a,banks_b,traj_b):
    unique={}
    def ids(banks):
        out=[]
        for bank in banks:
            byte=bank.tobytes()
            if byte not in unique:unique[byte]=len(unique)
            out.append(unique[byte])
        return np.asarray(out)
    aa,bb=ids(banks_a),ids(banks_b)
    a=np.column_stack([aa[traj_a[:,0]],aa[traj_a[:,1]],traj_a[:,2]])
    b=np.column_stack([bb[traj_b[:,0]],bb[traj_b[:,1]],traj_b[:,2]])
    return int(np.count_nonzero(np.any(a!=b,axis=1)))

def frozen_source_bundle(capacity):
    folder=HERE/'rtl_validation/final_sources'/f'c{capacity}'
    manifest_path=folder/'source_manifest.json'
    if not manifest_path.exists():return dict(status='NOT_AVAILABLE')
    manifest=bench.read(manifest_path);records=[]
    for row in manifest['files']:
        file=folder/row['path'];measured=bench.sha(file) if file.exists() else None
        records.append(dict(path=row['path'],expected_sha256=row['sha256'],measured_sha256=measured,
                            match=measured==row['sha256']))
    original_gpr=folder/'original_project.gprj';relocated=folder/'pro_v2.gprj'
    original_match=bench.sha(original_gpr)==manifest['original_gpr_sha256']
    relocated_match=bench.sha(relocated)==manifest['relocated_gpr_sha256']
    return dict(status='PASS' if records and all(r['match'] for r in records) and original_match and relocated_match else 'FAIL',
        capacity=capacity,manifest_path=str(manifest_path),manifest_sha256=bench.sha(manifest_path),
        original_project_sha256=bench.sha(original_gpr),relocated_project_sha256=bench.sha(relocated),
        original_project_manifest_match=original_match,relocated_project_manifest_match=relocated_match,
        files_checked=len(records),files=records)

def one(case,suffix):
    folder=HERE/'rtl_validation/results'/(case+'_'+suffix)
    old=HERE/'rtl_validation/results'/(case+'_basis_packed')
    if not (folder/'verification.json').exists():return dict(case=case,status='PENDING_EXPORT')
    fixture=HERE/'fixtures'/case;metadata=bench.read(folder/'verification.json')
    assert metadata['verification']['status']=='PASS' and metadata['verification']['mixed_output_bit_mismatches']==0
    manifest=bench.read(folder/'build_manifest.json');identity=source_bridge(folder)
    with np.load(folder/'frozen_operator_and_components.npz') as z:final={k:z[k].copy() for k in z.files}
    with np.load(old/'frozen_operator_and_components.npz') as z:prior={k:z[k].copy() for k in z.files}
    assert final['component_names'].tolist()==prior['component_names'].tolist()
    errors={name:int(np.count_nonzero(final[name]!=prior[name])) for name in ('actual_output','input_clocks','output_clocks')}
    relative_clock={name:int(np.count_nonzero((final[name]-final['input_clocks'][0])!=(prior[name]-prior['input_clocks'][0])))
                    for name in ('input_clocks','output_clocks')}
    rows_equal=np.array_equal(final['component_input_counts'],prior['component_input_counts'])
    trajectory_mismatches=canonical_trajectory(final['banks_q16'],final['trajectory'],prior['banks_q16'],prior['trajectory'])
    component_max_error=float(np.max(abs(final['component_output_counts']-prior['component_output_counts'])))
    logs={}
    for filename in ('events.csv','observations.csv','cfg_snapshots.csv'):
        a,b=table(folder/filename),table(old/filename)
        logs[filename]=dict(records=len(a),identical_records=a==b)
    state=bench.read(folder/'run_state.json');fixture_sha=bench.sha(fixture/'input.npy')
    assert fixture_sha==state['input_sha256']
    passing=(identity['status']=='PASS' and not any(errors.values()) and not any(relative_clock.values())
             and rows_equal and trajectory_mismatches==0 and component_max_error==0
             and all(v['identical_records'] for v in logs.values()))
    return dict(case=case,status='PASS' if passing else 'FAIL',final_export_suffix=suffix,
        samples=len(final['actual_output']),input_sha256=fixture_sha,source_identity=identity,
        output_and_absolute_clock_mismatches=errors,relative_clock_mismatches=relative_clock,
        exact_component_inputs_equal=rows_equal,exact_per_sample_coefficient_alpha_trajectory_mismatches=trajectory_mismatches,
        exact_component_outputs_max_difference_counts=component_max_error,actual_event_and_observation_logs=logs,
        final_archive_sha256=bench.sha(folder/'frozen_operator_and_components.npz'),
        compared_packed_archive_sha256=bench.sha(old/'frozen_operator_and_components.npz'),
        state_sha256=bench.sha(folder/'run_state.json'),verification_sha256=bench.sha(folder/'verification.json'))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--suffix',default='service_final')
    parser.add_argument('--output',type=Path,default=HERE/'rtl_scores/final/source_identity.json');a=parser.parse_args()
    records=[one(c,a.suffix) for c in CASES]
    bundles=[frozen_source_bundle(cap) for cap in (3,4)]
    status='PASS' if all(r['status']=='PASS' for r in records+bundles) else ('FAIL' if any(r['status']=='FAIL' for r in records+bundles) else 'PENDING')
    result=dict(status=status,cases=records,actual_samples_checked=sum(r.get('samples',0) for r in records),
        frozen_source_bundles=bundles,
        script_sha256=bench.sha(Path(__file__)),
        scope='Final actualcompleteRTLtraces independentlyread andcomparedtopriorpacked; everyfullsynthesisVerilogsource matchedagainstfinalsimulationmanifest. Not aP&Rornewbitstreamclaim.')
    a.output.parent.mkdir(parents=True,exist_ok=True);bench.write(a.output,result)
    print(json.dumps(dict(status=status,cases=[dict(case=r['case'],status=r['status']) for r in records],actual_samples_checked=result['actual_samples_checked']),indent=2))

if __name__=='__main__':main()

"""Unmodified fixed1024 arithmetic on the new predeclared temporal input."""
from pathlib import Path
import argparse
import os
import sys
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
import numpy as np

HERE=Path(__file__).resolve().parent
RTL=HERE.parents[1]
sys.path.insert(0,str(RTL/'v28_controlled_update_work/software/comparison_1024'))
import fixed_baseline
import run_comparison as existing
bench=existing.bench


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--heldout-authorized',action='store_true');args=parser.parse_args()
    cases=['c4_temporal_gap2ms_dev']
    if args.heldout_authorized:cases.append('c4_temporal_gap8ms_heldout')
    records=[]
    for case in cases:
        fixture=HERE/'fixtures'/case;meta=bench.read(fixture/'fixture.json');x=np.load(fixture/'input.npy')
        assert meta['input_sha256']==bench.sha(fixture/'input.npy')
        output=HERE/'baseline_results'/case;output.mkdir(parents=True,exist_ok=True)
        cache=output/'fixed_dtft_raw_extraction.json'
        if cache.exists():
            old=bench.read(cache);assert old['source_sha256']==bench.sha(Path(fixed_baseline.__file__)) and old['input_sha256']==meta['input_sha256']
            extracted=old['observations']
        else:
            extracted=fixed_baseline.extract_observations(x,'dtft_grid_q30')
            bench.write(cache,dict(source_sha256=bench.sha(Path(fixed_baseline.__file__)),input_sha256=meta['input_sha256'],observations=extracted))
        with np.load(fixture/'components.npz') as z:names=z.files;rows=np.vstack([z[name]*32768 for name in names])
        assert np.max(abs(rows.sum(axis=0)-x))<1e-8
        for db in (21,18):
            stem='conventional1024_fixed'+str(db)+'_dtft'
            decisions=fixed_baseline.detect_updates(x,fixed_baseline.Config(max_centers=4,threshold_db=db),extracted)
            result,details=existing.replay(rows,x,decisions)
            bench.write(output/(stem+'_decisions.json'),decisions)
            bench.write(output/(stem+'_bank_diagnostics.json'),details)
            np.savez_compressed(output/(stem+'.npz'),input=x,output=result['output'],component_names=names,
                component_output_counts=result['components'],banks_q16=result['banks'],trajectory=result['trajectory'])
            records.append(dict(case=case,threshold_db=db,updates=len(decisions['updates']),guards=details['guard_failures'],
                saturation=result['saturations'],archive_sha256=bench.sha(output/(stem+'.npz'))))
            print(records[-1],flush=True)
    bench.write(HERE/'baseline_results/manifest.json',dict(records=records,
        plan_sha256=bench.sha(HERE/'experiment_plan.json'),source_sha256=bench.sha(Path(fixed_baseline.__file__)),
        meaning='Offline reference only, unchanged integerDTFT/21main/18sensitivity/Q15FIR,zero compute/config service.'))


if __name__=='__main__':main()

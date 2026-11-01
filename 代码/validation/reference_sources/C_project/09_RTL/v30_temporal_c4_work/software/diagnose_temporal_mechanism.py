"""Describe frozen outputs/control; no new algorithm or counterfactual runs."""
from pathlib import Path
import csv
import json
import numpy as np
import score_selected as selected
bench=selected.bench
HERE=Path(__file__).resolve().parent


def main():
    partition=bench.read(HERE/'diagnosis/primary_partition_input_clock.json')
    rows=[]
    for case in selected.NEW:
        pair=next(p for p in partition['comparisons'] if p['case']==case)
        a,b=pair['categories_current'],pair['categories_baseline21']
        saved=sum(v['energy'] for v in b.values())-sum(v['energy'] for v in a.values())
        parts=[]
        for name in sorted(set(a)|set(b)):
            av=a.get(name,dict(energy=0))['energy'];bv=b.get(name,dict(energy=0))['energy']
            parts.append(dict(category=name,current_error_energy_counts2=av,reference_error_energy_counts2=bv,
                contribution_to_total_energy_saved_percent=100*(bv-av)/saved))
        folder=HERE/'baseline_results'/case
        decisions=bench.read(folder/'conventional1024_fixed21_dtft_decisions.json')
        meta=bench.read(selected.fixture(case)/'fixture.json')
        lo=max(e['start_sample'] for e in meta['events'])+1600
        hi=min(e['end_sample'] for e in meta['events'])-320
        updates=decisions['updates'];plateau=[u for u in updates if lo<=u['sample']<hi]
        four=[(x,y) for x,y in zip(updates[:-1],updates[1:]) if len(x['frequencies_hz'])==len(y['frequencies_hz'])==4]
        small=[(x,y) for x,y in four if max(abs(v-w) for v,w in zip(sorted(x['frequencies_hz']),sorted(y['frequencies_hz'])))<=3.90625]
        original=selected.folder(case)
        with np.load(original/'frozen_operator_and_components.npz') as z:clocks=z['input_clocks'].copy()
        with (original/'cfg_snapshots.csv').open(encoding='utf-8-sig',newline='') as file:configs=list(csv.DictReader(file))
        first_source=min(e['start_sample'] for e in meta['events']);origin=int(clocks[first_source])
        first_four=next((r for r in configs if int(r['mask'])==15),None)
        first_soft_four=next((u for u in updates if len(u['frequencies_hz'])==4),None)
        rows.append(dict(case=case,primary_saved_energy_partition=parts,
            original_actual_commits=len(configs),reference21_updates=len(updates),
            reference21_four_center_plateau=dict(start_sample=lo,stop_sample=hi,updates=len(plateau)),
            reference21_four_to_four_updates=len(four),reference21_four_to_four_all_changes_le1grid=len(small),
            first_actual_all4_config_ms=None if first_four is None else (int(first_four['clock'])-origin)/50000,
            first_reference21_all4_config_ms=None if first_soft_four is None else (first_soft_four['sample']-first_source)/32,
            first_all4_warning='A four-center configuration is not proof of accurate frequencies or completed useful suppression.',
            artifacts=dict(input_sha256=bench.sha(selected.fixture(case)/'input.npy'),
                decisions_sha256=bench.sha(folder/'conventional1024_fixed21_dtft_decisions.json'),
                actual_cfg_sha256=bench.sha(original/'cfg_snapshots.csv'))))
    bench.write(HERE/'diagnosis/temporal_mechanism.json',dict(rows=rows,source_sha256=bench.sha(Path(__file__)),
        partition_source_sha256=bench.sha(HERE/'diagnosis/primary_partition_input_clock.json'),
        conclusions=['Most energy reduction occurs during source-on transitions; steady residual distortion is not improved.',
            'The210 reference updates in development include mostly one-grid plateau changes, increasing transaction pressure.',
            'The independent input has zero plateau reference updates yet still shows primary benefit; update count alone cannot explain or prove the advantage.',
            'Observed queue overlap is functional evidence, not an isolated causal quality ablation.',
            'These are descriptive partitions of already-frozen outputs; no test-dependent rule or threshold is introduced.']))
    print(json.dumps(rows,indent=2))


if __name__=='__main__':main()

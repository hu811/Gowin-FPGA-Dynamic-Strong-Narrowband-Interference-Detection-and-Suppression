"""Independent development-only arithmetic audit of the unnormalized basis.

Supplied centers are coefficient-construction test inputs, not ground-truth
frequency feedthrough into a detector. No waveform detector is exercised here.
The seed,200 nominal cases and close-spacing stress list are fixed in source.
"""
from pathlib import Path
from fractions import Fraction
import csv, hashlib, json, sys
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path[:0]=[str(ROOT),str(ROOT/'hardware')]
import basis_probe
from fixed_builder import build_bank_fixed, cosq
from fir_replay import PROTOTYPE, COSINE, _correction

FS,STEP,DELAY=32000.,3.90625,320
SEED=26102082

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def put(path,obj):Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
def rne_fraction(v):
    sign=-1 if v<0 else 1;n=abs(v.numerator);d=v.denominator
    q,r=divmod(n,d)
    return sign*(q+int(2*r>d or (2*r==d and q&1)))

def independent_cos(index):
    # Separate quadrant/quarter-table formulation, no phase-word expression.
    q,address=divmod(int(index)%8192,2048)
    if q&1:address=2048-address
    value=0 if address==2048 else 65536 if address<=5 else int(COSINE[address])
    return -value if q in (1,2) else value

def independent_basis(grid):
    return np.asarray([rne_fraction(Fraction(2*int(p)*independent_cos((320-t)*grid),65536))
                       for t,p in enumerate(PROTOTYPE)],np.int64)

def independent_response(row,col):
    values=independent_basis(col)
    total=sum(int(v)*independent_cos((320-t)*row)*(1 if t==320 else 2)
              for t,v in enumerate(values))
    return rne_fraction(Fraction(total,4096))

def actual_response(h,frequencies):
    n=np.arange(641)-DELAY
    return np.asarray([np.dot(h.astype(float)/65536,np.cos(2*np.pi*f*n/FS)) for f in frequencies])

def diagnostics(h,frequencies,pairs):
    grids=[round(f/STEP) for f in frequencies];centers=np.asarray(grids)*STEP
    response=actual_response(h,centers);raw_response=actual_response(h,frequencies)
    middle=[.5*(centers[a]+centers[b]) for a,b in pairs]
    mid_response=actual_response(h,middle)
    return dict(quantized_centers_hz=centers.tolist(),center_residual_linear=response.tolist(),
        center_attenuation_db=(-20*np.log10(np.maximum(abs(response),1e-30))).tolist(),
        supplied_offgrid_residual_linear=raw_response.tolist(),
        supplied_offgrid_attenuation_db=(-20*np.log10(np.maximum(abs(raw_response),1e-30))).tolist(),
        midpoint_response_linear=mid_response.tolist(),
        midpoint_amplitude_change_db=(20*np.log10(np.maximum(abs(mid_response),1e-30))).tolist(),
        max_coefficient_abs=int(np.max(abs(h))),symmetric=bool(np.array_equal(h,h[::-1])))

def development_cases():
    rng=np.random.default_rng(SEED);cases=[]
    for cap in (3,4):
        for number in range(100):
            separation=125. if number<50 else float(rng.uniform(125,200))
            first=float(rng.uniform(1050,5700))
            if cap==3:
                options=[v for v in np.arange(1050.123,6900,17.371) if min(abs(v-first),abs(v-first-separation))>750]
                single=float(rng.choice(options)+rng.uniform(-.1,.1))
                fs=[first,first+separation,single];pairs=[(0,1)]
            else:
                # Sort cluster placement to guarantee an actual >750Hz gap.
                first=float(rng.uniform(1050,4800))
                second_sep=125. if number<50 else float(rng.uniform(125,200))
                second=float(rng.uniform(first+separation+751,6900-second_sep))
                fs=[first,first+separation,second,second+second_sep];pairs=[(0,1),(2,3)]
            assert min(fs)>=1000 and max(fs)<=7000
            assert all(abs(f/STEP-round(f/STEP))>1e-10 for f in fs)
            cases.append(dict(name=f'c{cap}_{number:03d}',capacity=cap,centers_hz=fs,pairs=pairs))
    return cases

def one_case(case):
    fs=case['centers_hz'];new,ni=basis_probe.build_bank_basis(fs,True);old,oi=build_bank_fixed(fs,True)
    dnew=diagnostics(new,fs,case['pairs']);dold=diagnostics(old,fs,case['pairs'])
    return dict(**case,new_basis=dnew,old_normalized_basis=dold,new_builder=ni,old_builder=oi,
        max_coefficient_difference_lsb=int(np.max(abs(new-old))),
        rms_coefficient_difference_lsb=float(np.sqrt(np.mean((new-old).astype(float)**2))),
        max_center_response_difference=float(np.max(abs(np.asarray(dnew['center_residual_linear'])-dold['center_residual_linear']))),
        max_midpoint_amplitude_difference_db=float(np.max(abs(np.asarray(dnew['midpoint_amplitude_change_db'])-dold['midpoint_amplitude_change_db']))))

def main():
    destination=HERE/'basis_audit_results';destination.mkdir(exist_ok=True)
    cases=development_cases()
    close=[0.,3.90625,15.625,31.25,46.875,62.5,78.125,93.75,109.375,121.09375,125.]
    plan=dict(seed=SEED,cases=cases,close_spacing_hz=close,
        scope='development-only coefficient arithmetic, not new independent dynamic performance test',
        no_detector_or_truth_use='Builders receive the same supplied estimated-center test values; no clean waveform or generator labels are inputs.',
        fixed_guards='unchanged: row infinity norm<.5,12 Q20 iterations,weight abs<=2,residual<=64LSB,18bit final coefficients')
    put(destination/'plan.json',plan)
    # Exhaustive shared cosine-grid correctness independently of basis_probe.
    cosine_errors=[i for i in range(8192) if cosq(i)!=independent_cos(i)]
    assert not cosine_errors
    sample_grids=sorted(set(round(f/STEP) for c in cases for f in c['centers_hz']))
    basis_mismatch=0;response_mismatch=0;rounding_ties=0;basis_values=0;max_scaled_difference=0.
    for grid in sample_grids:
        actual=basis_probe.basis_q16(grid);expected=independent_basis(grid)
        basis_mismatch+=int(np.count_nonzero(actual!=expected));basis_values+=len(actual)
        for t,p in enumerate(PROTOTYPE):
            num=2*int(p)*independent_cos((320-t)*grid)
            rounding_ties+=int(abs(num)%65536==32768)
        # Original normalization is one scalar per center before per-tap RNE.
        pcs=np.asarray([int(p)*independent_cos((320-t)*grid) for t,p in enumerate(PROTOTYPE)],np.int64)
        denom=sum(int(pc)*independent_cos((320-t)*grid)*(2 if t==320 else 4) for t,pc in enumerate(pcs))
        scalar=49*(2**32)/(50*denom)
        ideal_u=pcs.astype(float)*2/65536
        original=np.asarray(_correction(grid))
        max_scaled_difference=max(max_scaled_difference,float(np.max(abs(original-ideal_u*scalar*65536))))
    for c in cases:
        grids=[round(f/STEP) for f in c['centers_hz']]
        for row in grids:
            for col in grids:
                response_mismatch+=int(basis_probe.response_q20(row,col)!=independent_response(row,col))
    assert basis_mismatch==0 and response_mismatch==0
    records=[];failures=[]
    for c in cases:
        try:records.append(one_case(c))
        except (ArithmeticError,ValueError,OverflowError) as exc:
            failures.append(dict(case=c,type=type(exc).__name__,message=str(exc)))
    close_records=[]
    for separation in close:
        fs=[2789.1,2789.1+separation]
        record=dict(separation_hz=separation,quantized_separation_hz=(round(fs[1]/STEP)-round(fs[0]/STEP))*STEP)
        for name,builder in [('new_basis',basis_probe.build_bank_basis),('old_basis',build_bank_fixed)]:
            try:
                h,info=builder(fs,True)
                record[name]=dict(status='accepted',info=info,response=diagnostics(h,fs,[(0,1)]))
            except (ArithmeticError,ValueError,OverflowError) as exc:record[name]=dict(status='rejected',error=str(exc))
        close_records.append(record)
    # Inputs outside the declared capacity must fail closed.
    try:basis_probe.build_bank_basis([1000,2000,3000,4000,5000]);raise AssertionError('capacity guard missing')
    except ValueError as exc:capacity_guard=str(exc)
    new=[r['new_basis'] for r in records]
    exact125=[r for r in records if all(abs(r['centers_hz'][b]-r['centers_hz'][a]-125)<1e-8 for a,b in r['pairs'])]
    summary=dict(nominal_cases=len(cases),nominal_successes=len(records),nominal_failures=len(failures),
        cosine_grid_values_checked=8192,cosine_mismatches=len(cosine_errors),basis_grids_checked=len(sample_grids),
        basis_values_checked=basis_values,basis_rne_mismatches=basis_mismatch,response_q20_mismatches=response_mismatch,
        exercised_exact_rounding_ties=rounding_ties,
        minimum_new_center_attenuation_db=min(v for r in new for v in r['center_attenuation_db']),
        minimum_new_supplied_offgrid_attenuation_db=min(v for r in new for v in r['supplied_offgrid_attenuation_db']),
        maximum_new_midpoint_abs_change_db=max(abs(v) for r in new for v in r['midpoint_amplitude_change_db']),
        maximum_new_vs_old_coefficient_difference_lsb=max(r['max_coefficient_difference_lsb'] for r in records),
        maximum_new_vs_old_center_response_difference=max(r['max_center_response_difference'] for r in records),
        maximum_new_vs_old_midpoint_amplitude_difference_db=max(r['max_midpoint_amplitude_difference_db'] for r in records),
        maximum_new_row_norm_q20=max(r['new_builder']['row_norm_q20'] for r in records),
        maximum_new_weight_abs_q20=max(abs(v) for r in records for v in r['new_builder']['weights_q20']),
        maximum_new_iteration_residual_q20=max(abs(v) for r in records for v in r['new_builder']['residual_q20']),
        maximum_new_coefficient_abs=max(r['new_basis']['max_coefficient_abs'] for r in records),
        all_new_coefficients_symmetric=all(r['new_basis']['symmetric'] for r in records),capacity_guard=capacity_guard,
        exact125_cases=len(exact125),
        exact125_maximum_new_midpoint_abs_change_db=max(abs(v) for r in exact125 for v in r['new_basis']['midpoint_amplitude_change_db']),
        exact125_maximum_old_midpoint_abs_change_db=max(abs(v) for r in exact125 for v in r['old_normalized_basis']['midpoint_amplitude_change_db']),
        nominal_cases_new_midpoint_exceeds_1db=sum(any(abs(v)>1 for v in r['new_basis']['midpoint_amplitude_change_db']) for r in records),
        nominal_cases_old_midpoint_exceeds_1db=sum(any(abs(v)>1 for v in r['old_normalized_basis']['midpoint_amplitude_change_db']) for r in records),
        old_ideal_scalar_after_quantization_max_error_lsb=max_scaled_difference,
        notes=['Changing basis preserves the unquantized real span only; per-tap Q16 RNE and finite Q20 iteration change final coefficients.',
               'Response_q20 divides integerQ32 response by4096 to obtainQ20. This is independent of the old normalization denominator.',
               'Numerical contraction guards do not implement an explicit125Hz spacing guard; some closer cases can pass numerically and damage midpoint targets.',
               'No resource, service-cycle or complete-output dynamic claim follows from these development-only coefficient tests.'])
    results=dict(summary=summary,records=records,failures=failures,close_spacing=close_records,
        sources_sha256={str(p):sha(p) for p in [Path(__file__),ROOT/'basis_probe.py',ROOT/'hardware/fixed_builder.py']},
        plan_sha256=sha(destination/'plan.json'))
    put(destination/'results.json',results)
    with (destination/'nominal.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['case','capacity','minimum_center_attenuation_db','minimum_offgrid_attenuation_db','worst_midpoint_abs_change_db','max_coefficient_difference_lsb','max_midpoint_difference_db','row_norm_q20','max_residual_q20']);w.writeheader()
        for r in records:w.writerow(dict(case=r['name'],capacity=r['capacity'],minimum_center_attenuation_db=min(r['new_basis']['center_attenuation_db']),minimum_offgrid_attenuation_db=min(r['new_basis']['supplied_offgrid_attenuation_db']),worst_midpoint_abs_change_db=max(abs(v) for v in r['new_basis']['midpoint_amplitude_change_db']),max_coefficient_difference_lsb=r['max_coefficient_difference_lsb'],max_midpoint_difference_db=r['max_midpoint_amplitude_difference_db'],row_norm_q20=r['new_builder']['row_norm_q20'],max_residual_q20=max(abs(v) for v in r['new_builder']['residual_q20'])))
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print('Close-spacing status:',[(r['separation_hz'],r['new_basis']['status']) for r in close_records])

if __name__=='__main__':main()

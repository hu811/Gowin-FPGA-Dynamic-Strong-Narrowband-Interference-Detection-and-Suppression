"""Local 1024 auxiliary correction from prior strong authorization only.

No source labels. Retains original power floor, strict local maximum and log
interpolation; removes auxiliary background gate, full-band scan and top-5.
"""
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent;BASE=Path('D:/FPGA_Competition/pro_v2_resource_v7')
sys.path.insert(0,str(BASE/'model'))
import aux_reference as old

def detect(power, authorized_grids):
    power=[int(v) for v in power]
    if len(power)!=513 or any(v<0 or v>=1<<64 for v in power):raise ValueError('513-bin unsigned64 input required')
    result=[];audits=[]
    for slot,grid in authorized_grids:
        # Native1024 bin uses nearest-even, same grid convention as original.
        k0=round(int(grid)/8);choices=[]
        for k in range(max(32,k0-1),min(224,k0+1)+1):
            peak=power[k]
            if peak>power[k-1] and peak>=power[k+1] and peak>=old.MIN_POWER:
                choices.append((peak,-k,k))
        if not choices:
            audits.append(dict(slot=slot,authorization_grid=grid,nearest_bin=k0,selected_bin=None));continue
        _,_,k=max(choices);left,center,right=[old.log2_power(power[b]) for b in (k-1,k,k+1)]
        denominator=2*center-left-right
        if denominator<=0:raise old.NumericFailure('nonpositive local log-parabola denominator')
        offset=max(-4,min(4,old.divide_rne(4*(right-left),denominator)))
        out_grid=k*8+offset
        result.append(dict(bin=k,grid=out_grid,power=power[k],frequency_hz=out_grid*3.90625,fcw=out_grid<<19,slot=slot))
        audits.append(dict(slot=slot,authorization_grid=grid,nearest_bin=k0,selected_bin=k,output_grid=out_grid))
    return result,audits

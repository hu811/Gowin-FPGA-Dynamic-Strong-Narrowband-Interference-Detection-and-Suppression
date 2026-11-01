"""1024 auxiliary _simple detector, with exact power-rank/amplitude median.

The raw power is Q58. Only the middle two powers are square-rooted, which
preserves the median of amplitudes (it does not substitute a power median).
The 3-point log parabola uses Q22 log2, 8-bit ROM plus 12-bit interpolation.
"""
from pathlib import Path
import math
import numpy as np

Q = 1 << 29
LOG_Q = 1 << 22
MIN_POWER = math.ceil(.035**2 * (1 << 58))
AMP_FLOOR = round(1e-8 * Q)
ZERO_LOG = round(math.log2(1e-30 * (1 << 58)) * LOG_Q)
ROOT = Path(__file__).resolve().parents[1]


class NumericFailure(ValueError):
    pass


def rne(value, shift):
    base, remainder = divmod(int(value), 1 << shift)
    half = 1 << (shift - 1)
    return base + int(remainder > half or (remainder == half and (base & 1)))


def sqrt_rne(power):
    power = int(power)
    root = math.isqrt(power)
    root += int(power - root * root > root)
    if root >= 1 << 32:
        raise NumericFailure('sqrt32 overflow')
    return root


def log_rom():
    return np.rint(np.log2(1 + np.arange(257) / 256) * LOG_Q).astype(np.int64)


def generate_rom(root=ROOT):
    root=Path(root);(root/'rom').mkdir(parents=True,exist_ok=True)
    table=np.pad(log_rom(),(0,512-257))
    (root/'rom/aux_log2_q22.mem').write_text(''.join(f'{int(v):024b}\n' for v in table),encoding='ascii')
    low=table&((1<<18)-1)
    thresholds=(12,24,36,49,62,76,91,107,123,139,157,175,194,214,235,256)
    high=np.asarray([sum(i>=value for value in thresholds) if i<=256 else 0 for i in range(512)],dtype=np.int64)
    assert np.array_equal((high<<18)|low,table),'Exact split log ROM mismatch'
    (root/'rom/aux_log2_low18.mem').write_text(''.join(f'{int(v):018b}\n' for v in low),encoding='ascii')
    pairs=np.asarray([(int(low[i+1])<<18)|int(low[i]) for i in range(256)],dtype=np.int64)
    assert all((int(pairs[i])&((1<<18)-1))==int(low[i]) and (int(pairs[i])>>18)==int(low[i+1]) for i in range(256))
    (root/'rom/aux_log2_pair36.mem').write_text(''.join(f'{int(v):036b}\n' for v in pairs),encoding='ascii')


def log2_power(power):
    power=int(power)
    if power==0:return ZERO_LOG
    exponent=power.bit_length()-1
    normalized=power << (63-exponent)
    index=(normalized>>55)&255
    fraction=(normalized>>43)&4095
    table=log_rom()
    return exponent*LOG_Q+int(table[index])+rne((int(table[index+1])-int(table[index]))*fraction,12)


def divide_rne(numerator, denominator):
    if denominator<=0:raise NumericFailure('nonpositive log-parabola denominator')
    sign=-1 if numerator<0 else 1
    q,r=divmod(abs(int(numerator)),int(denominator))
    q+=int(2*r>denominator or (2*r==denominator and q&1))
    return sign*q


def detect(power):
    power=[int(v) for v in power]
    if len(power)!=513 or any(v<0 or v>=1<<64 for v in power):
        raise ValueError('one 513-bin unsigned64 spectrum required')
    candidates=[]
    for k in range(32,225):
        peak=power[k]
        if not (peak>power[k-1] and peak>=power[k+1] and peak>=MIN_POWER):continue
        side=sorted(power[k-8:k-2]+power[k+3:k+9])
        # Twice the amplitude median, in Q29; same lower amplitude floor.
        median_sum=max(sqrt_rne(side[5])+sqrt_rne(side[6]),2*AMP_FLOOR)
        if median_sum>=1<<32:raise NumericFailure('median sum32 overflow')
        if 4*peak<10*median_sum*median_sum:continue
        left,center,right=[log2_power(power[b]) for b in (k-1,k,k+1)]
        denominator=2*center-left-right
        numerator=4*(right-left)
        offset=max(-4,min(4,divide_rne(numerator,denominator)))
        candidates.append(dict(bin=k,grid=k*8+offset,power=peak))
    # Python stable sort preserves ascending native bin for equal peak power.
    result=sorted(candidates,key=lambda c:-c['power'])[:5]
    for c in result:
        c['amplitude_q29']=sqrt_rne(c['power'])
        c['frequency_hz']=c['grid']*3.90625
        c['fcw']=c['grid']<<19
    return result


def float_from_power(power):
    amp=np.sqrt(np.asarray(power,dtype=float)/(1<<58))
    found=[]
    for k in range(32,225):
        if not (amp[k]>amp[k-1] and amp[k]>=amp[k+1]):continue
        side=np.r_[amp[k-8:k-2],amp[k+3:k+9]]
        floor=max(float(np.median(side)),1e-8)
        contrast=20*np.log10(max(amp[k],1e-12)/floor)
        if amp[k]<.035 or contrast<10:continue
        a,b,c=np.log(np.maximum(amp[k-1:k+2],1e-15))
        off=np.clip(.5*(a-c)/(a-2*b+c),-.5,.5)
        grid=int(np.rint((k+off)*8))
        found.append(dict(bin=k,grid=grid,frequency_hz=grid*3.90625,power=int(power[k])))
    return sorted(found,key=lambda c:-c['power'])[:5]

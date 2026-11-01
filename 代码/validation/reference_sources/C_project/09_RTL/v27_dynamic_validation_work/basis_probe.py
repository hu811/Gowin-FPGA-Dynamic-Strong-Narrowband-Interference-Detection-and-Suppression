"""Remove redundant individual normalization before joint response balancing.

The new basis is2*prototype*cos, quantized once toQ16. The response matrix is
built from that actual basis, so its joint weights still enforce the specified
response. This is a coefficient-arithmetic change, not bitwise equivalence to
the original individually normalized basis. All complete outputs are compared.
"""
from functools import lru_cache
from pathlib import Path
import sys
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'hardware'))
from fixed_builder import cosq
from fir_replay import PROTOTYPE, round_div_even, quantized_frequencies


@lru_cache(maxsize=8192)
def basis_q16(grid):
    # Prototype full sum65544 is almost65536;2*p*cos has own-center response
    # close to1. Joint response balancing, not this approximate sum, normalizes.
    return np.asarray([round_div_even(2*int(p)*cosq((320-t)*grid),65536)
                       for t,p in enumerate(PROTOTYPE)],dtype=np.int64)


@lru_cache(maxsize=32768)
def response_q20(row,col):
    b = basis_q16(col)
    total = sum(int(b[t])*cosq((320-t)*row)*(1 if t==320 else 2) for t in range(321))
    return round_div_even(total,4096)


def build_bank_basis(frequencies_hz, return_diagnostics=False):
    frequencies = quantized_frequencies(frequencies_hz)
    if len(frequencies)>4:
        raise ValueError('unsupported_capacity')
    grids = [round(f/3.90625) for f in frequencies]
    n = len(grids)
    if not n:
        half = np.zeros(321,np.int64)
        half[320] = 65536
        h = np.r_[half,half[-2::-1]]
        info = dict(centers_hz=[],weights_q20=[],row_norm_q20=0,residual_q20=[],status='ok')
        return (h,info) if return_diagnostics else h
    g = np.asarray([[response_q20(i,j) for j in grids] for i in grids],dtype=np.int64)
    target = np.full(n,1<<20,dtype=np.int64)
    row_norm = int(np.max(np.sum(abs(np.diag(target)-g),axis=1)))
    if row_norm >= (1<<19):
        raise ArithmeticError(f'noncontractive_response row_norm_q20={row_norm}')
    def dot(w):
        return np.asarray([round_div_even(sum(int(a)*int(b) for a,b in zip(row,w)),1<<20)
                           for row in g],dtype=np.int64)
    weights = target.copy()
    for _ in range(12):
        weights += target-dot(weights)
        if np.max(abs(weights))>(2<<20):
            raise ArithmeticError('weight_overflow')
    residual = target-dot(weights)
    if np.max(abs(residual))>64:
        raise ArithmeticError(f'unconverged_response residual={residual.tolist()}')
    bases = np.asarray([basis_q16(grid) for grid in grids],dtype=np.int64)
    half = np.asarray([-sum(round_div_even(int(bases[j,t])*int(weights[j]),1<<20)
                            for j in range(n)) for t in range(321)],dtype=np.int64)
    half[320] += 65536
    if np.min(half)<-131072 or np.max(half)>131071:
        raise ArithmeticError('coefficient_overflow')
    h = np.r_[half,half[-2::-1]]
    info = dict(centers_hz=frequencies,weights_q20=weights.tolist(),row_norm_q20=row_norm,
                residual_q20=residual.tolist(),G_q20=g.tolist(),status='ok',
                basis='RNE(2*prototypeQ16*cosQ16/65536), no individual amplitude normalization')
    return (h,info) if return_diagnostics else h


if __name__=='__main__':
    from run_fixed_comparison import run
    run(build_bank_basis,'basis_comparison',Path(__file__))

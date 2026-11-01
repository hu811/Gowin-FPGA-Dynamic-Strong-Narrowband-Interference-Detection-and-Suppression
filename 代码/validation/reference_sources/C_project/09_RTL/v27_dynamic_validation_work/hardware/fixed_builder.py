"""Q20 response/weight model of overlap_backend.v; no true labels accepted.

Unsupported response matrices raise a named error. The online caller retains
its old authorized bank; this function never silently substitutes a new bank.
"""
from functools import lru_cache
from pathlib import Path
import sys
import numpy as np
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/'v23_conventional_comparison_work'))
from fir_replay import _correction, COSINE, round_div_even, quantized_frequencies

def cosq(index):
 phase=(int(index)&8191)<<19
 angle=0x40000000-(phase&0x3fffffff) if phase&0x40000000 else phase&0x3fffffff
 address=(angle>>19)&2047
 magnitude=0 if angle&0x40000000 else 65536 if address<=5 else COSINE[address]
 return -magnitude if bool(((phase>>31)^(phase>>30))&1) else magnitude

@lru_cache(maxsize=32768)
def response_q20(row_grid,col_grid):
 corr=_correction(int(col_grid))
 total=sum(int(corr[t])*cosq((320-t)*int(row_grid))*(1 if t==320 else 2) for t in range(321))
 return round_div_even(total,4096)

def build_bank_fixed(frequencies_hz,return_diagnostics=False):
 centers=quantized_frequencies(frequencies_hz)
 if len(centers)>4:raise ValueError('unsupported_capacity')
 grids=[round(f/3.90625) for f in centers];N=len(grids)
 if not N:
  half=np.zeros(321,np.int64);half[320]=65536
  h=np.r_[half,half[-2::-1]]
  info=dict(centers_hz=[],weights_q20=[],row_norm_q20=0,residual_q20=[],status='ok')
  return (h,info) if return_diagnostics else h
 G=np.array([[response_q20(i,j) for j in grids] for i in grids],np.int64)
 target=np.full(N,1<<20,dtype=np.int64)
 norm=int(np.max(np.sum(abs(np.diag(target)-G),axis=1)))
 if norm>=1<<19:raise ArithmeticError(f'noncontractive_response row_norm_q20={norm}')
 w=target.copy()
 def dot(w):return np.array([round_div_even(sum(int(a)*int(b) for a,b in zip(row,w)),1<<20) for row in G],np.int64)
 for _ in range(12):
  w=w+target-dot(w)
  if max(abs(w))>2<<20:raise ArithmeticError('weight_overflow')
 residual=target-dot(w)
 if max(abs(residual))>64:raise ArithmeticError(f'unconverged_response residual_q20={residual.tolist()}')
 corr=np.array([_correction(g) for g in grids],np.int64)
 half=np.array([-sum(round_div_even(int(corr[j,t])*int(w[j]),1<<20) for j in range(N)) for t in range(321)],np.int64)
 half[320]+=65536
 if np.min(half)<-131072 or np.max(half)>131071:raise ArithmeticError('coefficient_overflow')
 h=np.r_[half,half[-2::-1]]
 info=dict(centers_hz=centers,weights_q20=w.tolist(),row_norm_q20=norm,residual_q20=residual.tolist(),G_q20=G.tolist(),status='ok')
 return (h,info) if return_diagnostics else h

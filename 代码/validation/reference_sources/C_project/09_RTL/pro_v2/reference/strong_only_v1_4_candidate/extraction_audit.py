"""Literal instrumentation of archived estimator; tested for exact output parity."""
from copy import deepcopy
from functools import lru_cache
import numpy as np
from dependencies import legacy
_bank, _simple = legacy._bank, legacy._simple
FS, GRID, BAND, SETTINGS = legacy.FS, legacy.GRID, legacy.BAND, legacy.SETTINGS

def _audited(samples, refine=True):
    """At most five tones, <=5 extraction passes and three fixed refinements.

    Each extraction searches +/-0.75 native FFT bin around the largest residual
    bin; each refinement searches +/-0.5 bin around the current estimate. These
    are finite grid comparisons and scalar projections, not nonlinear fitting.
    """
    if not refine: return _simple(np.asarray(samples)), {}
    audit = dict(extraction_attempts=[], fitted_before_final_filter=[])
    n = len(samples)
    win, frequencies, bank = _bank(n)
    spectrum = np.fft.rfft(np.asarray(samples)*win)/win.sum()
    bins = np.arange(len(spectrum))
    inband = (bins*FS/n >= BAND[0]) & (bins*FS/n <= BAND[1])
    residue = spectrum.copy()
    indices, coeff = [], []
    search_half = max(1, int(np.ceil(.75*FS/n/GRID)))

    def projection(center, half, residual, exclude):
        selected = np.arange(max(0,center-half),min(len(frequencies),center+half+1))
        if exclude:
            selected = selected[np.all(abs(frequencies[selected,None]-frequencies[np.asarray(exclude)][None,:]) >= .80*FS/n,axis=1)]
        if len(selected)==0: return None
        center_bin = int(round(frequencies[center]*n/FS))
        lo,hi=max(0,center_bin-8),min(len(spectrum),center_bin+9)
        templates = bank[selected,lo:hi]
        norm=np.einsum('ij,ij->i',templates.conj(),templates).real
        dot=templates.conj()@residual[lo:hi]
        score=abs(dot)**2/np.maximum(norm,1e-30)
        best=int(np.argmax(score))
        return int(selected[best]),dot[best]/norm[best]

    for _ in range(SETTINGS["max_tracks"]):
        search=np.where(inband,abs(residue),0.)
        for i in indices:
            search[abs(bins*FS/n-frequencies[i])<.8*FS/n]=0
        k=int(np.argmax(search))
        center=int(np.clip(round((k*FS/n-BAND[0])/GRID),0,len(frequencies)-1))
        fit=projection(center,search_half,residue,indices)
        if fit is None: break
        i,a=fit
        audit["extraction_attempts"].append(dict(frequency_hz=float(frequencies[i]), amplitude=float(2*abs(a)), stopped_by035=bool(2*abs(a)<SETTINGS["min_amp"])))
        if 2*abs(a)<SETTINGS["min_amp"]: break
        indices.append(i);coeff.append(a)
        residue-=a*bank[i]

    for _ in range(SETTINGS["refinement_sweeps"]):
        for p in range(len(indices)):
            residue+=coeff[p]*bank[indices[p]]
            fit=projection(indices[p],max(1,search_half*2//3),residue,indices[:p]+indices[p+1:])
            if fit is not None: indices[p],coeff[p]=fit
            residue-=coeff[p]*bank[indices[p]]

    # Contrast is measured against the unmodelled background, never clean truth.
    found=[]
    for i,a in zip(indices,coeff):
        k=int(round(frequencies[i]*n/FS))
        side=np.r_[residue[max(1,k-12):max(1,k-3)],residue[k+4:min(len(residue),k+13)]]
        floor=max(float(np.median(abs(side)))*2,1e-8)
        amp=float(2*abs(a));contrast=float(20*np.log10(max(amp,1e-12)/floor))
        audit["fitted_before_final_filter"].append(dict(frequency_hz=float(frequencies[i]), amplitude=amp, contrast_db=contrast, blocked035=bool(amp<SETTINGS["min_amp"]), blocked10=bool(contrast<SETTINGS["contrast_db"])))
        if amp<SETTINGS["min_amp"] or contrast<SETTINGS["contrast_db"]:continue
        found.append({"frequency_hz":float(frequencies[i]),"amplitude":amp,
                      "contrast_db":contrast})
    return sorted(found,key=lambda x:x["frequency_hz"]), audit


@lru_cache(maxsize=1024)
def _cached(payload, refine):
    return _audited(np.frombuffer(payload,dtype=np.float64), refine)

def estimate_with_audit(samples, refine=True):
    x=np.ascontiguousarray(samples,dtype=np.float64)
    return deepcopy(_cached(x.tobytes(), bool(refine)))

def estimate_peaks(samples, refine=True):
    return estimate_with_audit(samples, refine)[0]

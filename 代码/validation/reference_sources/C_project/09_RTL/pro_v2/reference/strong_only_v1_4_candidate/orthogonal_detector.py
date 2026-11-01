"""Bounded orthogonal matching pursuit with joint frequency/amplitude refits.

All observations come from the same causal 512-point mixed-input FFT. Each
frequency trial accounts for the other fitted templates through its Schur
complement; it does not project on an approximately subtracted single tone.
No true frequency, clean component or subsequent window is an input.
"""
import numpy as np
from short_detector import _bank, BAND, FS, GRID, SETTINGS

MODE = 'orthogonal_joint_v1'


def estimate_with_audit(samples):
    x=np.asarray(samples,dtype=float)
    if x.ndim!=1 or len(x)!=512 or not np.isfinite(x).all():
        raise ValueError('one finite 512-sample mixed window required')
    n=len(x); win,frequencies,bank=_bank(n)
    with np.errstate(over='raise',invalid='raise',divide='raise'):
        spectrum=np.fft.rfft(x*win)/win.sum()
    bins=np.arange(len(spectrum)); bin_hz=bins*FS/n
    inband=(bin_hz>=BAND[0])&(bin_hz<=BAND[1])
    indices=[]; coeff=np.empty(0,dtype=complex); residue=spectrum.copy()
    search_half=max(1,int(np.ceil(.75*FS/n/GRID)))
    audit=dict(model=MODE,extraction_attempts=[],fitted_before_final_filter=[],
               joint_solves=0,coordinate_searches=0,max_model_centers=5)

    def joint_fit(current):
        if not current: return np.empty(0,dtype=complex),spectrum.copy()
        templates=bank[current]
        gram=templates.conj()@templates.T
        rhs=templates.conj()@spectrum
        amplitudes=np.linalg.solve(gram,rhs)
        residual=spectrum-amplitudes@templates
        if not np.isfinite(amplitudes).all() or not np.isfinite(residual).all():
            raise FloatingPointError('invalid joint fit')
        audit['joint_solves']+=1
        return amplitudes,residual

    def projection(center,half,others):
        selected=np.arange(max(0,center-half),min(len(frequencies),center+half+1))
        if others:
            selected=selected[np.all(abs(frequencies[selected,None]-frequencies[np.asarray(others)][None,:])
                                     >=.80*FS/n,axis=1)]
        if not len(selected): return None
        templates=bank[selected]
        norm=np.einsum('ij,ij->i',templates.conj(),templates).real
        if others:
            other=bank[others]
            gram=other.conj()@other.T
            fitted=np.linalg.solve(gram,other.conj()@spectrum)
            residual=spectrum-fitted@other
            cross=other.conj()@templates.T
            correction=np.linalg.solve(gram,cross)
            orthogonal_norm=norm-np.einsum('ij,ij->j',cross.conj(),correction).real
        else:
            residual=spectrum; orthogonal_norm=norm
        dot=templates.conj()@residual
        valid=(orthogonal_norm>1e-12*np.maximum(norm,1e-30)) & np.isfinite(orthogonal_norm)
        score=np.where(valid,abs(dot)**2/np.maximum(orthogonal_norm,1e-30),-np.inf)
        best=int(np.argmax(score))
        if not valid[best]: return None
        audit['coordinate_searches']+=1
        return int(selected[best]),dot[best]/orthogonal_norm[best]

    for _ in range(SETTINGS['max_tracks']):
        search=np.where(inband,abs(residue),0.)
        for index in indices: search[abs(bin_hz-frequencies[index])<.8*FS/n]=0.
        k=int(np.argmax(search))
        center=int(np.clip(round((k*FS/n-BAND[0])/GRID),0,len(frequencies)-1))
        fit=projection(center,search_half,indices)
        if fit is None: break
        index,a=fit
        audit['extraction_attempts'].append(dict(frequency_hz=float(frequencies[index]),
            amplitude=float(2*abs(a)),stopped_by035=bool(2*abs(a)<SETTINGS['min_amp'])))
        if 2*abs(a)<SETTINGS['min_amp']: break
        indices.append(index)
        coeff,residue=joint_fit(indices)
        # Refit BEFORE the next candidate: otherwise subtraction error creates
        # a fake third candidate which can hijack the later frequency searches.
        for _ in range(SETTINGS['refinement_sweeps']):
            for p in range(len(indices)):
                fit=projection(indices[p],max(1,search_half*2//3),indices[:p]+indices[p+1:])
                if fit is not None: indices[p]=fit[0]
            coeff,residue=joint_fit(indices)
    found=[]
    for index,a in zip(indices,coeff):
        k=int(round(frequencies[index]*n/FS))
        side=np.r_[residue[max(1,k-12):max(1,k-3)],residue[k+4:min(len(residue),k+13)]]
        floor=max(float(np.median(abs(side)))*2,1e-8)
        amp=float(2*abs(a)); contrast=float(20*np.log10(max(amp,1e-12)/floor))
        audit['fitted_before_final_filter'].append(dict(frequency_hz=float(frequencies[index]),
            amplitude=amp,contrast_db=contrast,blocked035=bool(amp<SETTINGS['min_amp']),
            blocked10=bool(contrast<SETTINGS['contrast_db'])))
        if amp>=SETTINGS['min_amp'] and contrast>=SETTINGS['contrast_db']:
            found.append(dict(frequency_hz=float(frequencies[index]),amplitude=amp,contrast_db=contrast))
    return sorted(found,key=lambda p:p['frequency_hz']),audit

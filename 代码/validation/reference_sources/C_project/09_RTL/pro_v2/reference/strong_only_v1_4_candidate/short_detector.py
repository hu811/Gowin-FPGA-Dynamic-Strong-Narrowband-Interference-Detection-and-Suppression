"""Frozen spectral primitives ONLY; old two-confirmation controller omitted."""
from functools import lru_cache
import numpy as np

FS = 32000


GRID = 3.90625


BAND = (1000., 7000.)


SETTINGS = {"min_amp": .035, "contrast_db": 10., "max_tracks": 5,
            "max_groups": 3,
            "confirm_frames": 2, "miss_frames": 2, "match_hz": 70.,
            "stable_hz": 7.8125, "group_hz": 750., "refinement_sweeps": 3,
            "template_radius_bins": 6, "short_candidate_n": 256,
            "long_n": 1024, "long_every_hops": 4,
            "long_correction_max_hz": 15.625, "history_median": 3}


@lru_cache(maxsize=4)
def _bank(n):
    """Dense Python convenience; hardware stores fractional-offset 13-bin ROM.

    Positive-frequency Hann response is truncated to +/-6 FFT bins. Negative
    image is omitted in this prototype, with search restricted to 1--7 kHz.
    """
    win = np.hanning(n + 1)[:-1]
    freq = np.arange(round(BAND[0] / GRID), round(BAND[1] / GRID) + 1) * GRID
    k = np.arange(n // 2 + 1)
    delta = k[None, :] - freq[:, None] * n / FS
    # Closed-form finite geometric sums, evaluated OFFLINE for constant ROM.
    def dft_rectangle(d):
        num = np.sinc(d)
        den = np.sinc(d / n)
        return np.exp(-1j * np.pi * d * (n - 1) / n) * num / den
    bank = (dft_rectangle(delta) - .5 * dft_rectangle(delta - 1)
            - .5 * dft_rectangle(delta + 1))
    bank[np.abs(delta) > SETTINGS["template_radius_bins"]] = 0.
    return win, freq, bank


def _simple(samples):
    n = len(samples)
    win = np.hanning(n + 1)[:-1]
    amp = 2 * abs(np.fft.rfft(samples * win)) / win.sum()
    found = []
    for k in range(2, len(amp) - 2):
        if not BAND[0] <= k * FS / n <= BAND[1]: continue
        if not (amp[k] > amp[k-1] and amp[k] >= amp[k+1]): continue
        side = np.r_[amp[max(1,k-8):max(1,k-2)], amp[k+3:min(len(amp),k+9)]]
        floor = max(float(np.median(side)), 1e-8)
        contrast = 20*np.log10(max(amp[k],1e-12)/floor)
        if amp[k] < SETTINGS["min_amp"] or contrast < SETTINGS["contrast_db"]: continue
        a,b,c = np.log(np.maximum(amp[k-1:k+2],1e-15))
        off = np.clip(.5*(a-c)/(a-2*b+c),-.5,.5)
        found.append({"frequency_hz":float(np.round((k+off)*FS/n/GRID)*GRID),
                      "amplitude":float(amp[k]), "contrast_db":float(contrast)})
    return sorted(found,key=lambda x:x["amplitude"],reverse=True)[:5]


def estimate_peaks(samples, refine=True):
    """At most five tones, <=5 extraction passes and three fixed refinements.

    Each extraction searches +/-0.75 native FFT bin around the largest residual
    bin; each refinement searches +/-0.5 bin around the current estimate. These
    are finite grid comparisons and scalar projections, not nonlinear fitting.
    """
    if not refine: return _simple(np.asarray(samples))
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
        if amp<SETTINGS["min_amp"] or contrast<SETTINGS["contrast_db"]:continue
        found.append({"frequency_hz":float(frequencies[i]),"amplitude":amp,
                      "contrast_db":contrast})
    return sorted(found,key=lambda x:x["frequency_hz"])


def _groups(tracks, confirmed):
    selected=sorted((t for t in tracks if t["confirmed"] or not confirmed),key=lambda t:t["frequency_hz"])
    clusters=[]
    for t in selected:
        if clusters and t["frequency_hz"]-clusters[-1][-1]["frequency_hz"]<=SETTINGS["group_hz"]:
            clusters[-1].append(t)
        else:clusters.append([t])
    groups={};unsupported=[];eligible=[];deferred=[]
    for cluster in clusters:
        ids=[t["id"] for t in cluster]
        if len(cluster)>2:
            unsupported.append({"track_ids":ids,"frequencies_hz":[t["frequency_hz"] for t in cluster]})
            continue
        # Bound the hardware-facing contract even when false peaks form many
        # isolated groups. Rank by observed power, never by known signal labels.
        score=sum(t.get("amplitude",0.)**2 for t in cluster)
        eligible.append((score,cluster))
    centers=0
    for score,cluster in sorted(eligible,key=lambda x:(-x[0],x[1][0]["id"])):
        ids=[t["id"] for t in cluster]
        if len(groups)>=SETTINGS["max_groups"] or centers+len(cluster)>SETTINGS["max_tracks"]:
            deferred.append({"track_ids":ids,"frequencies_hz":[t["frequency_hz"] for t in cluster],
                             "observed_power_score":float(score),"reason":"group_or_center_capacity"})
            continue
        key="g"+"_".join(str(i) for i in sorted(ids))
        groups[key]=[t["frequency_hz"] for t in cluster]
        centers+=len(cluster)
    return groups,unsupported,deferred

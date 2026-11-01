"""Frozen continuous inputs; only input.mem may connect to the detector."""
from pathlib import Path
import hashlib
import json
import numpy as np
from scipy import signal

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    plan_path = HERE/'experiment_plan.json'
    plan = json.loads(plan_path.read_text(encoding='utf8'))
    fs = plan['fs']
    output = HERE/'fixtures'
    output.mkdir(exist_ok=True)
    records = []
    for case in plan['cases']:
        folder = output/case['name']
        folder.mkdir(exist_ok=True)
        n = round(case['seconds']*fs)
        t = np.arange(n)/fs
        rng = np.random.default_rng(case['seed'])
        target = signal.sosfilt(signal.butter(6,(600,7400),btype='bandpass',fs=fs,output='sos'),rng.standard_normal(n))
        target *= case.get('target_rms',.01)/np.sqrt(np.mean(target**2))
        midpoints = [(case['frequencies_hz'][a]+case['frequencies_hz'][b])/2 for a,b in case['pairs']]
        probes = [np.sqrt(2)*.001*np.sin(2*np.pi*f*t+.37+i) for i,f in enumerate(midpoints)]
        components = dict(target=target)
        components.update({f'probe_{i}':wave for i,wave in enumerate(probes)})
        clean = sum(components.values())
        clean_power = float(np.mean(clean**2))
        jammer_amplitude = np.sqrt(2*clean_power*100)
        masks = [np.zeros(n,dtype=bool) for _ in case['frequencies_hz']]
        events = []
        for center, start_s, stop_s in case['segments']:
            start, stop = round(start_s*fs),round(stop_s*fs)
            assert 0 <= start < stop <= n
            assert not masks[center][start:stop].any()
            masks[center][start:stop] = True
            events.append(dict(center=center,frequency_hz=case['frequencies_hz'][center],
                               start_sample=start,end_sample=stop))
        phases = rng.uniform(-np.pi,np.pi,len(masks))
        for i,(frequency,mask,phase) in enumerate(zip(case['frequencies_hz'],masks,phases)):
            components[f'interference_{i}'] = jammer_amplitude*np.sin(2*np.pi*frequency*t+phase)*mask
        source = sum(components.values())
        front_end_gain = min(1.,.85/max(float(np.max(np.abs(source))),1e-30))
        components = {name:wave*front_end_gain for name,wave in components.items()}
        source = sum(components.values())
        samples = np.rint(source*32768).astype(np.int16)
        components['input_quantization'] = samples.astype(float)/32768-source
        assert np.max(abs(sum(components.values())*32768-samples)) < 1e-8
        simultaneous = np.sum(masks,axis=0)
        assert simultaneous.max() <= case['capacity']
        if events:
            first,last = min(e['start_sample'] for e in events),max(e['end_sample'] for e in events)
            assert first >= .1*fs
            primary = [first,min(n,last+round(.05*fs))]
        else:
            primary = [320,n]
        np.save(folder/'input.npy',samples)
        np.savez_compressed(folder/'components.npz',**components)
        (folder/'input.mem').write_text(''.join(f'{int(v)&65535:04x}\n' for v in samples),encoding='ascii')
        meta = dict(**case,fs=fs,samples=n,events=events,probe_frequencies_hz=midpoints,
                    primary_output_sample_range=primary,front_end_gain=front_end_gain,
                    actual_target_rms=float(np.sqrt(np.mean(components['target']**2))),
                    pre_gain_clean_power=clean_power,actual_jammer_peak_amplitude=jammer_amplitude*front_end_gain,
                    phases_rad=phases.tolist(),maximum_simultaneous_interferers=int(simultaneous.max()),
                    max_input_abs_counts=int(np.max(abs(samples.astype(np.int32)))),
                    input_sha256=sha(folder/'input.npy'),plan_sha256=sha(plan_path),generator_sha256=sha(Path(__file__)),
                    input_encoding='signed int16, RNE normalized*32768; labels offline only',online_truth_access=False)
        (folder/'fixture.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
        records.append(dict(case=case['name'],samples=n,input_sha256=meta['input_sha256'],capacity=case['capacity']))
    (output/'manifest.json').write_text(json.dumps(dict(plan_sha256=sha(plan_path),generator_sha256=sha(Path(__file__)),cases=records),indent=2),encoding='utf8')
    print(json.dumps(records,indent=2))


if __name__ == '__main__':
    main()

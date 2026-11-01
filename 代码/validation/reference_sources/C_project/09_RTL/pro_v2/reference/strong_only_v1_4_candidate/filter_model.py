"""Frozen merged FIR. Supported entry is CONFIGS['direct641']."""
import numpy as np
from prototype_search import prototype, correction, quantize, DEFAULT_CONFIG
from model import apply_schedule

CONFIGS={
    'direct769':dict(DEFAULT_CONFIG),
    'direct641':{'name':'firwin_641_b2_g098','method':'firwin','taps':641,
                 'cutoff_hz':12.,'beta':2.,'strength':.98},
}


def make_bank(frequencies,config):
    if len(frequencies)>5:raise ValueError('at most five centers')
    h=np.zeros(len(prototype(config)))
    h[len(h)//2]=1.
    for f in frequencies:h-=correction(float(f),config)
    h=quantize(h)
    if not np.array_equal(h,h[::-1]):raise AssertionError('lost FIR symmetry')
    return h


def replay(components,schedule,config):
    commands=schedule['commands']
    fade=round(schedule['settings']['fade_ms']*32)
    for a,b in zip(commands[1:],commands[2:]):
        if b['sample']<a['sample']+fade:raise AssertionError('overlapping fades')
    banks=[(int(c['sample']),make_bank(c['frequencies_hz'],config)) for c in commands]
    return apply_schedule(components,banks,fade_samples=fade)

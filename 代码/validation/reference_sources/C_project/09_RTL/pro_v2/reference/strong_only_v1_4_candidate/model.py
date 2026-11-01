"""Same-history FIR and fade replay only; no old detector or scenario generator."""
import numpy as np
from scipy import signal

def apply_schedule(samples, schedule, fade_samples=0):
    """Replay a coefficient schedule on one or multiple independent input rows.

    Every branch is evaluated from the SAME real input history. A mid-fade
    update snapshots the current effective coefficient vector atomically.
    No output state from a different filter is reused as FIR input history.
    """
    samples = np.asarray(samples, dtype=np.float64)
    one_dimensional = samples.ndim == 1
    x = samples[None, :] if one_dimensional else samples
    output = np.zeros_like(x)
    n = x.shape[-1]
    current = np.array(schedule[0][1], dtype=np.float64, copy=True)
    fade_samples = int(fade_samples)
    if fade_samples < 0:
        raise ValueError("fade_samples must be nonnegative")
    fade_from, fade_to, fade_done = current.copy(), current.copy(), fade_samples

    def filtered(coefficients, start, end):
        history_start = max(0, start - len(coefficients) + 1)
        part = signal.lfilter(coefficients, [1.0], x[:, history_start:end], axis=-1)
        return part[:, start - history_start:]

    for index, (start, coefficients) in enumerate(schedule):
        if start >= n:
            break
        end = min(n, schedule[index + 1][0] if index + 1 < len(schedule) else n)
        if index > 0 and fade_samples:
            fade_from, fade_to, fade_done = current.copy(), np.array(coefficients, copy=True), 0
        else:
            current = np.array(coefficients, copy=True)
            fade_from, fade_to, fade_done = current.copy(), current.copy(), fade_samples
        cursor = start
        if fade_done < fade_samples:
            fade_end = min(end, cursor + fade_samples - fade_done)
            weights = (np.arange(fade_end - cursor) + fade_done + 1) / fade_samples
            old = filtered(fade_from, cursor, fade_end)
            new = filtered(fade_to, cursor, fade_end)
            output[:, cursor:fade_end] = old * (1 - weights) + new * weights
            fade_done += fade_end - cursor
            current = fade_from * (1 - weights[-1]) + fade_to * weights[-1]
            cursor = fade_end
        if cursor < end:
            current = fade_to.copy()
            output[:, cursor:end] = filtered(current, cursor, end)
    return output[0] if one_dimensional else output

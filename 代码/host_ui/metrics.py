"""Offline display measurements from actual received samples.

No detector, filter, source labels, or measurements in this module are sent to
the FPGA.  A midpoint spectrum estimate is not a component-isolated measurement
of target attenuation and must not be used as the 1 dB acceptance result.
"""
from __future__ import annotations

import math
import numpy as np
from scene_modes import reference_pairs

FS, DELAY = 32_000, 320
MIN_MIDPOINT_SAMPLES = 2560  # 80 ms, independent of the online FFT/window.


def _samples(values, name):
    values = np.asarray(values)
    if values.ndim != 1 or values.dtype.kind != 'i' or values.dtype.itemsize != 2:
        raise ValueError(f'{name} must be one-dimensional signed int16 samples')
    return values


def _event_time(event, name):
    actual = event.get('actual_' + name)
    return float(event[name] if actual is None else actual)


def _interferers(x, y, metadata, first, align):
    """Hann mixed-signal band ratios, never independent component acceptance."""
    events = metadata.get('events') or []
    results = []
    start, end = first / FS, (first + len(y)) / FS
    resolution = FS / len(y) if len(y) else None
    for index, event in enumerate(events):
        if not event.get('enabled'):
            continue
        frequency = float(event['frequency_hz'])
        record = {'event_index': index + 1, 'event_label': f'干扰 {index+1}',
                  'frequency_hz': frequency, 'available': False,
                  'attenuation_db': None, 'acceptance_pass': None,
                  'measurement': '总混合频带抑制估计'}
        results.append(record)
        if not align:
            record['reason'] = '请先对齐 10 ms 群延迟'
            continue
        if len(y) < MIN_MIDPOINT_SAMPLES:
            record['reason'] = '公共观察窗不足 80 ms'
            continue
        if event.get('mode', 'fixed') != 'fixed':
            record['reason'] = '该路包含换频或漂移'
            continue
        if start < _event_time(event, 'start_s') or end > _event_time(event, 'end_s'):
            record['reason'] = '该路未覆盖整个公共观察窗'
            continue
        changing = False
        neighbors = []
        for other_index, other in enumerate(events):
            if not other.get('enabled'):
                continue
            t0, t1 = _event_time(other, 'start_s'), _event_time(other, 'end_s')
            if start <= t0 < end or start <= t1 < end:
                changing = True
            if t0 < end and t1 > start:
                if other.get('mode', 'fixed') != 'fixed':
                    changing = True
                elif other_index != index:
                    neighbors.append(abs(frequency - float(other['frequency_hz'])))
        if changing:
            record['reason'] = '观察窗跨越事件边缘或包含变化'
            continue
        if not resolution < frequency < FS / 2 - resolution:
            record['reason'] = '频带超出可测范围'
            continue
        if neighbors and min(neighbors) <= 4 * resolution:
            record['reason'] = '邻近干扰间距不足四个频点，不能分开估计'
            continue
        window = np.hanning(len(y))
        hz = np.fft.rfftfreq(len(y), 1 / FS)
        band = np.abs(hz - frequency) <= resolution
        input_power = float(np.sum(np.abs(np.fft.rfft(x * window))[band] ** 2))
        output_power = float(np.sum(np.abs(np.fft.rfft(y * window))[band] ** 2))
        if input_power <= 0 or output_power <= 0 or not math.isfinite(input_power + output_power):
            record['reason'] = '输入或输出频带能量为零或无效；不输出无限 dB 值'
            continue
        record.update(available=True, reason='总混合频带抑制估计，不作独立每路 20 dB 验收',
                      attenuation_db=10 * (math.log10(input_power) - math.log10(output_power)),
                      band_low_hz=float(hz[band][0]), band_high_hz=float(hz[band][-1]),
                      bin_hz=resolution)
    return results


def _midpoints(x, y, ref, metadata, first, align):
    events = metadata.get('events') or []
    results = []
    start, end = first / FS, (first + len(y)) / FS
    for pair in reference_pairs(events):
        record = {'pair': [p + 1 for p in pair], 'pair_label': f'{pair[0]+1}–{pair[1]+1}', 'available': False,
                  'frequency_hz': None, 'spectral_change_db': None,
                  'change_db': None,
                  'acceptance_pass': None}
        results.append(record)
        if len(events) <= pair[1] or not all(events[p].get('enabled') for p in pair):
            record['reason'] = '未设置这对干扰'
            continue
        members = [events[p] for p in pair]
        record['frequency_hz'] = float(sum(float(e['frequency_hz']) for e in members) / 2)
        if ref is None:
            record['reason'] = '无干净参考'
            continue
        if not align:
            record['reason'] = '请先对齐 10 ms 群延迟'
            continue
        if len(y) < MIN_MIDPOINT_SAMPLES:
            record['reason'] = '公共观察窗不足 80 ms'
            continue
        if any(e.get('mode', 'fixed') != 'fixed' for e in members):
            record['reason'] = '该对包含换频或漂移'
            continue

        if any(start < _event_time(e, 'start_s') or end > _event_time(e, 'end_s') for e in members):
            record['reason'] = '该对未覆盖整个公共观察窗'
            continue
        changing = False
        for event in events:
            if not event.get('enabled'):
                continue
            t0, t1 = _event_time(event, 'start_s'), _event_time(event, 'end_s')
            if start <= t0 < end or start <= t1 < end:
                changing = True
            if t0 < end and t1 > start and event.get('mode', 'fixed') != 'fixed':
                changing = True
        if changing:
            record['reason'] = '观察窗跨越事件边缘或包含变化'
            continue
        window = np.hanning(len(y))
        hz = np.fft.rfftfreq(len(y), 1 / FS)
        resolution = FS / len(y)
        midpoint = record['frequency_hz']
        distance = abs(float(members[0]['frequency_hz']) - float(members[1]['frequency_hz']))
        if not 0 < midpoint < FS / 2 or distance <= 4 * resolution:
            record['reason'] = '观察窗频率分辨率不足'
            continue
        band = np.abs(hz - midpoint) <= resolution
        reference_power = float(np.sum(np.abs(np.fft.rfft(ref * window))[band] ** 2))
        output_power = float(np.sum(np.abs(np.fft.rfft(y * window))[band] ** 2))
        if reference_power <= 0 or not math.isfinite(reference_power + output_power):
            record['reason'] = '参考频带能量为零或无效'
            continue
        if output_power <= 0:
            record['reason'] = '输出频带能量为零；谱差无有限值'
            continue
        record.update(available=True, reason='仅谱变化估计，不作 1 dB 验收',
                      spectral_change_db=10 * math.log10(output_power / reference_power),
                      change_db=10 * math.log10(output_power / reference_power),
                      band_low_hz=float(hz[band][0]), band_high_hz=float(hz[band][-1]),
                      bin_hz=resolution)
    return results


def compute_metrics(data, output, clean, metadata, start, width, align=True):
    """Return JSON-safe selected-window metrics; no gain fitting or zero padding.

    NRMSE is computed only with the fixed 320-sample alignment enabled.
    """
    data, output = _samples(data, 'data'), _samples(output, 'output')
    if len(output) > len(data):
        raise ValueError('output must be an actual received prefix of the input')
    if not math.isfinite(start) or not math.isfinite(width) or width <= 0:
        raise ValueError('start/width must be finite and width must be positive')
    if clean is not None:
        clean = np.asarray(clean, dtype=float)
        if clean.ndim != 1 or len(clean) != len(data) or not np.all(np.isfinite(clean)):
            raise ValueError('clean must be a full-length finite normalized reference')
    first = min(len(data), max(0, int(round(start * FS))))
    last = min(len(data), first + max(32, int(round(width * FS))))
    shift = DELAY if align else 0
    stop = min(last, max(first, len(output) - shift))
    n = max(0, stop - first)
    x = data[first:stop].astype(float) / 32768.0
    y = output[first + shift:stop + shift].astype(float) / 32768.0
    ref = None if clean is None else clean[first:stop]
    before = after = None
    reduction = None
    reduction_reason = None
    reason = None
    if clean is None:
        reason = '无干净参考'
    elif not align:
        reason = '未对齐 10 ms 群延迟'
    elif not n:
        reason = '尚无可对齐回传'
    else:
        energy = float(np.linalg.norm(ref))
        input_error = float(np.linalg.norm(x - ref))
        output_error = float(np.linalg.norm(y - ref))
        if input_error > 0 and output_error > 0:
            reduction = 20 * (math.log10(input_error) - math.log10(output_error))
        elif input_error == 0 and output_error == 0:
            reduction_reason = '输入和输出恢复误差均为零，降低量无定义'
        elif input_error == 0:
            reduction_reason = '输入恢复误差为零，不输出无限 dB 值'
        else:
            reduction_reason = '输出恢复误差为零，不输出无限 dB 值'
        if energy == 0:
            reason = '干净参考能量为零'
        else:
            before = 100.0 * input_error / energy
            after = 100.0 * output_error / energy
    if reduction is None and reduction_reason is None:
        reduction_reason = reason
    return {
        'sample_rate_hz': FS, 'aligned': bool(align), 'alignment_samples': shift,
        'first_sample': first, 'stop_sample_exclusive': stop,
        'common_samples': n, 'common_ms': n * 1000.0 / FS,
        'measurement_scope': 'actual_common' if n else 'preview',
        'input_nrmse_pct': before, 'output_nrmse_pct': after,
        'nrmse_percent': None if after is None else {'input': before, 'output': after},
        'nrmse_unavailable_reason': reason,
        'error_reduction_db': reduction,
        'error_reduction_unavailable_reason': reduction_reason,
        'midpoints': _midpoints(x, y, ref, metadata or {}, first, bool(align)),
        'interferers': _interferers(x, y, metadata or {}, first, bool(align)),
        'definitions': {
            'nrmse': '100*norm(signal-clean)/norm(clean); fixed 320-sample alignment; no gain fitting',
            'error_reduction_db': '20log10(norm(input-clean)/norm(output-clean)); common aligned interval; no gain fitting; zero errors return null with a reason',
            'interferer_attenuation': 'Offline configured fixed source frequency +/- one FFT bin; same-window Hann mixed input/output band power ratio, signed dB; >=80ms, no source edges/changes and >4 bins from other active tones',
            'interferer_limit': 'Total mixed-band attenuation includes target and noise. It is not isolated per-source interference attenuation and cannot certify a 20dB requirement. Source labels are used only for offline display.',
            'midpoint': 'Offline configured-frequency connected groups with <=750Hz neighbor gaps and exactly two enabled members; not FPGA grouping. Hann output/reference power ratio in midpoint +/- one FFT bin; signed dB',
            'midpoint_limit': 'Mixed output includes residual interferers and noise. This is not isolated target loss, not online grouping, and cannot certify the <=1 dB protection requirement.',
        },
    }

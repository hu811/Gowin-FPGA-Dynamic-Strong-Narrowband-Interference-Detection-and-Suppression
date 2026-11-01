"""Only fresh raw strong evidence grants execution; no two-hit entry fallback.

No source labels enter this module. ID identifies a track; version binds a
coefficient center and authorization epoch. A weak observation never creates a
track, resets misses, updates history, or queues work. Strong reacquisition
starts a new epoch/version. Two misses retire the ID as well.
"""
from copy import deepcopy
import numpy as np
from dependencies import legacy, cluster_baseline
from extraction_audit import estimate_with_audit
import balanced_strength
import orthogonal_detector

FS, GRID = legacy.FS, legacy.GRID
THRESHOLD_DB = 21.
POWER_RATIO = 10 ** (THRESHOLD_DB / 10)
MIN_AMPLITUDE = cluster_baseline.MIN_AMPLITUDE  # .055; deliberately retained
DEFAULT_GATE = 'balanced_fixed'
GATE_MODES = (DEFAULT_GATE, 'baseline_v1_2')
DEFAULT_EXTRACTION = 'orthogonal'
EXTRACTION_MODES = (DEFAULT_EXTRACTION, 'baseline_v1_2')


def strength_evidence(samples, peaks, *, gate_mode=DEFAULT_GATE):
    """Select a frozen evidence metric; lifecycle rules remain unchanged."""
    if gate_mode not in GATE_MODES:
        raise ValueError(f'unknown gate mode: {gate_mode}')
    x = np.asarray(samples, dtype=float)
    valid_input = x.ndim == 1 and len(x) >= 4 and np.isfinite(x).all()
    try:
        with np.errstate(over='raise', invalid='raise', divide='raise'):
            if not valid_input:
                evidence = []
            elif gate_mode == 'baseline_v1_2':
                evidence = cluster_baseline.local_strength_evidence(x, peaks,
                    strong_contrast_db=THRESHOLD_DB, guard_bins=2,
                    reference_bins=12, min_reference_bins=10)
            else:
                evidence = balanced_strength.local_strength_evidence(x, peaks)
    except (ValueError, FloatingPointError, OverflowError):
        evidence = []
    if len(evidence) != len(peaks):
        evidence = [dict(frequency_hz=float(p['frequency_hz']),
            raw_peak_power=None, background_power=None, reference_count=0,
            reference_bins=[], local_contrast_db=None, strong=False,
            enough_reference=False, numeric_valid=False) for p in peaks]
    for item in evidence:
        power, background = item['raw_peak_power'], item['background_power']
        finite = (power is not None and background is not None and
                  np.isfinite(power) and np.isfinite(background) and background > 0)
        # The inherited numerical floor remains part of the exact metric.
        # Require a finite threshold product as well as a finite raw spectrum.
        with np.errstate(over='ignore', invalid='ignore'):
            threshold = POWER_RATIO * background if finite else float('inf')
        valid = bool(finite and np.isfinite(threshold) and item['enough_reference']
                     and item['reference_count'] >= 10)
        item['gate_mode'] = gate_mode
        item['numeric_valid'] = valid
        item['strong_eligible'] = bool(valid and max(power, 1e-16) >= threshold)
        item['strong'] = item['strong_eligible']
        item['threshold_power'] = float(threshold) if np.isfinite(threshold) else None
    return evidence


class StrongController:
    """Observation-clock lifecycle; the scheduler separately owns active banks."""
    def __init__(self):
        self.tracks = []
        self.next_id = 0

    def step(self, peaks, evidence, observation_id, sample, long_peaks=()):
        peaks, evidence = deepcopy(peaks), deepcopy(evidence)
        assert len(peaks) == len(evidence)
        for p, e in zip(peaks, evidence):
            assert p['frequency_hz'] == e['frequency_hz']
            e.update(observation_id=observation_id, observation_sample=sample,
                     candidate_id=None, candidate_version=None,
                     raw_strength_pass=bool(e['strong_eligible']))
        strong_indices = [i for i, e in enumerate(evidence) if e['strong_eligible']]
        matches = sorted((abs(t['frequency_hz']-peaks[i]['frequency_hz']), ti, i)
            for ti, t in enumerate(self.tracks) for i in strong_indices
            if abs(t['frequency_hz']-peaks[i]['frequency_hz']) <= legacy.SETTINGS['match_hz'])
        used_t, used_p, updates, revoked = set(), set(), [], []
        # Only already-authorized, currently strong matches can use 1024 data.
        long_used = set()
        for _, ti, pi in matches:
            if ti in used_t or pi in used_p:
                continue
            old, p = self.tracks[ti], peaks[pi]
            reacquired = bool(old['misses'])
            frequency = p['frequency_hz']
            choices = [(abs(frequency-q['frequency_hz']), qi) for qi, q in enumerate(long_peaks)
                       if not reacquired and qi not in long_used and abs(frequency-q['frequency_hz']) <= legacy.SETTINGS['long_correction_max_hz']]
            corrected = False
            if choices:
                _, qi = min(choices)
                frequency = long_peaks[qi]['frequency_hz']
                long_used.add(qi)
                corrected = True
            if reacquired:
                # Invalidate old tasks without deleting an already active,
                # freshly supported physical member during group ambiguity.
                old['authorization_epoch'] += 1
                old['authorization_started_sample'] = sample
                old['version'] += 1
                old['history'] = [frequency]
                old['frequency_hz'] = frequency
            else:
                old['history'] = (old['history']+[frequency])[-legacy.SETTINGS['history_median']:]
                center = float(np.round(np.median(old['history']) / GRID) * GRID)
                if abs(old['frequency_hz']-center) > GRID:
                    old['frequency_hz'] = center
                    old['version'] += 1
            old.update(misses=0, strong_eligible=True, hits=1,
                amplitude=p['amplitude'], last_strong_observation=observation_id,
                last_strong_sample=sample, observation_id=observation_id,
                evidence_frequency_hz=p['frequency_hz'], long_corrected=corrected)
            evidence[pi].update(candidate_id=old['id'], candidate_version=old['version'])
            updates.append(dict(id=old['id'], version=old['version'],
                authorization_epoch=old['authorization_epoch'],
                action='fresh_strong_new_epoch' if reacquired else 'fresh_strong'))
            used_t.add(ti); used_p.add(pi)
        for ti, t in enumerate(self.tracks):
            if ti not in used_t:
                t['misses'] += 1
                t['strong_eligible'] = False
                t['hits'] = 0
                t['observation_id'] = observation_id
                if t['misses'] >= 2:
                    revoked.append(dict(id=t['id'], reason='two_missing_strong_observations'))
        self.tracks = [t for t in self.tracks if t['misses'] < 2]
        deferred = []
        for pi in strong_indices:
            if pi in used_p:
                continue
            if len(self.tracks) >= legacy.SETTINGS['max_tracks']:
                deferred.append(pi)
                continue
            p = peaks[pi]
            t = dict(p, id=self.next_id, version=1, misses=0, hits=1,
                authorization_epoch=1, authorization_started_sample=sample,
                confirmed=True, strong_eligible=True, history=[p['frequency_hz']],
                first_observed_sample=sample, last_strong_sample=sample,
                last_strong_observation=observation_id, observation_id=observation_id,
                evidence_frequency_hz=p['frequency_hz'], long_corrected=False)
            self.next_id += 1
            self.tracks.append(t)
            evidence[pi].update(candidate_id=t['id'], candidate_version=t['version'])
            updates.append(dict(id=t['id'], version=1, action='single_strong_entry'))
        # Missed members may be held only by existing active banks, never here.
        fresh = [t for t in self.tracks if t['strong_eligible']]
        desired, unsupported, capacity = legacy._groups(fresh, True)
        # Capacity must not hide a third current strong center. Apply the SAME
        # inherited grouping rule to all raw strong observations, including
        # those without a free track slot; these diagnostic IDs grant no rights.
        raw_tracks = [dict(peaks[i], id=i, confirmed=True) for i in strong_indices]
        _, raw_unsupported, _ = legacy._groups(raw_tracks, True)
        raw_clusters = [dict(
            track_ids=[evidence[i]['candidate_id'] for i in u['track_ids']
                       if evidence[i]['candidate_id'] is not None],
            observed_peak_indices=u['track_ids'],
            observed_cluster_size=len(u['track_ids']),
            frequencies_hz=u['frequencies_hz'], basis='all_raw_strong_observations')
            for u in raw_unsupported]
        blocked = sorted({i for u in unsupported+capacity+raw_clusters for i in u['track_ids']})
        desired = {g:v for g,v in desired.items()
                   if not set(map(int,g[1:].split('_'))).intersection(blocked)}
        proposals = {}
        for gid, centers in desired.items():
            ids = set(map(int, gid[1:].split('_')))
            members = [t for t in fresh if t['id'] in ids]
            proposals[gid] = [dict(id=t['id'], version=t['version'], frequency_hz=t['frequency_hz'],
                observation_id=observation_id, observation_sample=sample, strong_eligible=True)
                for t in sorted(members, key=lambda t:t['id'])]
        # A raw peak can pass 21dB while all lifetime slots are occupied. It
        # remains diagnostic evidence, not an unbound execution permission.
        for e in evidence:
            e['strong_eligible'] = bool(e['raw_strength_pass'] and e['candidate_id'] is not None)
            e['authorization_capacity_deferred'] = bool(e['raw_strength_pass'] and e['candidate_id'] is None)
        return dict(sample=sample, observation_id=observation_id,
            observation_end=sample, peaks_hz=[p['frequency_hz'] for p in peaks],
            candidates=deepcopy(desired), desired=desired, proposals=proposals,
            authorizations={str(t['id']):deepcopy(t) for t in self.tracks},
            local_evidence=evidence, unsupported_clusters=unsupported,
            raw_unsupported_clusters=raw_clusters,
            blocked_ids=blocked, capacity_deferred=capacity,
            strong_capacity_deferred_indices=deferred,
            authorization_events=updates, revoked=revoked,
            strong_third_candidate_count=max(
                sum(max(0,len(c['track_ids'])-2) for c in unsupported),
                sum(max(0,c['observed_cluster_size']-2) for c in raw_clusters)),
            audit={'tracks':deepcopy(self.tracks)})


def run_detector(mixture, *, gate_mode=DEFAULT_GATE, extraction_mode=DEFAULT_EXTRACTION):
    if gate_mode not in GATE_MODES:
        raise ValueError(f'unknown gate mode: {gate_mode}')
    if extraction_mode not in EXTRACTION_MODES:
        raise ValueError(f'unknown extraction mode: {extraction_mode}')
    x = np.asarray(mixture, dtype=float)
    if x.ndim != 1 or not np.isfinite(x).all():
        raise ValueError('one finite mixed stream required')
    controller = StrongController()
    frames, long_peaks = [], []
    for frame_no, end in enumerate(range(256, len(x)+1, 64)):
        short = legacy._simple(x[end-256:end])
        estimator=(orthogonal_detector.estimate_with_audit if extraction_mode == DEFAULT_EXTRACTION
                   else estimate_with_audit)
        try:
            generated, extraction = estimator(x[end-512:end]) if end >= 512 else ([],{})
        except (FloatingPointError, OverflowError, np.linalg.LinAlgError):
            generated, extraction = [],dict(numeric_fit_failure=True)
        peaks = [dict(p) for p in generated if p['amplitude'] >= MIN_AMPLITUDE]
        if end >= 1024 and frame_no % legacy.SETTINGS['long_every_hops'] == 0:
            long_peaks = legacy.estimate_peaks(x[end-1024:end], refine=False)
        evidence = strength_evidence(x[end-512:end], peaks, gate_mode=gate_mode) if peaks else []
        frame = controller.step(peaks, evidence, frame_no, end, long_peaks)
        frame.update(short_candidates_hz=[p['frequency_hz'] for p in short],
            long_peaks_hz=[p['frequency_hz'] for p in long_peaks],
            generation_diagnostics={'legacy_returned_peaks':generated,
                'blocked_by_outer_055':[p for p in generated if p['amplitude'] < MIN_AMPLITUDE],
                'extraction':extraction,
                'earlier_035_and_residual10_filters_retained':True})
        frames.append(frame)
    return dict(frames=frames, fs=FS, n_samples=len(x),
        settings=dict(legacy.SETTINGS, min_amp=MIN_AMPLITUDE, confirm_frames=1,
            miss_frames=2, raw_strength_db=THRESHOLD_DB, entry_fallback=False,
            long_role='correct only already-authorized freshly strong matches',
            auxiliary256_role='log only', global_gain_invariance=False,
            gate_mode=gate_mode, extraction_mode=extraction_mode,
            metric=(balanced_strength.METRIC if gate_mode == DEFAULT_GATE
                                        else 'candidate_masked_pooled_median_v1_2')))

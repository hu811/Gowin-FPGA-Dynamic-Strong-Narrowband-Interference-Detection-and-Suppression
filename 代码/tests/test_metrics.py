"""Checks for display measurements; no serial I/O or reference filtering."""
import json
import unittest
import numpy as np

from metrics import FS, DELAY, compute_metrics


def delayed(x):
    return np.concatenate((np.zeros(DELAY, dtype=np.int16), x))[:len(x)]


def source(frequency=2400., **updates):
    event = dict(enabled=True, frequency_hz=frequency, mode='fixed', start_s=.05, end_s=.45)
    event.update(updates)
    return event


class MetricsTests(unittest.TestCase):
    def test_mixed_layout_uses_only_the_real_configured_pair(self):
        t = np.arange(16000) / FS
        x = np.rint(5000 * np.sin(2*np.pi*3335.2*t)).astype(np.int16)
        metadata = {'events': [source(f) for f in (1511.3, 3272.7, 3397.7, 5847.7)]}
        m = compute_metrics(x, delayed(x), x / 32768., metadata, .1, .1)
        self.assertEqual(len(m['midpoints']), 1)
        pair, = m['midpoints']
        self.assertEqual(pair['pair'], [2, 3])
        self.assertEqual(pair['pair_label'], '2–3')
        self.assertAlmostEqual(pair['frequency_hz'], 3335.2)
        self.assertAlmostEqual(pair['change_db'], 0., places=12)
        self.assertIsNone(pair['acceptance_pass'])

    def test_three_connected_centers_do_not_create_a_fake_midpoint_pair(self):
        x = np.ones(16000, dtype=np.int16)
        metadata = {'events': [source(f) for f in (3000., 3125., 3250., 5800.)]}
        m = compute_metrics(x, delayed(x), x / 32768., metadata, .1, .1)
        self.assertEqual(m['midpoints'], [])

    def test_bypass_zero_db_and_no_rms_measurements(self):
        x = np.full(6400, 16384, dtype=np.int16)
        m = compute_metrics(x, delayed(x), x / 65536., {}, 0, .1)
        self.assertEqual(m['input_nrmse_pct'], 100.)
        self.assertEqual(m['output_nrmse_pct'], 100.)
        self.assertEqual(m['error_reduction_db'], 0.)

        def check_keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertNotIn(key, ('rms', 'dbfs', 'input_rms', 'output_rms', 'reference_rms', 'rms_scope'))
                    check_keys(item)
            elif isinstance(value, list):
                for item in value:
                    check_keys(item)

        check_keys(m)
        json.dumps(m, allow_nan=False)

    def test_known_error_reduction_forty_db(self):
        clean = np.full(6400, 4096 / 32768.)
        x = np.full(6400, 14096, dtype=np.int16)
        y = delayed(np.full(6400, 4196, dtype=np.int16))
        m = compute_metrics(x, y, clean, {}, 0, .1)
        self.assertAlmostEqual(m['error_reduction_db'], 40., places=12)
        self.assertIsNone(m['error_reduction_unavailable_reason'])

    def test_partial_prefix_is_not_zero_padded(self):
        x = np.full(6400, 8192, dtype=np.int16)
        m = compute_metrics(x, delayed(x)[:960], x / 32768., {'events': [source()]}, 0, .1)
        self.assertEqual(m['common_samples'], 640)
        self.assertEqual(m['common_ms'], 20.)
        self.assertEqual(m['output_nrmse_pct'], 0.)
        self.assertFalse(m['interferers'][0]['available'])
        self.assertIsNone(m['interferers'][0]['acceptance_pass'])

    def test_no_output_preview_and_zero_errors(self):
        x = np.full(3200, 8192, dtype=np.int16)
        m = compute_metrics(x, np.array([], dtype=np.int16), x / 32768., {}, 0, .1)
        self.assertEqual(m['measurement_scope'], 'preview')
        self.assertIsNone(m['nrmse_percent'])
        self.assertIsNone(m['error_reduction_db'])
        self.assertTrue(m['error_reduction_unavailable_reason'])
        z = np.zeros(3200, dtype=np.int16)
        m = compute_metrics(z, z, z.astype(float), {}, 0, .08)
        self.assertIsNone(m['output_nrmse_pct'])
        self.assertIsNone(m['error_reduction_db'])
        json.dumps(m, allow_nan=False)

    def test_one_zero_error_does_not_export_infinity(self):
        x = np.full(6400, 8192, dtype=np.int16)
        y = delayed(np.full(6400, 4096, dtype=np.int16))
        for clean in (x / 32768., np.full(6400, 4096 / 32768.)):
            m = compute_metrics(x, y, clean, {}, 0, .1)
            self.assertIsNone(m['error_reduction_db'])
            self.assertTrue(m['error_reduction_unavailable_reason'])
            json.dumps(m, allow_nan=False)

    def test_unaligned_and_no_reference_never_claim_recovery(self):
        x = np.full(6400, 8192, dtype=np.int16)
        for clean, align in ((x / 32768., False), (None, True)):
            m = compute_metrics(x, delayed(x), clean, {}, .05, .1, align)
            self.assertIsNone(m['output_nrmse_pct'])
            self.assertIsNone(m['error_reduction_db'])
            self.assertTrue(m['error_reduction_unavailable_reason'])

    def test_midpoint_bypass_and_guards(self):
        t = np.arange(16000) / FS
        x = np.rint(5000 * np.sin(2*np.pi*2373.8*t)).astype(np.int16)
        metadata = {'events': [source(f) for f in (2311.3, 2436.3)]}
        m = compute_metrics(x, delayed(x), x / 32768., metadata, .1, .1)
        p = m['midpoints'][0]
        self.assertTrue(p['available'])
        self.assertAlmostEqual(p['spectral_change_db'], 0., places=12)
        self.assertIsNone(p['acceptance_pass'])
        for start, width, align in ((.1, .04, True), (.1, .1, False), (.02, .1, True), (.4, .1, True)):
            p = compute_metrics(x, delayed(x), x / 32768., metadata, start, width, align)['midpoints'][0]
            self.assertFalse(p['available'])
        metadata['events'][0]['mode'] = 'linear'
        p = compute_metrics(x, delayed(x), x / 32768., metadata, .1, .1)['midpoints'][0]
        self.assertFalse(p['available'])

    def test_midpoint_tracks_power_ratio_without_claiming_target_loss(self):
        t = np.arange(16000) / FS
        x = np.rint(4096 * np.sin(2*np.pi*2375*t)).astype(np.int16)
        y = delayed(np.rint(x.astype(float) / 2).astype(np.int16))
        metadata = {'events': [source(f) for f in (2312.5, 2437.5)]}
        p = compute_metrics(x, y, x / 32768., metadata, .1, .1)['midpoints'][0]
        self.assertTrue(p['available'])
        self.assertAlmostEqual(p['spectral_change_db'], -6.0206, places=2)
        self.assertIsNone(p['acceptance_pass'])

    def test_narrowband_bypass_six_and_twenty_db(self):
        t = np.arange(16000) / FS
        x = np.rint(12000 * np.sin(2*np.pi*2400*t)).astype(np.int16)
        metadata = {'events': [source()]}
        for factor, expected in ((1., 0.), (.5, 6.0206), (.1, 20.)):
            y = delayed(np.rint(factor * x.astype(float)).astype(np.int16))
            m = compute_metrics(x, y, None, metadata, .1, .1)
            row = m['interferers'][0]
            self.assertTrue(row['available'])
            self.assertAlmostEqual(row['attenuation_db'], expected, places=2)
            self.assertIsNone(row['acceptance_pass'])
            self.assertIsNone(m['error_reduction_db'])
            json.dumps(m, allow_nan=False)

    def test_band_guards_silence_dynamics_edges_and_isolation(self):
        t = np.arange(16000) / FS
        x = np.rint(10000 * np.sin(2*np.pi*2400*t)).astype(np.int16)
        for events, start, width, align in (([source()], .1, .04, True),
                                            ([source()], .1, .1, False),
                                            ([source(mode='linear')], .1, .1, True),
                                            ([source(mode='step')], .1, .1, True),
                                            ([source()], .02, .1, True),
                                            ([source()], .4, .1, True),
                                            ([source(),source(4000,start_s=.15)], .1, .1, True),
                                            ([source(),source(2420)], .1, .1, True)):
            m = compute_metrics(x, delayed(x), None, {'events': events}, start, width, align)
            for row in m['interferers']:
                self.assertFalse(row['available'])
                self.assertIsNone(row['attenuation_db'])
                self.assertIsNone(row['acceptance_pass'])
        zero = np.zeros(16000, dtype=np.int16)
        for values in (zero, x):
            row = compute_metrics(values, zero, None, {'events':[source()]}, .1, .1)['interferers'][0]
            self.assertFalse(row['available'])
            self.assertIsNone(row['attenuation_db'])


if __name__ == '__main__':
    unittest.main()

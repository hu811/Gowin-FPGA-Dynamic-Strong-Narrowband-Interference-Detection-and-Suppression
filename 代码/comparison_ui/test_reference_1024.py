"""Engine-only regression; no GUI, UART, or writes to frozen model outputs."""
from pathlib import Path
import json
import time
import unittest
from unittest.mock import patch

import numpy as np

import reference_1024 as engine

MODEL = Path(__file__).resolve().parent.parent/'validation/recorded_1024_reference'


class ReferenceTests(unittest.TestCase):
    def test_frozen_64000_output_banks_trajectory_and_decisions(self):
        x = np.load(MODEL/'input.npy', allow_pickle=False)
        before = x.copy()
        result = engine.run_reference(x, MODEL)
        np.testing.assert_array_equal(result['output'], np.load(MODEL/'output_1024_software.npy', allow_pickle=False))
        with np.load(MODEL/'backend_state.npz', allow_pickle=False) as z:
            np.testing.assert_array_equal(result['banks_q16'], z['banks_q16'])
            np.testing.assert_array_equal(result['trajectory'], z['trajectory'])
        self.assertEqual(result['decisions'], json.loads((MODEL/'decisions_1024_21db.json').read_text(encoding='utf-8')))
        np.testing.assert_array_equal(x, before)
        self.assertEqual(result['stats']['observations'], 985)
        self.assertEqual(result['stats']['updates'], 25)
        self.assertEqual(result['stats']['coefficient_guard_failures'], 0)
        self.assertEqual(result['stats']['output_saturations'], 0)

    def test_different_lengths_deterministic_and_short_bypass(self):
        rng = np.random.default_rng(26102731)
        for count in (32, 319, 320, 321, 1023, 1024, 1088, 2049):
            with self.subTest(samples=count):
                x = rng.integers(-2000, 2001, count, dtype=np.int16)
                first = engine.run_reference(x, MODEL)
                second = engine.run_reference(x, MODEL)
                np.testing.assert_array_equal(first['output'], second['output'])
                self.assertEqual(first['decisions'], second['decisions'])
                self.assertEqual(len(first['output']), count)
                self.assertEqual(first['stats']['observations'], max(0, (count-1024)//64+1))
                if count < 1024:
                    expected = np.zeros_like(x)
                    expected[320:] = x[:max(0, count-320)]
                    np.testing.assert_array_equal(first['output'], expected)
                    self.assertEqual(first['stats']['updates'], 0)

    def test_input_validation(self):
        for value in (np.zeros(32, dtype=float), np.zeros((2, 16), dtype=np.int16)):
            with self.assertRaises(TypeError):
                engine.run_reference(value, MODEL)
        for count in (0, 31, engine.MAX_SAMPLES+1):
            with self.assertRaises(ValueError):
                engine.run_reference(np.zeros(count, dtype=np.int16), MODEL)

    def test_cancel_before_start(self):
        with self.assertRaises(engine.ReferenceCancelled):
            engine.run_reference(np.zeros(32, dtype=np.int16), MODEL, cancelled=lambda: True)

    def test_cancel_after_first_window(self):
        stop = False
        progress = []
        def callback(stage, completed, total):
            nonlocal stop
            progress.append((stage, completed, total))
            if stage == 'detect' and completed == 1:
                stop = True
        with self.assertRaises(engine.ReferenceCancelled):
            engine.run_reference(np.zeros(4096, dtype=np.int16), MODEL,
                                 progress=callback, cancelled=lambda: stop)
        self.assertEqual(progress[-1], ('detect', 1, 49))

    def test_cancel_at_filter_boundary(self):
        stop = False
        def callback(stage, completed, total):
            nonlocal stop
            if stage == 'filter' and completed == 0:
                stop = True
        with self.assertRaises(engine.ReferenceCancelled):
            engine.run_reference(np.zeros(32, dtype=np.int16), MODEL,
                                 progress=callback, cancelled=lambda: stop)

    def test_cancel_at_convolution_boundary(self):
        stop = False
        original = np.convolve
        def convolve(*args, **kwargs):
            nonlocal stop
            result = original(*args, **kwargs)
            stop = True
            return result
        with patch.object(np, 'convolve', side_effect=convolve):
            with self.assertRaises(engine.ReferenceCancelled):
                engine.run_reference(np.zeros(321, dtype=np.int16), MODEL, cancelled=lambda: stop)

    def test_changed_numerical_source_rejected_before_cached_model(self):
        original = engine._sha
        def changed(path):
            return 'invalid' if Path(path).name == 'cos_grid_direct.mem' else original(path)
        with patch.object(engine, '_sha', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'Frozen numerical source changed'):
                engine.run_reference(np.zeros(32, dtype=np.int16), MODEL)


if __name__ == '__main__':
    started = time.perf_counter()
    unittest.main(verbosity=2)

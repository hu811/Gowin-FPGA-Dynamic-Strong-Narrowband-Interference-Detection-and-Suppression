"""Preset/source validity checks; no claim about FPGA quality or queue timing."""
from copy import deepcopy
import unittest

import numpy as np

from scene import FS, HEADROOM, default_config, generate_scene
from scene_modes import MODE_LABELS, preset_config, reference_pairs


def event(hz, enabled=True):
    return dict(enabled=enabled, frequency_hz=hz)


class SceneModeTests(unittest.TestCase):
    def test_labels_and_unknown_mode(self):
        self.assertEqual(MODE_LABELS,
            {"two_pairs": "两对双峰", "mixed": "两单＋一双"})
        with self.assertRaises(ValueError):
            preset_config("three_centers")

    def test_two_pairs_preserves_original_configuration_and_input(self):
        baseline = default_config(4)
        preset = preset_config("two_pairs")
        self.assertEqual(preset, baseline)
        expected, expected_meta, expected_components = generate_scene(baseline)
        actual, actual_meta, actual_components = generate_scene(preset)
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual_meta, expected_meta)
        for name in expected_components:
            np.testing.assert_array_equal(actual_components[name], expected_components[name])
        self.assertEqual(reference_pairs(preset["events"]), [(0, 1), (2, 3)])

    def test_configs_are_fresh_and_nested_mutations_do_not_leak(self):
        for mode in MODE_LABELS:
            with self.subTest(mode=mode):
                first = preset_config(mode)
                original = deepcopy(first)
                first["events"][0]["frequency_hz"] = 999.0
                first["events"].append(event(1234.0))
                first["seed"] = 0
                self.assertEqual(preset_config(mode), original)
        self.assertEqual(preset_config("two_pairs"), default_config(4))

    def test_mixed_preset_has_time_ordered_single_pair_single(self):
        config = preset_config("mixed")
        self.assertEqual(config["capacity"], 4)
        self.assertEqual(set(config), set(default_config(4)))
        self.assertEqual([e["frequency_hz"] for e in config["events"]],
            [1511.3, 3272.7, 3397.7, 5847.7])
        self.assertEqual([e["start_s"] for e in config["events"]],
            [.25, .258, .259, .266])
        self.assertEqual([e["end_s"] for e in config["events"]],
            [1.30, 1.40, 1.55, 1.65])
        for source in config["events"]:
            self.assertTrue(source["enabled"])
            self.assertEqual(source["mode"], "fixed")
            self.assertEqual(source["frequency_hz"], source["end_frequency_hz"])
            self.assertEqual(source["js_db"], 20.0)
            self.assertEqual(source["change_s"], (source["start_s"] + source["end_s"]) / 2)
        self.assertEqual(reference_pairs(config["events"]), [(1, 2)])

    def test_both_presets_generate_finite_four_source_int16_input(self):
        for mode in MODE_LABELS:
            with self.subTest(mode=mode):
                samples, metadata, components = generate_scene(preset_config(mode))
                self.assertEqual(samples.dtype, np.dtype("<i2"))
                self.assertEqual(samples.size, 2 * FS)
                self.assertEqual(metadata["enabled_sources"], 4)
                self.assertEqual(metadata["max_simultaneous_sources"], 4)
                self.assertFalse(metadata["over_capacity"])
                self.assertFalse(metadata["enabled_sources_exceed_capacity"])
                self.assertLessEqual(metadata["unquantized_max_abs"], HEADROOM + 1e-12)
                self.assertLessEqual(metadata["actual_max_abs_sample"], round(HEADROOM * 32768))
                self.assertEqual(metadata["config"]["capacity"], 4)
                for values in components.values():
                    self.assertTrue(np.all(np.isfinite(values)))
                for source in metadata["events"]:
                    self.assertTrue(source["in_detector_range"])
                    self.assertAlmostEqual(source["actual_js_db"], 20.0, places=10)

    def test_empty_disabled_and_single_sources_have_no_pairs(self):
        for events in ([], [event(2000.)], [event(2000., False), event(2100.)],
                [event(2000., False), event(2100., False)]):
            with self.subTest(events=events):
                self.assertEqual(reference_pairs(events), [])

    def test_two_singles_do_not_become_a_pair(self):
        sources = [event(f) for f in (1511.3, 3272.7, 3397.7, 5847.7)]
        self.assertEqual(reference_pairs(sources), [(1, 2)])
        self.assertEqual(reference_pairs([sources[0], sources[3]]), [])

    def test_groups_follow_frequency_and_return_original_row_indices(self):
        self.assertEqual(reference_pairs([event(f) for f in (6000., 2000., 2125., 6125.)]),
            [(0, 3), (1, 2)])
        self.assertEqual(reference_pairs([event(2000.), event(2750.)]), [(0, 1)])
        self.assertEqual(reference_pairs([event(2000.), event(2750.001)]), [])

    def test_three_member_chain_is_not_split_into_pairs(self):
        # The endpoints are 1400 Hz apart; connectivity is transitive through 2700 Hz.
        self.assertEqual(reference_pairs([event(f) for f in (2000., 2700., 3400.)]), [])
        self.assertEqual(reference_pairs([event(f) for f in (2000., 2125., 2250., 6000., 6125.)]),
            [(3, 4)])

    def test_disabled_middle_does_not_connect_a_group(self):
        sources = [event(2000.), event(2700., False), event(3400.)]
        self.assertEqual(reference_pairs(sources), [])


if __name__ == "__main__":
    unittest.main()

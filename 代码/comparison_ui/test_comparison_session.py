"""No-GUI checks: actual recorded evidence, corruption, and fair scoring windows."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
HOST = HERE.parent / "host_ui"
if str(HOST) not in sys.path:
    sys.path.insert(0, str(HOST))
from comparison_session import load_actual_capture, measure_pair, payload

ACTUAL = HERE.parent / "host_ui/captures/c4_20261027_223556_432532"


class CaptureValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = np.load(ACTUAL / "input.npy", allow_pickle=False)
        cls.original_meta = json.loads((ACTUAL / "capture.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fpga_comparison_", dir=HERE)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for name in ("capture.json", "input.npy", "output.npy"):
            shutil.copyfile(ACTUAL / name, self.directory / name)

    def write_meta(self, **changes):
        meta = dict(self.original_meta, **changes)
        (self.directory / "capture.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_existing_actual_capture_is_verified_without_rewriting_it(self):
        output, meta = load_actual_capture(ACTUAL, self.expected)
        self.assertEqual(len(output), 64000)
        self.assertEqual(output.dtype, np.dtype("int16"))
        self.assertTrue(np.array_equal(output, np.load(ACTUAL / "output.npy", allow_pickle=False)))
        self.assertTrue(meta["transport_valid"])

    def test_other_input_with_same_count_cannot_reuse_capture(self):
        changed = self.expected.copy()
        changed[100] ^= np.int16(1)
        with self.assertRaisesRegex(ValueError, "input differs"):
            load_actual_capture(self.directory, changed)

    def test_missing_protocol_proof_and_failed_capture_are_rejected(self):
        for field in ("board_verified", "transport_valid", "start_ack_seen", "complete_seen"):
            with self.subTest(field=field):
                self.write_meta(**{field: False})
                with self.assertRaises(ValueError):
                    load_actual_capture(self.directory, self.expected)
        self.write_meta(failure="link interrupted")
        with self.assertRaisesRegex(ValueError, "failed"):
            load_actual_capture(self.directory, self.expected)

    def test_faked_complete_counts_cannot_authorize_comparison(self):
        for field in ("samples_sent", "samples_received", "input_admitted", "input_consumed"):
            with self.subTest(field=field):
                self.write_meta(**{field: len(self.expected) - 1})
                with self.assertRaisesRegex(ValueError, "count"):
                    load_actual_capture(self.directory, self.expected)

    def test_input_and_output_hashes_are_required(self):
        for field in ("input_sha256", "output_sha256"):
            with self.subTest(field=field):
                self.write_meta(**{field: "0" * 64})
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    load_actual_capture(self.directory, self.expected)

    def test_changed_output_and_partial_prefix_are_rejected(self):
        output = np.load(self.directory / "output.npy", allow_pickle=False)
        changed = output.copy()
        changed[100] ^= np.int16(1)
        np.save(self.directory / "output.npy", changed)
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_actual_capture(self.directory, self.expected)
        np.save(self.directory / "output.npy", output[:-1])
        with self.assertRaisesRegex(ValueError, "complete"):
            load_actual_capture(self.directory, self.expected)

    def test_wrong_dtype_cannot_be_silently_quantized(self):
        np.save(self.directory / "output.npy", np.zeros(len(self.expected), dtype=np.int32))
        with self.assertRaisesRegex(ValueError, "int16"):
            load_actual_capture(self.directory, self.expected)


class PairScoring(unittest.TestCase):
    def setUp(self):
        self.target_int = np.random.default_rng(27).integers(-5000, 5000, 2048, dtype=np.int16)
        self.target = self.target_int.astype(float) / 32768
        self.data = (self.target_int.astype(np.int32) + 1000).astype(np.int16)
        self.board = np.zeros(len(self.data), dtype=np.int16)
        self.software = np.zeros(len(self.data), dtype=np.int16)
        self.board[320:] = self.target_int[:-320] + 4
        self.software[320:] = self.target_int[:-320] + 8

    def score(self, target=True, **kwargs):
        return measure_pair(self.data, self.board, self.software,
                            self.target if target else None, {},
                            kwargs.get("start", .008), kwargs.get("width", .020))

    def test_same_window_same_reference_and_fixed_delay(self):
        result = self.score()
        self.assertEqual((result["first_sample"], result["stop_sample_exclusive"], result["common_samples"]),
                         (256, 896, 640))
        for name, output in (("board", self.board), ("software", self.software)):
            metrics = result[name]
            expected = 100 * np.linalg.norm(output[576:1216] / 32768 - self.target[256:896]) / np.linalg.norm(self.target[256:896])
            self.assertAlmostEqual(metrics["output_nrmse_pct"], expected, places=12)
            self.assertEqual(metrics["alignment_samples"], 320)
            self.assertEqual(metrics["common_samples"], 640)
        self.assertAlmostEqual(result["relative_reduction_pct"], 50.0, places=12)
        json.dumps(result, allow_nan=False)

    def test_tail_uses_identical_existing_samples_not_zero_padding(self):
        result = self.score(start=.04, width=.1)
        self.assertEqual((result["first_sample"], result["stop_sample_exclusive"], result["common_samples"]),
                         (1280, 1728, 448))
        self.assertEqual(result["board"]["common_samples"], result["software"]["common_samples"])

    def test_no_target_keeps_fidelity_unavailable(self):
        result = self.score(target=False)
        for name in ("board", "software"):
            self.assertIsNone(result[name]["output_nrmse_pct"])
            self.assertIsNone(result[name]["nrmse_percent"])
        self.assertEqual(result["common_samples"], 640)
        self.assertIsNone(result["relative_reduction_pct"])

    def test_absent_outputs_and_zero_error_do_not_create_fake_relative_scores(self):
        result = measure_pair(self.data, None, self.software, self.target, {}, .008, .020)
        self.assertIsNone(result["board"])
        self.assertIsNone(result["relative_reduction_pct"])
        empty = measure_pair(self.data, None, None, self.target, {}, .008, .020)
        self.assertEqual(empty["common_samples"], 0)
        exact = self.software.copy()
        exact[320:] = self.target_int[:-320]
        for board, software in ((exact, self.software), (self.board, exact)):
            result = measure_pair(self.data, board, software, self.target, {}, .008, .020)
            self.assertIsNone(result["relative_reduction_pct"])

    def test_partial_output_is_not_a_complete_comparison(self):
        with self.assertRaisesRegex(ValueError, "complete"):
            measure_pair(self.data, self.board[:-1], self.software, self.target, {}, 0, .1)

    def test_payload_is_endian_stable_and_does_not_accept_other_precisions(self):
        values = np.array([-32768, -1, 0, 32767], dtype=np.int16)
        self.assertEqual(payload(values), payload(values.astype(">i2")))
        self.assertEqual(hashlib.sha256(payload(values)).hexdigest(),
                         hashlib.sha256(values.astype("<i2").tobytes()).hexdigest())
        for wrong in (values.astype(np.int32), values.astype(np.uint16), values.reshape(2, 2)):
            with self.assertRaises(ValueError):
                payload(wrong)


if __name__ == "__main__":
    unittest.main()

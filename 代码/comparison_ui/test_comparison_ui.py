"""Hidden-Tk integration checks using saved outputs as labelled test fixtures.

No new detector computation, serial I/O, FPGA run, or software-reference run is
performed.  Runtime files are isolated in a temporary test directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import numpy as np
import serial

HERE = Path(__file__).resolve().parent
RELEASE = HERE.parent
os.environ["FPGA_COMPARE_RELEASE"] = str(RELEASE)

from compare_app import ComparisonInstrument, digest

ACTUAL = RELEASE / "host_ui/captures/c4_20261027_223556_432532"
REFERENCE = RELEASE / "validation/recorded_1024_reference"


class ComparisonHiddenTk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.software_fixture = np.load(REFERENCE / "output_1024_software.npy", allow_pickle=False)
        record = json.loads((REFERENCE / "results.json").read_text(encoding="utf-8"))
        assert digest(cls.software_fixture) == record["software_output_payload_sha256"]
        assert digest(np.load(ACTUAL / "input.npy", allow_pickle=False)) == record["input"]["payload_sha256"]

    def setUp(self):
        self.serial_guard = patch.object(serial, "Serial", side_effect=AssertionError("Serial I/O forbidden in GUI tests"))
        self.serial_mock = self.serial_guard.start()
        self.addCleanup(self.serial_guard.stop)
        self.engine_guard = patch("compare_app.run_reference", side_effect=AssertionError("Reference engine forbidden in fixture tests"))
        self.engine_mock = self.engine_guard.start()
        self.addCleanup(self.engine_guard.stop)
        self.message_guard = patch("compare_app.messagebox.showerror", side_effect=AssertionError("Unexpected GUI error dialog"))
        self.message_mock = self.message_guard.start()
        self.addCleanup(self.message_guard.stop)

        original_init = tk.Toplevel.__init__

        def hidden_toplevel(instance, *args, **kwargs):
            original_init(instance, *args, **kwargs)
            instance.withdraw()

        self.toplevel_guard = patch.object(tk.Toplevel, "__init__", hidden_toplevel)
        self.toplevel_guard.start()
        self.addCleanup(self.toplevel_guard.stop)
        self.temporary = tempfile.TemporaryDirectory(prefix="hidden_comparison_", dir=HERE)
        self.addCleanup(self.temporary.cleanup)
        self.scratch = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.cleanup_tk)
        self.app = ComparisonInstrument(self.root, self.scratch, 4)
        self.app.load_capture(ACTUAL)

    def cleanup_tk(self):
        try:
            self.app.close()
        except (AttributeError, tk.TclError):
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def open_fixture(self):
        self.app.open_comparison()
        window = self.app.comparison_window
        self.assertIsNotNone(window)
        self.assertEqual(window.state.get().startswith("输入已固定"), True)
        window.software = self.software_fixture.copy()
        window.software_meta = {"fixture_only": True, "software_reference": True,
                                "note": "Saved output injected only to test display/scoring; not a new computation."}
        window.redraw()
        return window

    def test_full_and_stable_metrics_do_not_modify_actual_samples(self):
        before = self.app.output.copy()
        before_hash = hashlib.sha256(before.tobytes()).hexdigest()
        window = self.open_fixture()
        self.assertEqual(window.values["board"].get(), "105.06 %")
        self.assertEqual(window.values["software"].get(), "127.50 %")
        self.assertEqual(window.values["relative"].get(), "+17.60 %")
        self.assertEqual(window.last_metrics["common_samples"], 63680)
        self.assertFalse(window.data.flags.writeable)
        self.assertFalse(np.shares_memory(window.data, self.app.data))
        self.assertFalse(np.shares_memory(window.board, self.app.output))

        window.start.set("0.4")
        window.width.set("800")
        window.redraw()
        self.assertEqual(window.values["board"].get(), "16.74 %")
        self.assertEqual(window.values["software"].get(), "16.26 %")
        self.assertLess(window.last_metrics["relative_reduction_pct"], 0)
        self.assertTrue(window.values["relative"].get().startswith("-"))
        self.assertEqual(window.last_metrics["first_sample"], 12800)
        self.assertEqual(window.last_metrics["common_samples"], 25600)
        self.assertTrue(np.array_equal(before, self.app.output))
        self.assertEqual(before_hash, hashlib.sha256(self.app.output.tobytes()).hexdigest())
        self.assertEqual(self.app.received, 64000)
        self.serial_mock.assert_not_called()
        self.engine_mock.assert_not_called()

    def test_plain_wav_has_no_invented_target_or_fidelity(self):
        wav = self.scratch / "plain_input.wav"
        shutil.copyfile(ACTUAL / "input.wav", wav)
        self.app.load_input_path(wav)
        self.assertNotIn("target", self.app.components)
        self.assertEqual(self.app.received, 0)
        self.app.open_comparison()
        window = self.app.comparison_window
        window.attach_board(ACTUAL)
        window.software = self.software_fixture.copy()
        window.redraw()
        self.assertIsNone(window.target)
        for name in ("board", "software"):
            self.assertEqual(window.values[name].get(), "无目标参考")
            self.assertIsNone(window.last_metrics[name]["output_nrmse_pct"])
        self.assertEqual(window.values["relative"].get(), "—")
        self.assertIsNone(window.last_metrics["relative_reduction_pct"])
        self.assertEqual(window.last_metrics["common_samples"], 63680)
        self.assertEqual(self.app.received, 0)

    def test_replaced_input_reopens_new_snapshot_without_old_board_or_software(self):
        old = self.open_fixture()
        old_hash = old.input_hash
        changed = self.app.data.copy()
        changed[100] ^= np.int16(1)
        path = self.scratch / "new_input.npy"
        np.save(path, changed)
        self.app.load_input_path(path)
        self.assertEqual(self.app.received, 0)
        self.assertIsNone(self.app.capture_dir)
        self.app.open_comparison()
        window = self.app.comparison_window
        self.assertTrue(old.closed)
        self.assertFalse(old.winfo_exists())
        self.assertNotEqual(old_hash, window.input_hash)
        self.assertEqual(window.input_hash, digest(changed))
        self.assertIsNone(window.board)
        self.assertIsNone(window.software)
        self.assertIsNone(window.board_directory)
        self.assertEqual(window.last_metrics["common_samples"], 0)

    def test_mismatched_worker_input_hash_is_not_published(self):
        self.app.open_comparison()
        window = self.app.comparison_window
        np.save(window.directory / "output_1024.npy", self.software_fixture)
        # Replace only the isolated test event channel; no worker is started.
        original_events = window.events
        window.events = queue.Queue()
        original_events.close()
        window.events.put(("finished", {
            "input_sha256": "0" * 64, "sample_count": len(window.data),
            "output_sha256": digest(self.software_fixture),
        }))
        window.poll()
        self.assertIsNone(window.software)
        self.assertIn("结果未采纳", window.state.get())
        self.assertIn("不一致", window.state.get())
        self.assertIsNotNone(window.board)
        self.assertEqual(window.values["software"].get(), "—")

    def test_reopening_does_not_leave_a_destroyed_window_timer(self):
        self.root.tk.eval("set ::comparison_test_bgerrors {}")
        self.root.tk.eval("proc bgerror {message} {lappend ::comparison_test_bgerrors $message}")
        self.app.open_comparison()
        self.app.open_comparison()
        self.root.after(180, self.root.quit)
        self.root.mainloop()
        errors = self.root.tk.splitlist(self.root.tk.call("set", "::comparison_test_bgerrors"))
        self.assertEqual(errors, ())

    def test_invalid_window_cannot_overwrite_saved_metrics(self):
        window = self.open_fixture()
        window.write_metrics()
        paths = [window.directory / name for name in ("comparison.json", "metrics.csv")]
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
        window.start.set("-1")
        self.assertFalse(window.redraw())
        window.save()
        self.assertEqual(before, [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths])

        # A real completion callback also must not persist stale valid-window
        # metrics when the current text field contains an invalid start.
        np.save(window.directory / "output_1024.npy", self.software_fixture)
        original_events = window.events
        window.events = queue.Queue()
        original_events.close()
        window.events.put(("finished", {
            "input_sha256": window.input_hash, "sample_count": len(window.data),
            "output_sha256": digest(self.software_fixture),
        }))
        window.poll()
        self.assertEqual(before, [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths])


if __name__ == "__main__":
    unittest.main()

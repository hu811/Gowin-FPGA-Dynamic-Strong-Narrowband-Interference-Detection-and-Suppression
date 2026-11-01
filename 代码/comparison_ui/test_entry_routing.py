"""Unified entry checks; all board calls are forbidden or explicit test doubles.

The paired-flow test writes labelled temporary fixtures, not board evidence.
No reference computation, UART traffic, or firmware operation is performed.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import serial

HERE=Path(__file__).resolve().parent
RELEASE=HERE.parent
os.environ['FPGA_COMPARE_RELEASE']=str(RELEASE)
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(RELEASE/'host_ui'))
sys.path.insert(0,str(RELEASE/'recorded_experiment'))
from compare_app import ComparisonInstrument

spec=importlib.util.spec_from_file_location('entry_routing_paired',RELEASE/'recorded_experiment/run_paired.py')
paired=importlib.util.module_from_spec(spec)
spec.loader.exec_module(paired)


class EntryRouting(unittest.TestCase):
    def test_common_and_preloaded_launchers_select_current_interface(self):
        generic=(RELEASE/'RUN_C4_GUI.bat').read_text(encoding='utf-8')
        self.assertFalse((RELEASE/'RUN_C4_COMPARISON.bat').exists())
        self.assertIn('%*',generic)
        self.assertIn('comparison_ui\\compare_app.py',generic)
        for name,folder in [('CONTROL','drm_recorded_two_pairs_no_interference'),('DRM','drm_recorded_two_pairs')]:
            script=(RELEASE/f'RUN_RECORDED_{name}.bat').read_text(encoding='utf-8')
            self.assertIn('RUN_C4_GUI.bat',script)
            self.assertIn('--input',script)
            self.assertIn(folder+'\\input.wav',script)
            self.assertNotIn('--capture',script)
        paired_script=(RELEASE/'RUN_RECORDED_PAIRED_TEST.bat').read_text(encoding='utf-8')
        self.assertIn('recorded_experiment\\run_paired.py',paired_script)
        self.assertIn('%*',paired_script)
        self.assertFalse((RELEASE/'recorded_experiment/RUN_PAIRED_TEST.bat').exists())

    def test_frozen_inputs_and_core_preflight_still_pass(self):
        with patch.object(serial,'Serial',side_effect=AssertionError('UART forbidden')) as uart:
            _,loaded,report=paired.load_frozen(RELEASE)
        self.assertEqual(report['status'],'INPUTS_VERIFIED')
        self.assertEqual(list(loaded),['control','mixed'])
        self.assertTrue(all(len(v[0])==64000 for v in loaded.values()))
        uart.assert_not_called()

    def test_generated_replay_targets_current_interface_with_clean_crlf(self):
        with tempfile.TemporaryDirectory(prefix='routing_replay_',dir=HERE) as directory:
            session=Path(directory)
            paired.replay_entry(session,'mixed',RELEASE)
            raw=(session/'OPEN_MIXED_RESULT.bat').read_bytes()
        self.assertNotIn(b'\r\r\n',raw)
        self.assertIn(b'comparison_ui\\compare_app.py',raw)
        self.assertIn(b'--capture "%~dp0mixed"',raw)
        self.assertNotIn(b'--input',raw)

    def test_mocked_pair_keeps_control_mixed_order_then_opens_current_gui(self):
        _,loaded,_=paired.load_frozen(RELEASE)
        order=[]

        def capture(port,capacity,data,folder,**kwargs):
            folder.mkdir(parents=True)
            order.append((folder.name,capacity,len(data)))
            self.assertTrue(np.array_equal(data,loaded[folder.name][0]))
            return {'metadata':{'board_verified':True,'transport_valid':True},'output':np.zeros(64000,dtype=np.int16)}

        fake_board=SimpleNamespace(capture=capture)
        fake_analysis={'runs':{case:{'metrics':{window:{'output_nrmse_pct':0.} for window in ('full_common','stable')}} for case in ('control','mixed')}}
        with tempfile.TemporaryDirectory(prefix='routing_pair_fixture_',dir=HERE) as directory, \
             patch.object(serial,'Serial',side_effect=AssertionError('UART forbidden')) as uart, \
             patch.object(paired,'HERE',Path(directory)), \
             patch.object(paired,'load_frozen',return_value=(fake_board,loaded,{'reference':'TEST FIXTURE ONLY'})), \
             patch.object(paired,'choose_port',return_value='TEST_DOUBLE'), \
             patch('analyze_pair.analyze_session',return_value=fake_analysis), \
             patch.object(paired.subprocess,'Popen') as popen:
            (Path(directory)/'experiment.json').write_bytes((RELEASE/'recorded_experiment/experiment.json').read_bytes())
            session=paired.run(RELEASE)
            command=popen.call_args.args[0]
            self.assertEqual(Path(command[1]),RELEASE/'comparison_ui/compare_app.py')
            self.assertEqual(Path(command[-1]),session/'mixed')
            for case in ('control','mixed'):
                self.assertIn(b'comparison_ui\\compare_app.py',(session/f'OPEN_{case.upper()}_RESULT.bat').read_bytes())
            uart.assert_not_called()
        self.assertEqual(order,[('control',4,64000),('mixed',4,64000)])

    def test_preloaded_current_interface_does_not_start_transport(self):
        _,loaded,_=paired.load_frozen(RELEASE)
        with patch.object(serial,'Serial',side_effect=AssertionError('UART forbidden')) as uart, \
             patch('compare_app.run_reference',side_effect=AssertionError('Reference execution forbidden')), \
             patch('compare_app.messagebox.showerror',side_effect=AssertionError('Unexpected dialog')):
            for case,folder in paired.CASES.items():
                with self.subTest(case=case),tempfile.TemporaryDirectory(prefix='routing_tk_',dir=HERE) as directory:
                    root=tk.Tk();root.withdraw()
                    app=None
                    try:
                        app=ComparisonInstrument(root,Path(directory),4)
                        app.load_input_path(RELEASE/'real_recorded_inputs/prepared'/folder/'input.wav')
                        root.update_idletasks()
                        self.assertEqual(root.title(),'ACG525 · 可变输入对照')
                        self.assertTrue(np.array_equal(app.data,loaded[case][0]))
                        self.assertIn('target',app.components)
                        self.assertEqual(app.received,0)
                        self.assertFalse(app.busy)
                        self.assertTrue(app.compare_button.winfo_exists())
                    finally:
                        if app is not None:app.close()
                        else:root.destroy()
            uart.assert_not_called()


if __name__=='__main__':unittest.main()

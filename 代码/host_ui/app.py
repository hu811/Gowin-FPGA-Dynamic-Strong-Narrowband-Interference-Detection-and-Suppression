"""V36-compatible WAV experiment host for the existing V32 C4 firmware."""
from __future__ import annotations
import argparse
import copy
import csv
from datetime import datetime
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from scene import FS, MAX_SAMPLES, default_config, generate_scene
from scene_modes import MODE_LABELS, preset_config
import board_io
from plots import SignalPlots, DELAY
from metrics import compute_metrics
from wav_bundle import load_import_bundle

BG, PANEL, INK, MUTED = '#ffffff', '#f7f8fa', '#171719', '#777b82'
ACCENT, BORDER = '#171719', '#e8eaed'


class RoundedButton(tk.Canvas):
    """Small rounded action, with the same state API used by the toolbar."""
    def __init__(self, parent, text, command, primary=False, width=112):
        super().__init__(parent, width=width, height=36, bg=BG, highlightthickness=0,
                         borderwidth=0, takefocus=1, cursor='hand2')
        self.caption, self.command, self.primary = text, command, primary
        self.enabled, self.hovered = True, False
        self.bind('<Configure>', lambda event: self._draw())
        self.bind('<Enter>', lambda event: self._hover(True))
        self.bind('<Leave>', lambda event: self._hover(False))
        self.bind('<Button-1>', self._invoke)
        self.bind('<Return>', self._invoke)
        self.bind('<space>', self._invoke)
        self.bind('<FocusIn>', lambda event: self._draw())
        self.bind('<FocusOut>', lambda event: self._draw())
        self._draw()

    def _hover(self, active):
        self.hovered = active
        self._draw()

    def _invoke(self, event=None):
        if self.enabled:
            self.focus_set()
            self.command()

    def configure(self, cnf=None, **kwargs):
        if 'state' in kwargs:
            self.enabled = kwargs.pop('state') != 'disabled'
            super().configure(cursor='hand2' if self.enabled else 'arrow')
            self._draw()
        if cnf is not None or kwargs:
            return super().configure(cnf, **kwargs)

    config = configure

    def _draw(self):
        if not hasattr(self, 'enabled'):
            return
        self.delete('all')
        w = max(int(self.cget('width')), self.winfo_width())
        h, r = 36, 11
        fill, color = ('#343438' if self.hovered else ACCENT), 'white'
        if not self.enabled:
            fill, color = '#f0f0f3', '#b0b0b5'
        points = (r, 1, w-r, 1, w-1, 1, w-1, r, w-1, h-r, w-1, h-1,
                  w-r, h-1, r, h-1, 1, h-1, 1, h-r, 1, r, 1, 1)
        self.create_polygon(points, smooth=True, splinesteps=20, fill=fill,
                            outline=ACCENT if self.focus_get() is self else fill, width=1)
        self.create_text(w/2, h/2, text=self.caption, fill=color, font=('Microsoft YaHei UI', 10))


def capture_worker(port, capacity, data, directory, metadata, config, components, events, cancel):
    """Separate process keeps rasterization and UI work outside the serial service."""
    result = None
    pending = []
    first_pending = 0
    expected = 0
    last_frame = None
    last_notice = time.perf_counter()
    def flush():
        nonlocal first_pending, last_notice
        if pending:
            events.put(('samples', (first_pending, np.asarray(pending, dtype=np.int16))))
            pending.clear()
        if last_frame is not None:
            events.put(('snapshot', last_frame))
        first_pending = expected
        last_notice = time.perf_counter()
    def notify(frame):
        nonlocal expected, last_frame, first_pending
        last_frame = frame
        if frame.count:
            if frame.first_index != expected:
                raise ValueError('Verified callback sample index is discontinuous')
            if not pending:
                first_pending = frame.first_index
            pending.extend(frame.samples)
            expected += frame.count
        if time.perf_counter()-last_notice >= .05:
            flush()
    try:
        result = board_io.capture(port, capacity, data, directory, source_metadata=metadata,
                                  on_frame=notify,
                                  on_log=lambda text: events.put(('log', text)),
                                  cancelled=cancel.is_set)
        flush()
        events.put(('finished', {'output': result['output'], 'metadata': result['metadata']}))
    except Exception as exc:
        # Exceptions with custom constructors do not round-trip reliably through
        # multiprocessing. Send only explicit, serializable failure evidence.
        result = {'output': getattr(exc, 'output', np.empty(0, dtype=np.int16))}
        flush()
        events.put(('failed', {'message': str(exc), 'output': result['output']}))
    finally:
        try:
            if directory.exists():
                (directory / 'scene.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
                if components:
                    np.savez_compressed(directory / 'source_components.npz', **components)
                if result is not None:
                    output = result['output']
                    with (directory / 'samples.csv').open('w', encoding='utf-8-sig', newline='') as handle:
                        writer = csv.writer(handle)
                        writer.writerow(['sample_index', 'time_s', 'input_int16', 'fpga_output_int16'])
                        writer.writerows((i, f'{i/FS:.8f}', int(data[i]), int(value))
                                         for i, value in enumerate(output))
        except Exception as exc:
            events.put(('save_error', str(exc)))
        events.put(('worker_done', None))


class Instrument:
    def __init__(self, root, release_root: Path, capacity: int = 4):
        self.root, self.release_root = root, release_root
        self.storage = Path(__file__).resolve().parent / 'captures'
        self.process_context = mp.get_context('spawn')
        self.events = self.process_context.Queue()
        self.cancel = self.process_context.Event()
        self.process = None
        self.dead_since = None
        self.busy = False
        self.ui_sequence_fault = False
        self.run_failed = False
        self.loaded_capture = False
        self.received = 0
        self.output = np.empty(0, dtype=np.int16)
        self.data = np.empty(0, dtype=np.int16)
        self.metadata, self.components = {}, {}
        self.capture_dir = None
        self.imported_path = None
        self.last_draw = 0.
        self.dirty = True
        self.latest_snapshot = None
        self.configuration = default_config(capacity)
        self.capacity = tk.IntVar(value=capacity)
        self.firmware_label = tk.StringVar(value=f'{capacity} 中心固件')
        self.capacity.trace_add('write', lambda *_: self.firmware_label.set(f'{self.capacity.get()} 中心固件'))
        self.mode_choice = tk.StringVar(value=MODE_LABELS['two_pairs'])
        self.port = tk.StringVar(value='COM9')
        self.source_label = tk.StringVar(value='场景生成器')
        self.status = tk.StringVar(value='预览输入后，点击“开始收发”。')
        self.telemetry = tk.StringVar(value='跟踪槽位 —     活动位 —     等待槽 — / 2     任务 —     配置提交 —     丢样 —')
        self.detail = tk.StringVar(value='32 kHz · 16 位单通道数字样本 · 512 主检测')
        self.gain = tk.StringVar(value='')
        self.start = tk.DoubleVar(value=0.)
        self.width = tk.StringVar(value='80')
        self.fmax = tk.StringVar(value='8000')
        self.follow = tk.BooleanVar(value=True)
        self.align = tk.BooleanVar(value=True)
        self.same_scale = tk.BooleanVar(value=False)
        self.fidelity = tk.StringVar(value='绿色为生成器的干净目标参考；不是电脑计算的滤波输出。')
        self.metric_values = {key: tk.StringVar(value='—') for key in ('error', 'reduction', 'attenuation', 'midpoint')}
        self.input_error = tk.StringVar(value='输入参考 —')
        self.metric_cards = {}
        self.last_metrics = {}
        self.metric_note = tk.StringVar(value='输入预览')
        self.globals = {}
        self.rows = []
        self.editables = []
        self.generator_inputs = []
        self.root.title('ACG525 · WAV数字验证')
        self.root.geometry('1480x900')
        self.root.minsize(1240, 780)
        self.root.configure(bg=BG)
        self._styles()
        self._build()
        self.set_config(self.configuration)
        self.preview()
        self.refresh_ports()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(60, self.poll)

    def _styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 9), background=BG, foreground=INK)
        style.configure('TFrame', background=BG)
        style.configure('Panel.TFrame', background=PANEL)
        style.configure('TLabel', background=BG)
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 18, 'bold'))
        style.configure('Metric.TLabel', background=PANEL, font=('Segoe UI', 21), foreground=INK)
        style.configure('MetricRows.TLabel', background=PANEL, font=('Microsoft YaHei UI', 10), foreground=INK)
        style.configure('MetricName.TLabel', background=PANEL, foreground=MUTED, font=('Microsoft YaHei UI', 9))
        style.configure('MetricHint.TLabel', background=PANEL, foreground=MUTED, font=('Microsoft YaHei UI', 8))
        style.configure('Muted.TLabel', foreground=MUTED)
        style.configure('TButton', padding=(9, 6), background=INK, foreground='white', borderwidth=0, relief='flat', focusthickness=1, focuscolor='#777b82')
        style.map('TButton', background=[('disabled', '#ededf0'), ('active', '#343438')], foreground=[('disabled', '#a1a3a8'), ('!disabled', 'white')])
        style.configure('TEntry', padding=5, fieldbackground=BG, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.map('TEntry', bordercolor=[('focus', ACCENT)])
        style.configure('TCombobox', padding=4, fieldbackground=BG, background=PANEL, bordercolor=BORDER, arrowcolor=MUTED)
        style.configure('TNotebook', background=BG, borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG, tabmargins=(0, 0, 0, 8))
        style.configure('TNotebook.Tab', background=PANEL, padding=(13, 8), borderwidth=0)
        style.map('TNotebook.Tab', background=[('selected', BG)], foreground=[('selected', INK), ('!selected', MUTED)])
        style.configure('TSeparator', background=BORDER)
        for widget in ('TCheckbutton', 'TRadiobutton'):
            style.configure(widget, background=BG, foreground=INK, indicatorbackground=BG, indicatorforeground=INK, focuscolor=BG)
            style.map(widget, background=[('active', BG)], indicatorbackground=[('selected', INK), ('!selected', BG)])
        style.configure('TLabelframe', background=BG, borderwidth=0, bordercolor=BG)
        style.configure('TLabelframe.Label', font=('Microsoft YaHei UI', 10, 'bold'), background=BG, foreground=INK)

    def _build(self):
        header = ttk.Frame(self.root, padding=(24, 18, 24, 4))
        header.pack(fill='x')
        ttk.Label(header, text='动态干扰测试', style='Title.TLabel').pack(side='left')
        ttk.Label(header, text='数字输入 · 32 kHz', style='Muted.TLabel').pack(side='right')
        bar = ttk.Frame(self.root, padding=(24, 8, 24, 14))
        bar.pack(fill='x')
        ttk.Label(bar, textvariable=self.firmware_label, style='Muted.TLabel').pack(side='left')
        ttk.Label(bar, text='串口').pack(side='left', padx=(16, 4))
        self.ports = ttk.Combobox(bar, textvariable=self.port, width=10)
        self.ports.pack(side='left')
        self.editables.append(self.ports)
        self.refresh_button = ttk.Button(bar, text='刷新串口', command=self.refresh_ports)
        self.refresh_button.pack(side='left', padx=6)
        self.editables.append(self.refresh_button)
        self.run_button = RoundedButton(bar, text='开始收发', primary=True, command=self.run)
        self.run_button.pack(side='right')
        self.stop_button = RoundedButton(bar, text='停止', command=self.cancel.set, width=76)
        self.stop_button.configure(state='disabled')
        self.stop_button.pack(side='right', padx=8)
        body = ttk.Frame(self.root)
        body.pack(fill='both', expand=True, padx=24, pady=(0, 10))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        left = ttk.Frame(body, width=390)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
        left.grid_propagate(False)
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky='nsew')
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, minsize=210)
        self._settings(left)
        meters = ttk.Frame(right, padding=(14, 0, 0, 0))
        meters.grid(row=0, column=1, rowspan=3, sticky='nsew')
        ttk.Label(meters, text='所选区间指标', font=('Microsoft YaHei UI', 10, 'bold')).pack(anchor='w', pady=(4, 12))
        for key, label, hint in (
                ('error', '恢复误差 · NRMSE', '越低越好'),
                ('reduction', '误差降低', '正值表示改善'),
                ('attenuation', '干扰频带衰减 · 估计', '每路单列 · 正值表示抑制'),
                ('midpoint', '中点谱差 · 估计', '相对目标参考')):
            card = ttk.Frame(meters, style='Panel.TFrame', padding=(14, 9))
            card.pack(fill='x', pady=(0, 10))
            self.metric_cards[key] = card
            ttk.Label(card, text=label, style='MetricName.TLabel').pack(anchor='w')
            ttk.Label(card, textvariable=self.metric_values[key],
                      style='Metric.TLabel' if key in ('error', 'reduction') else 'MetricRows.TLabel',
                      justify='left').pack(anchor='w', pady=(6, 4))
            if key == 'error':
                ttk.Label(card, textvariable=self.input_error, style='MetricHint.TLabel').pack(anchor='w', pady=(0, 4))
            if key in ('attenuation', 'midpoint'):
                ttk.Label(card, text=hint, style='MetricHint.TLabel', wraplength=180).pack(anchor='w')
        self.metric_note_label = ttk.Label(meters, textvariable=self.metric_note, style='Muted.TLabel',
                                          wraplength=195, justify='left')
        self.metric_note_label.pack(anchor='w', pady=(2, 0))
        controls = ttk.Frame(right)
        controls.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        ttk.Label(controls, text='观察窗 (ms)').pack(side='left')
        w = ttk.Combobox(controls, textvariable=self.width, width=5, values=('10', '20', '40', '80', '160', '320', '全段'), state='readonly')
        w.pack(side='left', padx=(4, 10))
        w.bind('<<ComboboxSelected>>', lambda e: self.mark_dirty())
        ttk.Label(controls, text='频谱 (Hz)').pack(side='left')
        w = ttk.Combobox(controls, textvariable=self.fmax, width=6, values=('4000', '8000', '12000', '16000'), state='readonly')
        w.pack(side='left', padx=4)
        w.bind('<<ComboboxSelected>>', lambda e: self.mark_dirty())
        ttk.Checkbutton(controls, text='跟随接收', variable=self.follow, command=self.mark_dirty).pack(side='right')
        self.plots = SignalPlots()
        self.canvas = FigureCanvasTkAgg(self.plots.figure, master=right)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky='nsew')
        self.canvas.mpl_connect('button_press_event', self.timeline_click)
        nav = ttk.Frame(right)
        nav.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        ttk.Button(nav, text='全段', command=self.view_all).pack(side='left', padx=(0, 6))
        ttk.Button(nav, text='重叠段', command=self.view_overlap).pack(side='left', padx=(0, 10))
        ttk.Checkbutton(nav, text='对齐 10 ms', variable=self.align, command=self.mark_dirty).pack(side='left')
        ttk.Checkbutton(nav, text='同纵轴', variable=self.same_scale, command=self.mark_dirty).pack(side='left', padx=(6, 0))
        ttk.Button(nav, text='保存图 / 指标', command=self.save_plot).pack(side='right')
        bottom = ttk.Frame(self.root, padding=(24, 6, 24, 14))
        bottom.pack(fill='x', side='bottom', before=body)
        ttk.Label(bottom, textvariable=self.status, font=('Microsoft YaHei UI', 10, 'bold'), wraplength=1300).pack(anchor='w')
        ttk.Label(self.diagnostics, textvariable=self.telemetry, wraplength=350, font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=(10, 8))
        ttk.Label(self.diagnostics, textvariable=self.detail, style='Muted.TLabel', wraplength=350).pack(anchor='w', pady=(3, 0))

    def _settings(self, parent):
        tabs = ttk.Notebook(parent)
        tabs.pack(fill='both', expand=True)
        input_page = ttk.Frame(tabs)
        self.diagnostics = ttk.Frame(tabs, padding=10)
        tabs.add(input_page, text='输入设置')
        tabs.add(self.diagnostics, text='记录')
        help_page = ttk.Frame(tabs, padding=10)
        tabs.add(help_page, text='指标说明')
        inner = ttk.Frame(input_page, padding=(0, 0, 0, 8))
        inner.pack(fill='both', expand=True)
        source = ttk.Labelframe(inner, text='场景', padding=10)
        source.pack(fill='x', pady=(0, 8))
        mode_row = ttk.Frame(source)
        mode_row.pack(fill='x', pady=(0, 6))
        ttk.Label(mode_row, text='预置').pack(side='left', padx=(0, 8))
        self.mode_picker = ttk.Combobox(mode_row, textvariable=self.mode_choice,
                                       values=tuple(MODE_LABELS.values()), state='readonly', width=19)
        self.mode_picker.pack(side='left', fill='x', expand=True)
        self.mode_picker.bind('<<ComboboxSelected>>', lambda event: self.apply_scene_mode())
        self.editables.append(self.mode_picker)
        self.apply_mode_button = ttk.Button(mode_row, text='应用', command=self.apply_scene_mode)
        self.apply_mode_button.pack(side='left', padx=(6, 0))
        self.editables.append(self.apply_mode_button)
        presets = ttk.Frame(source)
        presets.pack(fill='x')
        for column, (text, mode) in enumerate([('无干扰对照', 'quiet'), ('6 s 观察', 'slow')]):
            b = ttk.Button(presets, text=text, command=lambda m=mode: self.preset(m))
            b.grid(row=0, column=column, sticky='ew', padx=(0, 6))
            self.editables.append(b)
        presets.columnconfigure((0, 1), weight=1)
        values = ttk.Frame(source)
        values.pack(fill='x', pady=(8, 0))
        items = [('seconds', '时长 (s)'), ('seed', '种子'),
                 ('target_low_hz', '目标下限 (Hz)'), ('target_high_hz', '上限 (Hz)'),
                 ('target_rms', '目标电平 (FS)'), ('noise_rms', '背景 (FS)')]
        for index, (key, label) in enumerate(items):
            row, col = index // 2, (index % 2) * 2
            ttk.Label(values, text=label).grid(row=row, column=col, sticky='w', pady=3)
            variable = tk.StringVar()
            self.globals[key] = variable
            entry = ttk.Entry(values, textvariable=variable, width=8)
            entry.grid(row=row, column=col+1, sticky='ew', padx=(4, 8), pady=3)
            self.editables.append(entry)
            self.generator_inputs.append(entry)
        events = ttk.Labelframe(inner, text='干扰', padding=10)
        events.pack(fill='x', pady=(0, 8))
        headings = ('开', '频率 (Hz)', 'J/S (dB)', '出现 (ms)', '消失 (ms)', '')
        for c, label in enumerate(headings):
            ttk.Label(events, text=label, style='Muted.TLabel').grid(row=0, column=c, padx=2, sticky='w')
        for r in range(4):
            row = {'enabled': tk.BooleanVar(value=True)}
            self.rows.append(row)
            check = ttk.Checkbutton(events, variable=row['enabled'])
            check.grid(row=r+1, column=0, padx=2, pady=5)
            self.editables.append(check)
            self.generator_inputs.append(check)
            for c, (key, width) in enumerate((('frequency_hz', 7), ('js_db', 4), ('start_ms', 6), ('end_ms', 6)), 1):
                row[key] = tk.StringVar()
                entry = ttk.Entry(events, textvariable=row[key], width=width)
                entry.grid(row=r+1, column=c, padx=2, pady=5, sticky='ew')
                self.editables.append(entry)
                self.generator_inputs.append(entry)
            row['extra'] = {}
            b = ttk.Button(events, text='…', width=2, command=lambda i=r: self.advanced(i))
            b.grid(row=r+1, column=5, padx=2)
            self.editables.append(b)
            self.generator_inputs.append(b)
        actions = ttk.Frame(inner)
        actions.pack(fill='x', pady=(0, 6))
        for column, (label, callback) in enumerate([('预览场景', self.preview), ('导入 WAV / NPY', self.import_input),
                                                   ('打开收发记录', self.open_capture), ('上次完整记录', self.open_last_complete)]):
            b = ttk.Button(actions, text=label, command=callback)
            b.grid(row=column // 2, column=column % 2, sticky='ew', padx=(0, 6), pady=(0, 6))
            self.editables.append(b)
        actions.columnconfigure((0, 1), weight=1)
        files = ttk.Frame(self.diagnostics)
        files.pack(fill='x')
        for label, callback in [('保存场景参数', self.save_config), ('载入场景参数', self.load_config)]:
            b = ttk.Button(files, text=label, command=callback)
            b.pack(side='left', padx=(0, 6))
            self.editables.append(b)
        ttk.Label(self.diagnostics, text='当前输入', font=('Microsoft YaHei UI', 10, 'bold')).pack(anchor='w', pady=(14, 4))
        ttk.Label(self.diagnostics, textvariable=self.source_label, wraplength=350, style='Muted.TLabel').pack(anchor='w', pady=(0, 4))
        ttk.Label(self.diagnostics, textvariable=self.gain, style='Muted.TLabel', wraplength=350).pack(anchor='w', pady=(10, 0))
        ttk.Separator(self.diagnostics).pack(fill='x', pady=12)
        ttk.Label(help_page, text='输入与指标', font=('Microsoft YaHei UI', 10, 'bold')).pack(anchor='w')
        notes = ('当前固件接收 UART 数字样本；信号发生器的模拟输入链路尚未接入。\n\n'
                 'WAV / NPY：32 kHz、单通道、int16。实验包同目录的参考与哈希经核验后仅用于离线显示；普通文件无参考时不显示NRMSE。导入后不再叠加界面场景。\n\n'
                 'NRMSE：相对干净参考的误差范数比，越低越好，无增益拟合。'
                 '误差降低：20 log10(输入误差范数 / 输出误差范数)，正值表示改善。\n\n'
                 '干扰频带衰减：静态干扰附近输入／输出带内功率比，正值表示该频带被抑制；'
                 '这是总混合频带估计，不能单独证明目标保真或每路干扰衰减达标。\n\n'
                 '中点谱差：按输入设定中相距 ≤750 Hz 的两成员频率组选择中点，非板端分组；'
                 '输出 / 干净参考带内功率比受残余干扰影响。'
                 '当前未独立测量目标分量，不能据此验收 ≤1 dB。\n\n'
                 '参考与指标仅供电脑离线展示；在线检测与滤波在 FPGA 内执行。主检测范围 1–7 kHz。')
        ttk.Label(help_page, text=notes, wraplength=350, style='Muted.TLabel').pack(anchor='w', pady=8)

    def set_config(self, config):
        self.configuration = copy.deepcopy(config)
        self.capacity.set(config['capacity'])
        for key, variable in self.globals.items():
            variable.set(str(config.get(key, 0)))
        for i, row in enumerate(self.rows):
            event = config['events'][i] if i < len(config['events']) else {
                'enabled': False, 'frequency_hz': 1000., 'js_db': 20., 'start_s': .2,
                'end_s': 1.5, 'end_frequency_hz': 1000., 'change_s': .8, 'mode': 'fixed', 'phase_rad': 0.}
            row['enabled'].set(event['enabled'])
            for key in ('frequency_hz', 'js_db'):
                row[key].set(f"{event[key]:g}")
            row['start_ms'].set(f"{event['start_s']*1000:g}")
            row['end_ms'].set(f"{event['end_s']*1000:g}")
            row['extra'] = {key: event[key] for key in ('end_frequency_hz', 'mode', 'change_s', 'phase_rad')}

    def get_config(self):
        config = copy.deepcopy(self.configuration)
        config.update({key: (int(var.get()) if key == 'seed' else float(var.get()))
                       for key, var in self.globals.items()})
        config['capacity'] = self.capacity.get()
        config['events'] = []
        for row in self.rows:
            event = copy.deepcopy(row['extra'])
            event.update(enabled=row['enabled'].get(), frequency_hz=float(row['frequency_hz'].get()),
                         js_db=float(row['js_db'].get()), start_s=float(row['start_ms'].get())/1000,
                         end_s=float(row['end_ms'].get())/1000)
            if event['mode'] == 'fixed':
                event['end_frequency_hz'] = event['frequency_hz']
            config['events'].append(event)
        return config

    def advanced(self, index):
        row = self.rows[index]
        popup = tk.Toplevel(self.root)
        popup.title(f'干扰 {index+1} · 换频设置')
        popup.transient(self.root)
        popup.grab_set()
        frame = ttk.Frame(popup, padding=18)
        frame.pack(fill='both', expand=True)
        values = {}
        labels = [('mode', '方式'), ('end_frequency_hz', '结束 / 换频后 Hz'), ('change_s', '阶跃时刻 s'), ('phase_rad', '初相位 rad')]
        for r, (key, label) in enumerate(labels):
            ttk.Label(frame, text=label).grid(row=r, column=0, sticky='w', pady=5)
            values[key] = tk.StringVar(value=str(row['extra'][key]))
            if key == 'mode':
                entry = ttk.Combobox(frame, textvariable=values[key], state='readonly', values=('fixed', 'step', 'linear'))
            else:
                entry = ttk.Entry(frame, textvariable=values[key])
            entry.grid(row=r, column=1, padx=10, pady=5)
        ttk.Label(frame, text='fixed 固定；step 阶跃；linear 在出现到消失期间连续漂移。',
                  wraplength=380, style='Muted.TLabel').grid(row=4, column=0, columnspan=2, pady=10)
        def accept():
            try:
                extra = {key: (var.get() if key == 'mode' else float(var.get())) for key, var in values.items()}
                if not all(np.isfinite(extra[k]) for k in extra if k != 'mode'):
                    raise ValueError('请输入有限数字。')
                row['extra'] = extra
                popup.destroy()
            except ValueError as exc:
                messagebox.showerror('设置无效', str(exc), parent=popup)
        ttk.Button(frame, text='确定', command=accept).grid(row=5, column=1, sticky='e')

    def apply_scene_mode(self):
        mode = next(key for key, label in MODE_LABELS.items() if label == self.mode_choice.get())
        self.preset(mode)

    def preset(self, mode):
        if self.busy:
            return
        if mode in MODE_LABELS:
            self.mode_choice.set(MODE_LABELS[mode])
        selected_mode = next(key for key, label in MODE_LABELS.items() if label == self.mode_choice.get())
        config = preset_config(selected_mode)
        if mode == 'quiet':
            for event in config['events']:
                event['enabled'] = False
        if mode == 'slow':
            config['seconds'] = 6.0
            starts = (.5, 1.2, 1.208, 1.9) if selected_mode == 'mixed' else (.5, .508, 1.2, 1.208)
            for i, event in enumerate(config['events']):
                event['start_s'] = starts[i]
                event['end_s'] = (4., 4.1, 4.6, 4.7)[i]
                event['change_s'] = (event['start_s']+event['end_s'])/2
        self.set_config(config)
        self.preview()

    def preview(self):
        if self.busy:
            return
        try:
            config = self.get_config()
            data, metadata, components = generate_scene(config)
            self.configuration = config
            self.imported_path = None
            self.set_input(data, metadata, components, '场景输入预览')
        except (ValueError, KeyError, TypeError) as exc:
            messagebox.showerror('场景设置无效', str(exc), parent=self.root)

    def set_input(self, data, metadata, components, label):
        self.data, self.metadata, self.components = data, metadata, components
        self.output = np.empty(len(data), dtype=np.int16)
        self.received = 0
        self.capture_dir = None
        self.latest_snapshot = None
        self.run_failed = False
        self.loaded_capture = False
        self.plots.invalidate()
        self.start.set(0.)
        self.source_label.set(f'{label} · {len(data):,} 样本 / {len(data)/FS:.3f} s')
        if self.imported_path is not None:
            self.source_label.set(self.source_label.get() + '\n导入模式：上方场景参数不应用；点“预览场景”切回生成器。')
        for widget in self.generator_inputs:
            widget.configure(state='disabled' if self.imported_path is not None else 'normal')
        scale = metadata.get('front_end_scale', metadata.get('scale', 1.))
        peak = int(np.max(np.abs(data.astype(np.int32))))
        self.gain.set(f'统一输入增益 {scale:.5g} · 实际峰值 {peak} / 32768；32 kHz 单通道。')
        unsupported = any(e.get('enabled', True) and not e.get('in_detector_range', True)
                          for e in metadata.get('events', []))
        if metadata.get('over_capacity'):
            self.gain.set(self.gain.get() + '\n此场景同时源数量超过所选容量，作为边界测试，不能保证全部处理。')
        if unsupported:
            self.gain.set(self.gain.get() + '\n含 1–7 kHz 搜索范围外干扰，不保证检出。')
        self.status.set('输入已预览；输出等待 FPGA 回传。')
        self.telemetry.set('跟踪槽位 —     活动位 —     等待槽 — / 2     任务 —     配置提交 —     丢样 —')
        self.mark_dirty()

    def window_seconds(self):
        return max(len(self.data)/FS, .001) if self.width.get() == '全段' else float(self.width.get())/1000

    def capture_roots(self):
        roots = [self.storage, self.release_root / 'host_ui' / 'captures',
                 self.release_root.parent / 'pro_v36_host' / 'host_ui' / 'captures',
                 self.release_root.parent / 'pro_v35_host' / 'host_ui' / 'captures',
                 self.release_root.parent / 'pro_v34_host' / 'host_ui' / 'captures',
                 self.release_root.parent / 'pro_v33_host' / 'host_ui' / 'captures',
                 self.release_root.parent / 'pro_v31_ready' / 'host_ui' / 'captures']
        return list(dict.fromkeys(roots))

    def open_capture(self):
        path = filedialog.askdirectory(title='打开已有收发记录（不会连接串口）',
                                       initialdir=next((str(p) for p in self.capture_roots() if p.exists()), str(self.release_root)))
        if path:
            try:
                self.load_capture(Path(path))
            except (ValueError, OSError, KeyError, TypeError) as exc:
                messagebox.showerror('不能打开记录', str(exc), parent=self.root)

    def open_last_complete(self):
        candidates = []
        for base in self.capture_roots():
            if base.is_dir():
                for path in base.iterdir():
                    if (path/'capture.json').is_file():
                        try:
                            meta = json.loads((path/'capture.json').read_text(encoding='utf-8'))
                            if meta.get('transport_valid'):
                                candidates.append(path)
                        except (ValueError, OSError):
                            pass
        if not candidates:
            messagebox.showinfo('还没有完整记录', '可以先运行一次收发，或选择“打开收发记录”。', parent=self.root)
            return
        try:
            self.load_capture(max(candidates, key=lambda p: p.stat().st_mtime_ns))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            messagebox.showerror('不能打开记录', str(exc), parent=self.root)

    def load_capture(self, directory):
        """Replay only saved actual outputs. No reference model is run here."""
        if self.busy:
            raise ValueError('请先停止当前收发。')
        directory = Path(directory)
        meta = json.loads((directory/'capture.json').read_text(encoding='utf-8'))
        data = board_io.load_input(directory/'input.npy')
        output = np.load(directory/'output.npy', allow_pickle=False)
        if output.ndim != 1 or output.dtype != np.dtype('int16') or len(output) > len(data):
            raise ValueError('回传文件必须是有效 int16 已接收前缀。')
        complete = meta.get('transport_valid') is True
        if 'samples_received' in meta and meta['samples_received'] != len(output):
            raise ValueError('记录的已接收计数与输出文件长度不一致。')
        if complete:
            if len(output) != len(data):
                raise ValueError('标为完整的记录没有收齐输出，不能当作成功记录回放。')
            for key in ('samples_sent', 'input_admitted', 'input_consumed'):
                if key in meta and meta[key] != len(data):
                    raise ValueError(f'完整记录的 {key} 与输入长度不一致。')
        for key, values in (('input_sha256', data), ('output_sha256', output)):
            if key in meta and hashlib.sha256(values.astype('<i2').tobytes()).hexdigest() != meta[key]:
                raise ValueError(f'{key} 核对失败，不能将被改动数据作为原记录展示。')
        components = {}
        file = directory/'source_components.npz'
        if file.exists():
            with np.load(file, allow_pickle=False) as archive:
                if 'target' in archive:
                    reference = archive['target']
                    if reference.ndim != 1 or len(reference) != len(data) or not np.all(np.isfinite(reference)):
                        raise ValueError('干净目标参考长度或数值无效。')
                    components['target'] = reference.copy()
        source = meta.get('source_metadata') or {}
        config_path = directory/'scene.json'
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else None
        if config is None:
            config = source.get('config')
        if config is not None:
            self.set_config(config)
        if meta.get('capacity') in (3, 4):
            self.capacity.set(meta['capacity'])
        self.imported_path = directory/'input.npy'
        self.set_input(data, source, components, f'实际记录回放：{directory.name}')
        self.output = np.empty(len(data), dtype=np.int16)
        self.output[:len(output)] = output
        self.received = len(output)
        self.loaded_capture = True
        self.run_failed = not complete
        self.capture_dir = directory
        self.follow.set(False)
        state = '完整记录' if not self.run_failed else f"失败前缀：{meta.get('failure', '未完成')}"
        self.status.set(f'{state} · 实际输出 {self.received:,}/{len(data):,} · 回放未连接串口')
        self.detail.set(f'原记录：{directory}；绿色只用保存的干净目标，缺失输出不补零。')
        self.telemetry.set(f"记录 C{meta.get('capacity', '?')}；该图为历史实际回传，不是新一次板测。")
        self.view_overlap()

    def view_all(self):
        self.follow.set(False)
        self.width.set('全段')
        self.start.set(0.)
        self.mark_dirty()

    def load_input_path(self, path):
        """Load a WAV/NPY or validated offline-reference bundle; never start I/O."""
        if self.busy:
            raise ValueError('请先停止当前收发。')
        path = Path(path).resolve()
        data, metadata, components = load_import_bundle(path)
        if not 64 <= len(data) <= MAX_SAMPLES:
            raise ValueError(f'输入长度须为 64..{MAX_SAMPLES} 样本。')
        self.imported_path = path
        metadata['quality_reference_available'] = 'target' in components
        reference = '已核验离线参考' if 'target' in components else '无干净参考'
        self.set_input(data, metadata, components, f'{path.name} · {reference}')

    def import_input(self):
        path = filedialog.askopenfilename(title='导入 32 kHz 单通道 int16 样本',
                                          filetypes=[('WAV / NPY', '*.wav *.npy')])
        if not path:
            return
        try:
            self.load_input_path(path)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            messagebox.showerror('不能导入', str(exc), parent=self.root)

    def save_config(self):
        try:
            config = self.get_config()
            generate_scene(config)
            path = filedialog.asksaveasfilename(title='保存场景参数', defaultextension='.json', filetypes=[('JSON', '*.json')])
            if path:
                Path(path).write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        except (ValueError, OSError) as exc:
            messagebox.showerror('不能保存', str(exc), parent=self.root)

    def load_config(self):
        path = filedialog.askopenfilename(title='载入场景参数', filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            generate_scene(config)
            self.set_config(config)
            self.preview()
        except (ValueError, OSError, KeyError, TypeError) as exc:
            messagebox.showerror('不能载入', str(exc), parent=self.root)

    def refresh_ports(self):
        if self.busy:
            return
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        self.ports.configure(values=[port.device for port in ports])
        # Keep user choice. Showing inventory must not open or reset any port.
        self.detail.set(' | '.join(f'{port.device}: {port.description}' for port in ports)
                        or '未找到串口；接板上 UART 后刷新。')

    def run(self):
        if self.busy:
            return
        try:
            if self.imported_path is None:
                config = self.get_config()
                data, metadata, components = generate_scene(config)
                self.configuration = config
                self.set_input(data, metadata, components, '本次发送场景')
            if not 64 <= len(self.data) <= MAX_SAMPLES:
                raise ValueError(f'请生成或导入 64..{MAX_SAMPLES} 个样本。')
            port = self.port.get().strip()
            if not port:
                raise ValueError('请选择板上 UART 的 COM 号。')
            capacity = self.capacity.get()
            release = board_io.verify_release(self.release_root, capacity)
            data = self.data.copy()
            meta = copy.deepcopy(self.metadata)
            meta['local_release'] = release
            meta['host_gui_revision'] = 'v36_wav_import_ready'
            config = copy.deepcopy(meta.get('config')) if self.imported_path else copy.deepcopy(self.configuration)
            components = {k: v.copy() for k, v in self.components.items()}
        except (ValueError, OSError, KeyError, RuntimeError) as exc:
            messagebox.showerror('无法开始', str(exc), parent=self.root)
            return
        self.output = np.empty(len(data), dtype=np.int16)
        self.received = 0
        self.plots.invalidate()
        self.ui_sequence_fault = False
        self.run_failed = False
        self.loaded_capture = False
        self.capture_dir = self.storage / f"c{capacity}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        self.cancel.clear()
        self.dead_since = None
        self.set_busy(True)
        self.status.set(f'等待 C{capacity} RESET 应答；之后按 32 kHz 发送并接收实际样本。')
        directory = self.capture_dir
        self.process = self.process_context.Process(
            target=capture_worker, name='V31-UART', daemon=True,
            args=(port, capacity, data, directory, meta, config, components, self.events, self.cancel))
        try:
            self.process.start()
        except (OSError, RuntimeError) as exc:
            self.set_busy(False)
            messagebox.showerror('不能启动收发进程', str(exc), parent=self.root)

    def set_busy(self, busy):
        self.busy = busy
        for widget in self.editables:
            widget.configure(state='disabled' if busy else 'normal')
        self.mode_picker.configure(state='disabled' if busy else 'readonly')
        if not busy and self.imported_path is not None:
            for widget in self.generator_inputs:
                widget.configure(state='disabled')
        self.run_button.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')

    def poll(self):
        needs_draw = False
        ended_successfully = False
        for _ in range(300):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'samples':
                first, values = value
                end = first+len(values)
                if first != self.received or end > len(self.output):
                    self.ui_sequence_fault = True
                    self.run_failed = True
                    self.cancel.set()
                    self.status.set('接收索引不连续，已请求停止；保留失败记录。')
                    continue
                self.output[first:end] = values
                self.received = end
                needs_draw = True
            elif kind == 'snapshot':
                self.latest_snapshot = value
                if not self.ui_sequence_fault:
                    self.update_snapshot(value)
            elif kind == 'frame':
                self.latest_snapshot = value
                if value.count:
                    end = value.first_index + value.count
                    if value.first_index != self.received or end > len(self.output):
                        self.ui_sequence_fault = True
                        self.run_failed = True
                        self.cancel.set()
                        self.status.set('接收索引不连续，已请求停止；保留失败记录。')
                        continue
                    self.output[value.first_index:end] = value.samples
                    self.received = end
                    needs_draw = True
                if not self.ui_sequence_fault:
                    self.update_snapshot(value)
            elif kind == 'log':
                self.detail.set(str(value))
            elif kind == 'finished':
                if (self.ui_sequence_fault or len(value['output']) != len(self.data)
                        or self.received != len(self.data)
                        or not np.array_equal(self.output[:self.received], value['output'])):
                    self.ui_sequence_fault = True
                    self.run_failed = True
                    self.status.set('界面接收序列或最终样本核对失败；本次不算通过。')
                    self.detail.set(f'请保留记录供检查：{self.capture_dir}')
                    needs_draw = True
                    continue
                self.received = len(value['output'])
                self.output[:self.received] = value['output']
                self.status.set(f'收发完整：{self.received:,} / {len(self.data):,} 样本。可拖动观察位置比较；结果已保存。')
                self.detail.set(f'保存于 {self.capture_dir}。传输通过；滤波质量请查看实际波形/频谱和离线记录。')
                needs_draw = True
                ended_successfully = True
            elif kind == 'failed':
                self.run_failed = True
                partial = value['output']
                self.received = min(len(partial), len(self.output))
                self.output[:self.received] = partial[:self.received]
                self.status.set(f"本次未完成：{value['message']}")
                self.detail.set(f'已接收 {self.received:,} / {len(self.data):,}；部分数据（如有）保留于 {self.capture_dir}')
                needs_draw = True
            elif kind == 'save_error':
                self.detail.set('附加场景文件保存失败：' + value)
            elif kind == 'worker_done':
                self.set_busy(False)
        if self.busy and self.process is not None and self.process.exitcode is not None:
            if self.dead_since is None:
                self.dead_since = time.monotonic()
            elif time.monotonic()-self.dead_since > 1.:
                self.run_failed = True
                self.status.set(f'收发进程异常结束（退出码 {self.process.exitcode}）；本次不算通过。')
                self.detail.set(f'当前已收到 {self.received:,} 个样本；请检查 {self.capture_dir} 的部分记录。')
                self.set_busy(False)
                needs_draw = True
        if needs_draw:
            if self.follow.get() and not ended_successfully:
                shift = DELAY if self.align.get() else 0
                self.start.set(max(0., (self.received-shift)/FS-self.window_seconds()))
            if ended_successfully:
                self.view_overlap()
                self.detail.set(f'已定位多干扰重叠段；点“全段”查看启停与末尾。记录：{self.capture_dir}')
            self.dirty = True
        if self.dirty and time.perf_counter()-self.last_draw > .35:
            self.redraw()
        self.root.after(60, self.poll)

    def update_snapshot(self, snapshot):
        frequencies = [f'{freq*FS/2**32:.1f}' if freq else '—' for freq in snapshot.frequencies[:self.capacity.get()]]
        self.telemetry.set(f"跟踪槽位 [{', '.join(frequencies)}] Hz   活动位 0x{snapshot.active:02x}   "
                           f"等待槽 {snapshot.waiting}/2   分组任务 {'运行' if snapshot.task_running else '空闲'}   "
                           f"提交 {snapshot.commits}   丢样 {snapshot.dropped}")
        self.status.set(f'接收 {self.received:,} / {len(self.data):,} · 等待 {snapshot.waiting}/2'
                        f' · 丢样 {snapshot.dropped}'
                        + (f' · 故障 link=0x{snapshot.link_faults:04x} core=0x{snapshot.error_code:02x}'
                           if snapshot.link_faults or snapshot.error_code else ''))

    def mark_dirty(self):
        self.dirty = True

    def redraw(self):
        self.last_draw = time.perf_counter()
        self.dirty = False
        title = ('未完成 · ' if self.run_failed else
                 '记录回放 · ' if self.loaded_capture else
                 '板端回传 · ' if self.received else '输入预览 · ')
        title += f'{self.received:,} / {len(self.data):,} 样本'
        if self.received and self.align.get():
            title += ' · 对齐 10 ms'
        self.plots.update(self.data, self.output[:self.received], self.metadata,
                          start=self.start.get(), width=self.window_seconds(),
                          align=self.align.get(), maximum_hz=float(self.fmax.get()), status=title,
                          clean=self.components.get('target'), same_scale=self.same_scale.get())
        self.fidelity.set(self.plots.metrics)
        self.update_metrics()
        self.plots.render(self.canvas)

    def update_metrics(self):
        self.last_metrics = compute_metrics(
            self.data, self.output[:self.received], self.components.get('target'), self.metadata,
            self.start.get(), self.window_seconds(), self.align.get())
        # Cards are intentionally terse. Exact definitions and unavailable
        # reasons remain in diagnostics and the exported JSON, not pass badges.
        values = self.last_metrics
        error = values.get('nrmse_percent')
        self.metric_values['error'].set('—' if error is None else f"{error['output']:.1f} %")
        self.input_error.set('输入参考 —' if error is None else f"输入 {error['input']:.1f} %")
        reduction = values.get('error_reduction_db')
        self.metric_values['reduction'].set('—' if reduction is None else f'{reduction:+.1f} dB')
        bands = [f"{row['event_label']}    {row['attenuation_db']:.1f} dB"
                 for row in values.get('interferers', []) if row.get('attenuation_db') is not None]
        self.metric_values['attenuation'].set('\n'.join(bands) if bands else '—')
        midpoint = values.get('midpoints', [])
        available = [f"中点 {row['pair_label']}    {row['change_db']:+.2f} dB" for row in midpoint
                     if row.get('change_db') is not None]
        self.metric_values['midpoint'].set('\n'.join(available) if available else '—')
        scope = f"公共观察窗 {values['common_ms']:.1f} ms" if values['common_samples'] else '输入预览 · 等待回传'
        self.metric_note.set(scope + '\n衰减与中点均为混合谱估计；— 表示当前不可测。')

    def timeline_click(self, event):
        if event.inaxes in (self.plots.timeline, self.plots.overview) and event.xdata is not None:
            self.follow.set(False)
            if self.width.get() == '全段':
                self.width.set('80')
            maximum = max(0., len(self.data)/FS-self.window_seconds())
            self.start.set(min(maximum, max(0., event.xdata-self.window_seconds()/2)))
            self.mark_dirty()

    def view_overlap(self):
        events = [e for e in self.metadata.get('events', []) if e.get('enabled', True)]
        if self.width.get() == '全段':
            self.width.set('80')
        width = self.window_seconds()
        candidates = sorted({max(0., float(e['start_s']) + .10) for e in events})
        if candidates:
            position = max(candidates, key=lambda t: sum(e['start_s'] <= t < e['end_s'] for e in events))
            self.start.set(min(max(0., len(self.data)/FS-width), position))
            self.follow.set(False)
            self.mark_dirty()

    def save_plot(self):
        try:
            directory = self.capture_dir or Path(__file__).resolve().parent / 'previews'
            directory.mkdir(parents=True, exist_ok=True)
            name = ('fpga_' if self.received else 'input_preview_') + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.png'
            self.redraw()
            self.plots.save(directory / name)
            report = {'schema': 'v36-selected-window-metrics-1',
                      'scope': 'Offline display metrics of actual received output; no independent target-component protection measurement.',
                      'capture_directory': str(self.capture_dir) if self.capture_dir else None,
                      'received_samples': self.received, 'total_samples': len(self.data),
                      'partial_capture': self.run_failed, 'historical_capture': self.loaded_capture,
                      'input_sha256': hashlib.sha256(self.data.astype('<i2').tobytes()).hexdigest(),
                      'received_output_sha256': hashlib.sha256(self.output[:self.received].astype('<i2').tobytes()).hexdigest(),
                      'metrics': self.last_metrics}
            (directory / name).with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            with (directory / name).with_suffix('.csv').open('w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(('metric', 'value', 'unit', 'scope'))
                for key in ('input', 'output'):
                    error = self.last_metrics['nrmse_percent']
                    writer.writerow((f'{key}_nrmse', None if error is None else error[key], '%', 'fixed alignment; no gain fitting'))
                writer.writerow(('error_reduction', self.last_metrics.get('error_reduction_db'), 'dB',
                                 '20log10(input reference-error norm / output reference-error norm)'))
                for row in self.last_metrics.get('interferers', []):
                    writer.writerow((f"interference_{row['event_label']}", row['attenuation_db'], 'dB',
                                     'mixed input/output band-power estimate; not isolated component attenuation'))
                for row in self.last_metrics['midpoints']:
                    writer.writerow((f"midpoint_{row['pair_label']}", row['change_db'], 'dB', 'mixed-spectrum estimate; not isolated target protection'))
            self.detail.set(f'图已保存：{directory / name}')
        except OSError as exc:
            messagebox.showerror('不能保存图', str(exc), parent=self.root)

    def close(self):
        if self.busy:
            self.cancel.set()
            self.status.set('正在停止串口并保存已接收数据…')
            self.root.after(150, self._close_when_done)
        else:
            self.root.destroy()

    def _close_when_done(self):
        if self.process and self.process.is_alive():
            self.root.after(150, self._close_when_done)
        else:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--capacity', type=int, choices=(3, 4), default=4)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--capture', type=Path, help='Open a saved actual capture; never start serial automatically.')
    source.add_argument('--input', type=Path, help='Preload WAV/NPY or reference bundle; never start serial automatically.')
    parser.add_argument('--self-check', type=Path, help='Create hidden GUI and input-only plot, never open serial.')
    args = parser.parse_args()
    root = tk.Tk()
    if args.self_check:
        root.withdraw()
    app = Instrument(root, args.release_root, args.capacity)
    if args.capture:
        app.load_capture(args.capture)
    if args.input:
        try:
            app.load_input_path(args.input)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            root.destroy()
            parser.error(str(exc))
    if args.self_check:
        args.self_check.mkdir(parents=True, exist_ok=True)
        app.redraw()
        root.update_idletasks()
        app.plots.save(args.self_check / ('capture_replay.png' if args.capture else 'input_preview.png'))
        report = {'scope': 'hidden GUI + saved capture replay; no new hardware or serial I/O' if args.capture else 'hidden GUI + input preview only; no FPGA or serial I/O',
                  'samples': len(app.data), 'returned_samples': app.received,
                  'figure_axes': len(app.plots.figure.axes),
                  'clean_reference_available': 'target' in app.components,
                  'selected_fidelity': app.fidelity.get(),
                  'selected_metrics': app.last_metrics,
                  'metric_cards': {key: variable.get() for key, variable in app.metric_values.items()},
                  'loaded_capture': str(args.capture) if args.capture else None,
                  'imported_input': str(args.input) if args.input else None,
                  'window_requested': [root.winfo_reqwidth(), root.winfo_reqheight()]}
        (args.self_check / 'gui_check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
        root.destroy()
    else:
        root.mainloop()


if __name__ == '__main__':
    mp.freeze_support()
    main()

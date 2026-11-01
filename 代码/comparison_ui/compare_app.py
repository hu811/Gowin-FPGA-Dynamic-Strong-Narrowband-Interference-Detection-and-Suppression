"""Optional same-input comparison UI; original FPGA host and firmware are unchanged."""
from __future__ import annotations
import argparse
import copy
import csv
from datetime import datetime
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import sys
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

HERE = Path(__file__).resolve().parent
bootstrap = argparse.ArgumentParser(add_help=False)
bootstrap.add_argument('--release-root', type=Path,
                       default=Path(os.environ.get('FPGA_COMPARE_RELEASE', HERE.parent)))
RELEASE = bootstrap.parse_known_args()[0].release_root.resolve()
sys.path.insert(0, str(RELEASE/'host_ui'))
from app import Instrument, RoundedButton
from scene import generate_scene, FS
from reference_1024 import run_reference, ReferenceCancelled
from comparison_session import payload, load_actual_capture, measure_pair
from compare_plots import ComparisonPlots


def digest(values):
    return hashlib.sha256(payload(values)).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def reference_worker(data, model_directory, directory, events, cancel):
    """Only mixed samples enter the numeric engine; target/board data stay outside."""
    last = 0.
    def progress(stage, completed, total):
        nonlocal last
        now = time.monotonic()
        if now-last >= .12 or completed == total:
            events.put(('progress', (stage, completed, total)))
            last = now
    try:
        result = run_reference(data, model_directory, progress, cancel.is_set)
        if cancel.is_set():
            raise ReferenceCancelled('Cancelled before saving result')
        output = result['output']
        np.save(directory/'output_1024.npy', output)
        np.savez_compressed(directory/'software_state.npz', banks_q16=result['banks_q16'], trajectory=result['trajectory'])
        write_json(directory/'software_decisions.json', result['decisions'])
        record = dict(schema='same-input-software1024-1', input_sha256=digest(data),
                      output_sha256=digest(output), sample_count=len(data), fs_hz=FS,
                      software_reference=True, fpga_output=False,
                      stats=result['stats'], source_hashes=result['source_hashes'],
                      timing='Offline numerical reference; compute/configuration waiting excluded, not FPGA latency')
        write_json(directory/'software_result.json', record)
        events.put(('finished', record))
    except ReferenceCancelled:
        events.put(('cancelled', None))
    except Exception as error:
        events.put(('failed', f'{type(error).__name__}: {error}'))


class ComparisonWindow(tk.Toplevel):
    def __init__(self, parent, release_root, data, metadata, target=None, actual_directory=None):
        super().__init__(parent)
        self.release_root = Path(release_root)
        self.data = np.asarray(data).copy()
        self.input_hash = digest(self.data)
        self.data.setflags(write=False)
        self.metadata = copy.deepcopy(metadata)
        self.target = None if target is None else np.asarray(target, dtype=float).copy()
        if self.target is not None:
            if self.target.shape != self.data.shape or not np.all(np.isfinite(self.target)):
                raise ValueError('目标参考必须与输入等长且数值有限。')
            self.target.setflags(write=False)
        self.board = self.software = None
        self.board_meta = self.software_meta = None
        self.board_directory = None
        self.directory = self.release_root/'comparison_results'/(
            datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+self.input_hash[:8])
        self.directory.mkdir(parents=True, exist_ok=False)
        np.save(self.directory/'input.npy', self.data)
        if self.target is not None:
            np.save(self.directory/'target_reference.npy', self.target)
        write_json(self.directory/'input_metadata.json', self.metadata)
        self.context = mp.get_context('spawn')
        self.events = self.context.Queue()
        self.cancel = self.context.Event()
        self.process = None
        self.started = False
        self.dead_since = None
        self.closed = False
        self.start = tk.StringVar(value='0')
        self.width = tk.StringVar(value='全段')
        self.fmax = tk.StringVar(value='8000')
        self.state = tk.StringVar(value='输入已固定。点击“计算1024”；更换输入请回主窗口重新预览。')
        self.values = {key: tk.StringVar(value='—') for key in ('board', 'software', 'relative')}
        self.note = tk.StringVar()
        self.last_metrics = None
        self.title('同一输入对照 · 512 FPGA / 1024离线')
        self.geometry('1420x900')
        self.minsize(1120, 760)
        self.configure(bg='white')
        self._build()
        self.protocol('WM_DELETE_WINDOW', self.close)
        if actual_directory is not None:
            self.attach_board(actual_directory)
        self.redraw()
        self.after(100, self.poll)

    def _build(self):
        top = ttk.Frame(self, padding=(20, 14, 20, 8))
        top.pack(fill='x')
        ttk.Label(top, text='同一输入对照', font=('Microsoft YaHei UI', 17, 'bold')).pack(side='left')
        ttk.Label(top, text=f'{len(self.data):,} 样本 · 32 kHz · 输入ID {self.input_hash[:12]}',
                  style='Muted.TLabel').pack(side='right')
        bar = ttk.Frame(self, padding=(20, 0, 20, 8))
        bar.pack(fill='x')
        self.run_button = ttk.Button(bar, text='计算1024', command=self.run_reference)
        self.run_button.pack(side='left')
        self.cancel_button = ttk.Button(bar, text='取消计算', command=self.cancel.set, state='disabled')
        self.cancel_button.pack(side='left', padx=6)
        ttk.Button(bar, text='载入同输入FPGA记录', command=self.choose_board).pack(side='left', padx=(2, 16))
        ttk.Label(bar, text='起点 (s)').pack(side='left')
        entry = ttk.Entry(bar, textvariable=self.start, width=7)
        entry.pack(side='left', padx=5)
        entry.bind('<Return>', lambda event: self.redraw())
        ttk.Label(bar, text='观察窗 (ms)').pack(side='left')
        window = ttk.Combobox(bar, textvariable=self.width, values=('10', '20', '80', '320', '800', '全段'), width=6, state='readonly')
        window.pack(side='left', padx=5)
        window.bind('<<ComboboxSelected>>', lambda event: self.redraw())
        ttk.Label(bar, text='频谱 (Hz)').pack(side='left')
        frequency = ttk.Combobox(bar, textvariable=self.fmax, values=('4000', '8000', '16000'), width=7, state='readonly')
        frequency.pack(side='left', padx=5)
        frequency.bind('<<ComboboxSelected>>', lambda event: self.redraw())
        ttk.Button(bar, text='全段', command=self.view_all).pack(side='left', padx=5)
        ttk.Button(bar, text='保存对照', command=self.save).pack(side='right')
        cards = ttk.Frame(self, padding=(20, 0, 20, 8))
        cards.pack(fill='x')
        for i, (key, title) in enumerate([('board', '512 FPGA · 恢复误差'), ('software', '1024离线 · 恢复误差'), ('relative', '512相对误差降低')]):
            cards.columnconfigure(i, weight=1)
            card = ttk.Frame(cards, style='Panel.TFrame', padding=(14, 8))
            card.grid(row=0, column=i, sticky='ew', padx=(0, 10 if i < 2 else 0))
            ttk.Label(card, text=title, style='MetricName.TLabel').pack(anchor='w')
            ttk.Label(card, textvariable=self.values[key], style='Metric.TLabel').pack(anchor='w')
        self.plots = ComparisonPlots()
        self.canvas = FigureCanvasTkAgg(self.plots.figure, master=self)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=12)
        self.canvas.mpl_connect('button_press_event', self.timeline_click)
        bottom = ttk.Frame(self, padding=(20, 6, 20, 12))
        bottom.pack(fill='x')
        ttk.Label(bottom, textvariable=self.state, font=('Microsoft YaHei UI', 9, 'bold'), wraplength=1350).pack(anchor='w')
        ttk.Label(bottom, textvariable=self.note, style='Muted.TLabel', wraplength=1350).pack(anchor='w', pady=(3, 0))

    def attach_board(self, directory):
        output, meta = load_actual_capture(directory, self.data)
        self.board, self.board_meta = output, meta
        self.board_directory = Path(directory).resolve()
        np.save(self.directory/'output_512_actual.npy', output)
        write_json(self.directory/'actual_capture_metadata.json', meta)

    def choose_board(self):
        path = filedialog.askdirectory(parent=self, title='选择已完成的FPGA收发记录（输入须完全相同）',
                                       initialdir=str(self.release_root/'host_ui/captures'))
        if path:
            try:
                self.attach_board(path)
                self.state.set('已核对：FPGA与1024使用完全相同的int16输入。')
                self.redraw()
            except (ValueError, OSError, KeyError, TypeError) as error:
                messagebox.showerror('不能作为同输入对照', str(error), parent=self)

    def run_reference(self):
        if self.started:
            return
        model = self.release_root/'validation/recorded_1024_reference'
        if not (model/'source_manifest.json').is_file():
            messagebox.showerror('缺少1024模型', f'请保留完整代码目录：{model}', parent=self)
            return
        self.cancel.clear()
        self.dead_since = None
        self.process = self.context.Process(target=reference_worker, daemon=True,
            args=(self.data, model, self.directory, self.events, self.cancel))
        try:
            self.process.start()
        except (OSError, RuntimeError) as error:
            messagebox.showerror('无法启动离线计算', str(error), parent=self)
            return
        self.started = True
        self.run_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.state.set('1024离线计算中；未连接串口，也不修改FPGA回传数据。')

    def finish(self):
        self.started = False
        self.run_button.configure(state='normal')
        self.cancel_button.configure(state='disabled')
        if self.process is not None and not self.process.is_alive():
            self.process.join(timeout=0)

    def poll(self):
        if self.closed:
            return
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                stage, done, total = value
                label = {'verify': '核对模型', 'detect': '分析输入', 'filter': '计算滤波'}.get(stage, stage)
                self.state.set(f'1024离线 · {label} {done:,}/{total:,}；输入ID {self.input_hash[:12]}')
            elif kind == 'finished':
                try:
                    output = np.load(self.directory/'output_1024.npy', allow_pickle=False)
                    if value['input_sha256'] != self.input_hash or value['sample_count'] != len(self.data):
                        raise ValueError('后台结果与当前冻结输入不一致。')
                    if output.shape != self.data.shape or output.dtype != np.dtype('int16') or digest(output) != value['output_sha256']:
                        raise ValueError('1024输出长度、格式或哈希核对失败。')
                    self.software, self.software_meta = output, value
                    self.state.set('1024离线计算完成。蓝色为FPGA实际回传，紫色为软件参考。')
                    if self.redraw():
                        self.write_metrics()
                except (ValueError, OSError, KeyError) as error:
                    self.state.set(f'结果未采纳：{error}')
                self.finish()
            elif kind in ('failed', 'cancelled'):
                self.state.set('1024计算已取消；已有实际板测数据保留。' if kind == 'cancelled' else f'1024计算失败：{value}')
                self.finish()
        if self.started and self.process.exitcode is not None:
            if self.dead_since is None:
                self.dead_since = time.monotonic()
            elif time.monotonic()-self.dead_since > 1:
                self.state.set(f'离线计算进程异常结束（退出码{self.process.exitcode}）；不显示新结果。')
                self.finish()
        self.after(100, self.poll)

    def window(self):
        start = float(self.start.get())
        if not np.isfinite(start) or start < 0:
            raise ValueError('起点必须是非负秒数。')
        width = len(self.data)/FS if self.width.get() == '全段' else float(self.width.get())/1000
        if start >= len(self.data)/FS:
            raise ValueError('起点超出输入时长。')
        return start, width

    def redraw(self):
        try:
            start, width = self.window()
            measured = measure_pair(self.data, self.board, self.software, self.target, self.metadata, start, width)
            self.plots.update(self.data, self.board, self.software, self.target, start, width, float(self.fmax.get()))
            self.last_metrics = measured
            for key in ('board', 'software'):
                metrics = measured[key]
                value = None if metrics is None else metrics['output_nrmse_pct']
                self.values[key].set('无目标参考' if self.target is None else '—' if value is None else f'{value:.2f} %')
            relative = measured['relative_reduction_pct']
            self.values['relative'].set('—' if relative is None else f'{relative:+.2f} %')
            self.note.set('固定对齐320样本；观察窗只选择已生成的输出。1024为离线数值参考，未计硬件计算/配置等待。'
                          + (' 普通输入没有目标参考，不计算恢复误差。' if self.target is None else ' 相对降低为负值时，表示该区间1024误差更低。'))
            self.canvas.draw_idle()
            return True
        except (ValueError, TypeError) as error:
            self.state.set(str(error))
            return False

    def view_all(self):
        self.start.set('0')
        self.width.set('全段')
        self.redraw()

    def timeline_click(self, event):
        if event.inaxes is self.plots.overview and event.xdata is not None:
            if self.width.get() == '全段':
                self.width.set('80')
            width = float(self.width.get())/1000
            self.start.set(f'{min(max(0., len(self.data)/FS-width), max(0., event.xdata-width/2)):.5f}')
            self.redraw()

    def write_metrics(self):
        record = dict(schema='same-input-comparison-1', input_sha256=self.input_hash,
                      samples=len(self.data), fs_hz=FS, actual_capture=str(self.board_directory) if self.board_directory else None,
                      actual_output_sha256=digest(self.board) if self.board is not None else None,
                      software_output_sha256=digest(self.software) if self.software is not None else None,
                      target_reference_sha256=None if self.target is None else hashlib.sha256(self.target.astype('<f8').tobytes()).hexdigest(),
                      software_reference=True, metrics=self.last_metrics,
                      software_meta=self.software_meta)
        write_json(self.directory/'comparison.json', record)
        with (self.directory/'metrics.csv').open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(('method', 'output_nrmse_pct', 'common_samples', 'first_sample', 'stop_sample_exclusive'))
            for key in ('board', 'software'):
                metrics = self.last_metrics[key]
                if metrics is not None:
                    writer.writerow((key, metrics['output_nrmse_pct'], metrics['common_samples'], metrics['first_sample'], metrics['stop_sample_exclusive']))

    def save(self):
        if self.software is None:
            self.state.set('先完成1024离线计算，再保存对照。')
            return
        if not self.redraw():
            return
        self.write_metrics()
        self.plots.save(self.directory/'comparison.png')
        self.state.set(f'对照图、输入输出与指标已保存：{self.directory}')

    def close(self):
        self.closed = True
        self.cancel.set()
        self.destroy()


class ComparisonInstrument(Instrument):
    def __init__(self, *args, **kwargs):
        self.comparison_window = None
        super().__init__(*args, **kwargs)
        self.root.title('ACG525 · 可变输入对照')

    def _build(self):
        super()._build()
        self.compare_button = RoundedButton(self.run_button.master, text='1024离线对照', command=self.open_comparison, width=150)
        self.compare_button.pack(side='right', before=self.stop_button, padx=(0, 8))
        self.editables.append(self.compare_button)

    def open_comparison(self):
        if self.busy:
            return
        try:
            if self.imported_path is None:
                config = self.get_config()
                data, metadata, components = generate_scene(config)
                if not np.array_equal(data, self.data):
                    self.configuration = config
                    self.set_input(data, metadata, components, '本次对照输入')
            if self.comparison_window is not None and self.comparison_window.winfo_exists():
                self.comparison_window.close()
            actual = self.capture_dir if self.received == len(self.data) and not self.run_failed else None
            self.comparison_window = ComparisonWindow(self.root, self.release_root, self.data,
                self.metadata, self.components.get('target'), actual)
        except (ValueError, OSError, KeyError, TypeError) as error:
            messagebox.showerror('不能建立同输入对照', str(error), parent=self.root)

    def close(self):
        if self.comparison_window is not None and self.comparison_window.winfo_exists():
            self.comparison_window.close()
        super().close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, parents=[bootstrap])
    parser.add_argument('--capacity', type=int, choices=(4,), default=4)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--input', type=Path)
    source.add_argument('--capture', type=Path)
    args = parser.parse_args()
    root = tk.Tk()
    instrument = ComparisonInstrument(root, args.release_root, args.capacity)
    if args.input:
        instrument.load_input_path(args.input)
    if args.capture:
        instrument.load_capture(args.capture)
    root.mainloop()


if __name__ == '__main__':
    mp.freeze_support()
    main()

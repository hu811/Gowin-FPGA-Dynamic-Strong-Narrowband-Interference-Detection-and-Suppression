"""Display one frozen input and separately labelled actual/software outputs."""
import numpy as np
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from plots import minmax_trace, spectrum

FS, DELAY = 32000, 320
COLORS = {'input': '#D55E00', 'target': '#009E73', 'board': '#0072B2', 'software': '#8B3FC7'}


class ComparisonPlots:
    def __init__(self):
        self.figure = Figure(figsize=(10, 7), dpi=100, facecolor='white')
        grid = self.figure.add_gridspec(3, 2, height_ratios=(.7, 1, 1.2),
                                       left=.08, right=.98, top=.90, bottom=.08,
                                       hspace=.65, wspace=.24)
        self.overview = self.figure.add_subplot(grid[0, :])
        self.board_axis = self.figure.add_subplot(grid[1, 0])
        self.software_axis = self.figure.add_subplot(grid[1, 1])
        self.spectrum_axis = self.figure.add_subplot(grid[2, :])
        self.lines = {}
        for axis in self.figure.axes:
            axis.set_facecolor('white')
            axis.grid(True, color='#eef0f3', lw=.6)
            axis.tick_params(labelsize=8, colors='#777b82')
            for side, spine in axis.spines.items():
                spine.set_color('#e3e6ea')
                spine.set_visible(side not in ('top', 'right'))
        for key, label in [('input', '混合输入'), ('target', '目标参考'), ('board', '512 FPGA实际输出'), ('software', '1024离线参考')]:
            self.lines['overview', key], = self.overview.plot([], [], color=COLORS[key], lw=.65,
                ls='--' if key == 'software' else '-', label=label, alpha=.65 if key == 'input' else 1)
            self.lines['spectrum', key], = self.spectrum_axis.plot([], [], color=COLORS[key],
                lw=.85, ls='--' if key == 'software' else '-', label=label)
        for key, axis in [('board', self.board_axis), ('software', self.software_axis)]:
            self.lines[key, 'target'], = axis.plot([], [], color=COLORS['target'], lw=.8, label='目标参考')
            self.lines[key, key], = axis.plot([], [], color=COLORS[key], lw=.8,
                ls='--' if key == 'software' else '-', label='FPGA实际输出' if key == 'board' else '软件输出')
            axis.set_xlabel('观察窗内时间 (ms)', fontsize=8)
            axis.set_ylabel('幅度 (FS)', fontsize=8)
            axis.legend(fontsize=7, frameon=False, loc='upper right')
        self.board_axis.set_title('512 · FPGA实际回传', loc='left', fontsize=10)
        self.software_axis.set_title('1024 · 电脑离线参考', loc='left', fontsize=10)
        self.overview.set_title('完整输入与输出', loc='left', fontsize=10)
        self.overview.set_xlabel('时间 (s)', fontsize=8)
        self.overview.set_ylabel('幅度 (FS)', fontsize=8)
        self.spectrum_axis.set_xlabel('频率 (Hz)', fontsize=8)
        self.spectrum_axis.set_ylabel('幅度 (dBFS)', fontsize=8)
        self.spectrum_axis.legend(fontsize=7, ncol=4, frameon=False, loc='upper right')
        self.spectrum_axis.set_ylim(-110, 2)
        self.selection = Rectangle((0, 0), 1, 1, transform=self.overview.get_xaxis_transform(),
                                   color=COLORS['board'], alpha=.06)
        self.overview.add_patch(self.selection)
        self.notes = {key: axis.text(.5, .5, '', ha='center', va='center', color='#888b91',
                                   fontsize=10, transform=axis.transAxes)
                      for key, axis in [('board', self.board_axis), ('software', self.software_axis)]}
        self.title = self.figure.suptitle('', fontsize=10, x=.08, ha='left')

    @staticmethod
    def set_line(line, values, origin=0., milliseconds=False):
        times, levels = minmax_trace(np.asarray(values))
        line.set_data((times + origin)*(1000 if milliseconds else 1), levels)

    def update(self, data, board, software, target, start, width, maximum_hz=8000):
        total = len(data)/FS
        first = min(len(data), max(0, round(start*FS)))
        stop = min(len(data), first+max(32, round(width*FS)))
        outputs = [value for value in (board, software) if value is not None]
        if outputs:
            stop = min(stop, max(first, min(len(value) for value in outputs)-DELAY))
        x = data[first:stop].astype(float)/32768.
        ref = None if target is None else target[first:stop]
        self.set_line(self.lines['overview', 'input'], data.astype(float)/32768.)
        self.set_line(self.lines['overview', 'target'], [] if target is None else target)
        peak = max(.005, float(np.max(np.abs(data.astype(float))))/32768.)
        selected = {}
        for key, output in [('board', board), ('software', software)]:
            values = [] if output is None else output[DELAY:].astype(float)/32768.
            self.set_line(self.lines['overview', key], values)
            if len(values):
                peak = max(peak, float(np.max(np.abs(values))))
            y = np.array([]) if output is None else output[first+DELAY:stop+DELAY].astype(float)/32768.
            selected[key] = y
            self.set_line(self.lines[key, key], y, milliseconds=True)
            self.set_line(self.lines[key, 'target'], [] if ref is None else ref, milliseconds=True)
            self.notes[key].set_text('等待实际回传记录' if key == 'board' and output is None
                                     else '点击“计算1024”' if output is None
                                     else '所选区间没有可对齐样本' if not len(y) else '')
        self.overview.set_xlim(0, max(total, .001))
        self.overview.set_ylim(-peak*1.08, peak*1.08)
        self.selection.set_x(first/FS)
        self.selection.set_width(max(0, stop-first)/FS)
        shared_peak = max([.005] + [float(np.max(np.abs(y))) for y in selected.values() if len(y)]
                          + ([float(np.max(np.abs(ref)))] if ref is not None and len(ref) else []))
        for axis in (self.board_axis, self.software_axis):
            axis.set_xlim(0, max(width*1000, 1))
            axis.set_ylim(-shared_peak*1.1, shared_peak*1.1)
        for key, values in [('input', x), ('target', [] if ref is None else ref),
                            ('board', selected['board']), ('software', selected['software'])]:
            frequency, db = spectrum(np.asarray(values)*32768.)
            self.lines['spectrum', key].set_data(frequency, db)
        self.spectrum_axis.set_xlim(0, maximum_hz)
        scope = '公共区间' if board is not None and software is not None else '当前输出区间' if outputs else '输入预览'
        self.title.set_text(f'同一输入 · 固定对齐10 ms · {scope} {first/FS:.3f}–{stop/FS:.3f} s · 两输出使用同一纵轴')
        return first, stop

    def save(self, path):
        self.figure.savefig(path, dpi=170, facecolor='white')

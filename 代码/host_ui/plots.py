"""Display actual samples and an optional OFFLINE clean target; never filter on PC."""
from pathlib import Path
import numpy as np
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib import rcParams

FS, DELAY = 32000, 320
INK, MUTED = '#272a2e', '#7c838b'
INPUT, OUTPUT, CLEAN = '#D55E00', '#0072B2', '#009E73'
GRID, BORDER, MISSING = '#f0f2f4', '#e5e8eb', '#bfc6cd'
rcParams['font.sans-serif'] = ['Microsoft YaHei', 'DejaVu Sans']
rcParams['axes.unicode_minus'] = False


def spectrum(samples):
    x = np.asarray(samples, dtype=np.float64)/32768.
    if len(x) < 32:
        return np.array([]), np.array([])
    window = np.hanning(len(x))
    amplitude = 2*np.abs(np.fft.rfft(x*window))/window.sum()
    amplitude[0] *= .5
    if len(x) % 2 == 0:
        amplitude[-1] *= .5
    return np.fft.rfftfreq(len(x), 1/FS), 20*np.log10(np.maximum(amplitude, 1e-6))


def comparison_segments(data, output, start, width, align):
    """Common interval for numerical comparisons, NOT the input drawing limit."""
    data, output = np.asarray(data), np.asarray(output)
    first = max(0, min(len(data), int(round(start*FS))))
    last = min(len(data), first+max(32, int(round(width*FS))))
    shift = DELAY if align else 0
    if len(output):
        last = min(last, max(first, len(output)-shift))
    x = data[first:last]
    y = output[first+shift:last+shift] if len(output) and last > first else output[:0]
    return np.arange(first, last)/FS, x, y


def minmax_trace(samples, first=0, maximum_bins=1800):
    """Keep both extrema of every bucket; never silently discard narrow peaks."""
    samples = np.asarray(samples)
    if not len(samples):
        return np.array([]), np.array([])
    step = max(1, int(np.ceil(len(samples)/maximum_bins)))
    if step == 1:
        return (np.arange(len(samples))+first)/FS, samples
    starts = np.arange(0, len(samples), step)
    low = np.minimum.reduceat(samples, starts)
    high = np.maximum.reduceat(samples, starts)
    times = np.repeat((starts+first)/FS, 2)
    values = np.column_stack((low, high)).ravel()
    return times, values


def window_data(data, output, clean, start, width, align):
    """Input always spans its whole selected window; output is verified prefix only."""
    data, output = np.asarray(data), np.asarray(output)
    first = min(len(data), max(0, int(round(start*FS))))
    last = min(len(data), first+max(32, int(round(width*FS))))
    shift = DELAY if align else 0
    out_last = min(last, max(first, len(output)-shift))
    target = None if clean is None else np.asarray(clean)
    if target is not None and (target.ndim != 1 or len(target) != len(data) or not np.all(np.isfinite(target))):
        raise ValueError('Clean reference must be a finite, full-length normalized target.')
    return dict(first=first, last=last, out_last=out_last, shift=shift,
                input=data[first:last], output=output[first+shift:out_last+shift],
                target=None if target is None else target[first:last])


def fidelity_text(data, output, clean, start, width, align):
    if clean is None:
        return '无干净目标参考；只显示真实输入和回传，不估算保真度。'
    if not align:
        return '当前为原始输出时刻；勾选 10 ms 对齐后显示同区间目标恢复误差。'
    view = window_data(data, output, clean, start, width, True)
    n = len(view['output'])
    if not n:
        return '绿色为干净目标参考；所选区间没有已回传的对齐输出。'
    ref = np.asarray(clean)[view['first']:view['first']+n]
    denominator = np.linalg.norm(ref)
    if denominator == 0:
        return '所选区间干净目标能量为零，NRMSE 无定义。'
    before = 100*np.linalg.norm(view['input'][:n].astype(float)/32768.-ref)/denominator
    after = 100*np.linalg.norm(view['output'].astype(float)/32768.-ref)/denominator
    return (f'选窗目标恢复 NRMSE：输入 {before:.2f}% → 板上输出 {after:.2f}%'
            f' · 公共 {n/FS*1000:.1f} ms · 固定 10 ms 对齐，无增益拟合')


class SignalPlots:
    def __init__(self):
        self.figure = Figure(figsize=(9.4, 5.6), dpi=90, facecolor='#ffffff')
        grid = self.figure.add_gridspec(4, 2, height_ratios=(.48, 1., .9, .24),
                                      left=.08, right=.98, bottom=.07, top=.91,
                                      hspace=.95, wspace=.30)
        self.overview = self.figure.add_subplot(grid[0, :])
        self.wave_in = self.figure.add_subplot(grid[1, 0])
        self.wave_out = self.figure.add_subplot(grid[1, 1], sharex=self.wave_in)
        self.fft = self.figure.add_subplot(grid[2, :])
        self.timeline = self.figure.add_subplot(grid[3, :])
        # Names retained for existing callers; there is one common-scale spectrum.
        self.fft_in = self.fft_out = self.fft
        self.title = self.figure.suptitle('', x=.08, y=.99, ha='left', fontsize=10, color=INK)
        for axis in self.figure.axes:
            axis.set_facecolor('white')
            axis.tick_params(colors=MUTED, labelsize=7)
            axis.set_axisbelow(True)
            axis.grid(True, color=GRID, lw=.5)
            for name, spine in axis.spines.items():
                spine.set_color(BORDER)
                if name in ('top', 'right'):
                    spine.set_visible(False)
        self.overview.set_title('全段信号', loc='left', color=INK, fontsize=9)
        self.overview.set_ylabel('幅度 (FS)', color=MUTED, fontsize=8)
        self.overview_in, = self.overview.plot([], [], color=INPUT, lw=.6, alpha=.8)
        self.overview_clean, = self.overview.plot([], [], color=CLEAN, lw=.9)
        self.overview_out, = self.overview.plot([], [], color=OUTPUT, lw=.75, ls='--')
        self.selected = Rectangle((0, 0), .08, 1, transform=self.overview.get_xaxis_transform(),
                                  facecolor=OUTPUT, alpha=.065, edgecolor='none')
        self.overview.add_patch(self.selected)
        self.missing_overview = Rectangle((0, 0), 1, 1, transform=self.overview.get_xaxis_transform(),
                                          facecolor=MISSING, alpha=.20, edgecolor='none')
        self.overview.add_patch(self.missing_overview)
        self.input_line, = self.wave_in.plot([], [], color=INPUT, lw=.7, label='混合输入')
        self.input_clean, = self.wave_in.plot([], [], color=CLEAN, lw=.8, alpha=.85, label='目标参考')
        self.output_clean, = self.wave_out.plot([], [], color=CLEAN, lw=1.1, zorder=2, label='目标参考')
        self.output_line, = self.wave_out.plot([], [], color=OUTPUT, lw=.9, ls='--', zorder=3, label='实际 FPGA 输出')
        self.wave_in.set_title('输入', loc='left', color=INK, fontsize=9)
        self.wave_out.set_title('输出', loc='left', color=INK, fontsize=9)
        self.output_note = self.wave_out.text(.5, .5, '', transform=self.wave_out.transAxes,
                                             ha='center', color=MUTED, fontsize=9)
        self.output_missing = Rectangle((0, 0), 0, 1, transform=self.wave_out.get_xaxis_transform(),
                                       facecolor=MISSING, alpha=.20, edgecolor='none')
        self.wave_out.add_patch(self.output_missing)
        for axis in (self.wave_in, self.wave_out):
            axis.set_xlabel('观察窗内时间 (ms)', color=MUTED, fontsize=8)
            axis.set_ylabel('幅度 (FS)', color=MUTED, fontsize=8)
            axis.legend(loc='upper right', fontsize=6, frameon=False, labelcolor=INK)
        self.fft_input, = self.fft.plot([], [], color=INPUT, lw=.7, alpha=.8, label='混合输入')
        self.fft_clean, = self.fft.plot([], [], color=CLEAN, lw=1.1, zorder=2, label='目标参考')
        self.fft_output, = self.fft.plot([], [], color=OUTPUT, lw=.9, ls='--', zorder=3, label='实际 FPGA 输出')
        self.fft.legend(loc='upper right', fontsize=7, ncol=3, frameon=False, labelcolor=INK)
        self.fft.set_ylabel('幅度 (dBFS)', color=MUTED, fontsize=8)
        self.fft.set_xlabel('频率 (Hz)', color=MUTED, fontsize=8)
        self.fft.set_ylim(-110, 2)
        self.timeline.set_title('干扰区间（输入设定）', loc='left', color=INK, fontsize=8)
        self.timeline.set_xlabel('整段信号时间 (s)', color=MUTED, fontsize=8)
        self.timeline_selected = Rectangle((0, 0), .08, 1, transform=self.timeline.get_xaxis_transform(),
                                           facecolor=OUTPUT, alpha=.065, edgecolor='none')
        self.timeline.add_patch(self.timeline_selected)
        self.event_patches = []
        self._source = None
        self._spectrum_key = None
        self.metrics = ''
        self._canvas = None
        self._background = None
        self._needs_full_draw = True
        self._saving = False
        self._moving = (self.overview_in, self.overview_clean, self.overview_out,
                        self.selected, self.missing_overview, self.timeline_selected,
                        self.input_line, self.input_clean, self.output_line, self.output_clean,
                        self.output_note, self.output_missing, self.fft_input, self.fft_output,
                        self.fft_clean, self.title, self.fft.title)
        for artist in self._moving:
            artist.set_animated(True)

    def invalidate(self):
        self._source = None
        self._spectrum_key = None
        self._needs_full_draw = True

    def _set_limits(self, axis, which, values):
        previous = axis.get_xlim() if which == 'x' else axis.get_ylim()
        if not np.allclose(previous, values, rtol=0, atol=1e-10):
            (axis.set_xlim if which == 'x' else axis.set_ylim)(values)
            self._needs_full_draw = True

    def _paint(self, canvas, restore=True):
        if restore:
            canvas.restore_region(self._background)
        for artist in self._moving:
            self.figure.draw_artist(artist)
        # Legends are part of the cached background. Restore their rectangles
        # above new data without redrawing all text on every refresh.
        for axis in (self.wave_in, self.wave_out, self.fft):
            legend = axis.get_legend()
            if legend is not None:
                canvas.restore_region(self._background, bbox=legend.get_window_extent())

    def _on_draw(self, event):
        if event.canvas is self._canvas and not self._saving:
            self._background = self._canvas.copy_from_bbox(self.figure.bbox)
            self._needs_full_draw = False
            self._paint(self._canvas, restore=False)

    def render(self, canvas):
        """Keep static axes/text cached; normal data refresh avoids full redraw."""
        if self._canvas is not canvas:
            self._canvas = canvas
            canvas.mpl_connect('draw_event', self._on_draw)
            self._needs_full_draw = True
        if self._background is None or self._needs_full_draw:
            canvas.draw()
        else:
            self._paint(canvas)
            canvas.blit(self.figure.bbox)

    @staticmethod
    def _line(line, samples, first, multiplier=1.):
        t, values = minmax_trace(samples, first)
        line.set_data(t*multiplier, values)

    def update(self, data, output, metadata, start=.0, width=.08, align=True,
               maximum_hz=8000, status='', clean=None, same_scale=False):
        source = (id(data), id(clean), len(data))
        if source != self._source:
            self._source = source
            self._spectrum_key = None
            self._needs_full_draw = True
            self._line(self.overview_in, np.asarray(data, dtype=float)/32768., 0)
            self._line(self.overview_clean, clean if clean is not None else [], 0)
            total = max(len(data)/FS, .001)
            self.overview.set_xlim(0, total)
            self.timeline.set_xlim(0, total)
            overview_peak = max(.01, float(np.max(np.abs(np.asarray(data, dtype=float))))/32768.)
            self.overview.set_ylim(-overview_peak*1.08, overview_peak*1.08)
            for patch in self.event_patches:
                patch.remove()
            self.event_patches = []
            events = [e for e in metadata.get('events', []) if e.get('enabled', True)]
            for i, event in enumerate(events):
                rect = Rectangle((event['start_s'], i-.3), event['end_s']-event['start_s'], .6,
                                 facecolor=INPUT, alpha=.65, edgecolor='none')
                self.timeline.add_patch(rect)
                self.event_patches.append(rect)
            self.timeline.set_ylim(-.7, max(1, len(events))-.3)
            self.timeline.set_yticks(range(len(events)), [str(i+1) for i in range(len(events))])
        self.title.set_text(status + f' · 观察 {start*1000:.0f}–{(start+width)*1000:.0f} ms')
        v = window_data(data, output, clean, start, width, align)
        first, last, out_last, shift = (v[k] for k in ('first', 'last', 'out_last', 'shift'))
        self._line(self.input_line, v['input'].astype(float)/32768., 0, 1000)
        self._line(self.input_clean, [] if v['target'] is None else v['target'], 0, 1000)
        self._line(self.output_line, v['output'].astype(float)/32768., 0, 1000)
        if clean is None:
            ref_out = np.array([])
        elif align:
            ref_out = v['target']
        else:
            indices = np.arange(first, last)-DELAY
            ref_out = np.zeros(last-first)
            valid = indices >= 0
            ref_out[valid] = np.asarray(clean)[indices[valid]]
        self._line(self.output_clean, ref_out, 0, 1000)
        if len(v['output']):
            self.output_note.set_text('')
        else:
            self.output_note.set_text('所选区间没有可对齐输出\n绿色是离线参考，非板上输出' if clean is not None
                                      else '所选区间没有可对齐输出')
        self._set_limits(self.wave_in, 'x', (0, width*1000))
        p_in = max(.005, float(np.max(np.abs(v['input'].astype(float))))/32768. if len(v['input']) else 0)
        p_out = max(.005, float(np.max(np.abs(v['output'].astype(float))))/32768. if len(v['output']) else 0,
                    float(np.max(np.abs(ref_out))) if len(ref_out) else 0)
        if same_scale:
            p_in = p_out = max(p_in, p_out)
        p_in = 2.**np.ceil(np.log2(p_in*1.12))
        p_out = 2.**np.ceil(np.log2(p_out*1.12))
        self._set_limits(self.wave_in, 'y', (-p_in, p_in))
        self._set_limits(self.wave_out, 'y', (-p_out, p_out))
        subtitle = '输出' + (' · 同纵轴' if same_scale else ' · 独立纵轴')
        if self.wave_out.get_title(loc='left') != subtitle:
            self.wave_out.set_title(subtitle, loc='left', color=INK, fontsize=9)
            self._needs_full_draw = True
        valid_end = max(0, len(output)-shift)/FS
        self.missing_overview.set_x(valid_end)
        self.missing_overview.set_width(max(0., len(data)/FS-valid_end))
        self.output_missing.set_x((out_last-first)/FS*1000)
        self.output_missing.set_width(max(0., last-out_last)/FS*1000)
        self._line(self.overview_out, np.asarray(output[shift:], dtype=float)/32768., 0)
        for patch in (self.selected, self.timeline_selected):
            patch.set_x(start)
            patch.set_width(width)
        n = len(v['output'])
        common_n = n if n >= 32 else len(v['input'])
        key = (source, first, last, common_n, n, bool(align))
        if key != self._spectrum_key:
            self._spectrum_key = key
            hz, db = spectrum(v['input'][:common_n])
            self.fft_input.set_data(hz, db)
            hz, db = spectrum(v['output'])
            self.fft_output.set_data(hz, db)
            ref = (v['target'][:common_n] if align else ref_out[:common_n]) if clean is not None else np.array([])
            hz, db = spectrum(ref*32768.)
            self.fft_clean.set_data(hz, db)
        self._set_limits(self.fft, 'x', (0, maximum_hz))
        label = '公共观察段' if n >= 32 else '输入观察段；输出不足 32 样本'
        self.fft.title.set_text(f'频谱 · {label} {common_n/FS*1000:.1f} ms')
        self.fft.title.set_fontsize(9)
        self.fft.title.set_color(INK)
        self.metrics = fidelity_text(data, output, clean, start, width, align)

    def save(self, path: Path):
        self._saving = True
        try:
            for artist in self._moving:
                artist.set_animated(False)
            self.figure.savefig(path, dpi=160, facecolor=self.figure.get_facecolor())
        finally:
            for artist in self._moving:
                artist.set_animated(True)
            self._saving = False
            self._needs_full_draw = True

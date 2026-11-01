"""Read-only source binding and bounded current-C4 native RTL calibration."""
from pathlib import Path
import csv
import hashlib
import json
import shutil
import subprocess
import time
import numpy as np

HERE = Path(__file__).resolve().parent
WORK = Path('D:/FPGA_Competition/timing_sweep_work_20261028')
BUILD = WORK/'compiled'
ORIGINAL = Path('D:/FPGA_Competition/支撑材料/项目资料/04_PC_Software/real_signal_validation_20261024/rtl_smoke/build_copy')
RELEASE = Path('D:/FPGA_Competition/最终提交/代码')
EXE_SHA = 'b8f57f0f1d2143a38551020eed93b51d48d1884eff4b3b1cd5778ba34a7f0550'

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p, v): Path(p).write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding='utf-8')
def norm(p): return '\n'.join(line.rstrip() for line in p.read_text(encoding='utf-8').splitlines() if line.strip())

def prepare():
    manifest = json.loads((ORIGINAL/'manifest.json').read_text(encoding='utf-8'))
    copied = {}
    for name, digest in manifest['sim_copies_sha256'].items():
        source, target = ORIGINAL/name, BUILD/name
        assert sha(source) == digest, name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists(): shutil.copy2(source, target)
        assert sha(target) == digest, name
        copied[name] = digest
    target = BUILD/'native/Release/fullrtl.exe'
    target.parent.mkdir(parents=True, exist_ok=True)
    assert sha(ORIGINAL/'native/Release/fullrtl.exe') == EXE_SHA
    if not target.exists(): shutil.copy2(ORIGINAL/'native/Release/fullrtl.exe', target)
    assert sha(target) == EXE_SHA
    binding = []
    for source in (BUILD/'sources').glob('*.v'):
        production = RELEASE/'c4/rtl'/source.name
        if production.exists():
            binding.append(dict(name=source.name, normalized_equal=norm(source)==norm(production),
                                simulation_sha256=sha(source), production_sha256=sha(production)))
    differences = [item['name'] for item in binding if not item['normalized_equal']]
    assert differences == ['pro_v2_digital_top.v'], differences
    tb = (BUILD/'tb_full_quality.v').read_text(encoding='utf-8')
    assert 'pro_v2_auto_core #(.MAX_CENTERS(4)) dut(' in tb
    assert 'pro_v2_digital_top' not in tb
    record = dict(status='CURRENT_C4_NATIVE_SOURCE_BINDING_PASS', exe_sha256=EXE_SHA,
                  original_manifest_sha256=sha(ORIGINAL/'manifest.json'), copied_files=copied,
                  source_binding=binding, differences_not_in_tb_instance=differences,
                  difference_detail='Only digital top OLED reset uses board_reset instead of rst_n; TB instantiates auto core directly. Other source differences are blank lines.',
                  simulation_scope='Complete automatic C4 RTL, equivalent synchronous RAM simulation primitives, 50MHz/32kHz; not board run or P&R')
    dump(HERE/'runtime_identity.json', record)
    return target

def main():
    exe = prepare()
    raw = WORK/'golden_prefix'
    raw.mkdir(parents=True, exist_ok=False)
    data_path = RELEASE/'host_ui/captures/c4_20261027_223512_015471'
    x = np.load(data_path/'input.npy')[:4096]
    expected = np.load(data_path/'output.npy')[:4096]
    np.save(raw/'input.npy', x)
    (raw/'input.mem').write_text(''.join(f'{int(v)&65535:04x}\n' for v in x), encoding='ascii')
    command = [str(exe), '+SAMPLES=4096', f'+INPUT={(raw/"input.mem").as_posix()}', f'+FOLDER={raw.as_posix()}/']
    dump(HERE/'progress.json', dict(phase='golden_prefix', done_unique_cases=0, total_unique_cases=15))
    began = time.perf_counter()
    with (raw/'run.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=BUILD, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    text = (raw/'run.log').read_text(encoding='utf-8')
    assert result.returncode == 0 and 'PASS FULL_AUTO_QUALITY_TRACE' in text
    with (raw/'outputs.csv').open() as f: rows = list(csv.DictReader(f))
    y = np.asarray([int(row['output']) for row in rows], np.int16)
    assert len(y) == 4096 and np.array_equal(y, expected)
    checks = []
    legacy = ORIGINAL.parents[1]
    for folder, capture in [('rtl_smoke', 'c4_20261027_223512_015471'), ('rtl_smoke_mixed_16000', 'c4_20261027_223556_432532')]:
        old = legacy/folder
        old_x = np.load(old/'raw/input.npy')
        old_y = np.asarray([int(row['output']) for row in csv.DictReader((old/'raw/outputs.csv').open())], np.int16)
        b = RELEASE/'host_ui/captures'/capture
        assert np.array_equal(old_x, np.load(b/'input.npy')[:len(old_x)])
        assert np.array_equal(old_y, np.load(b/'output.npy')[:len(old_y)])
        checks.append(dict(record=folder, samples=len(old_x), output_mismatches_lsb=0))
    record = dict(status='GOLDEN_CALIBRATION_PASS', new_native_prefix_samples=4096,
                  new_native_output_mismatches_lsb=0, existing_native_vs_actual_board=checks,
                  wall_seconds_not_fpga_latency=time.perf_counter()-began, command=command,
                  output_sha256=hashlib.sha256(y.astype('<i2').tobytes()).hexdigest())
    dump(HERE/'golden_calibration.json', record)
    print(json.dumps(record, ensure_ascii=False), flush=True)

if __name__ == '__main__': main()

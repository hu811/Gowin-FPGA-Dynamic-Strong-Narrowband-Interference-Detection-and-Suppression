"""Two frozen recorded-input board captures; PC prepares input and scores offline."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np

HERE=Path(__file__).resolve().parent
DEFAULT_RELEASE=Path(__file__).resolve().parent.parent
CASES={'control':'drm_recorded_two_pairs_no_interference','mixed':'drm_recorded_two_pairs'}

def write_json(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def digest(path):return sha256(Path(path).read_bytes()).hexdigest()

def load_frozen(release):
    release=Path(release).resolve()
    sys.path.insert(0,str(release/'host_ui'))
    import board_io
    from wav_bundle import load_import_bundle
    frozen=json.loads((HERE/'experiment.json').read_text(encoding='utf-8'))
    for name,expected in frozen['host_source_hashes'].items():
        if digest(release/'host_ui'/name)!=expected:
            raise ValueError(f'Host source changed: {name}; revalidate before this experiment.')
    local=board_io.verify_release(release,4)
    if local['bitstream_sha256']!=frozen['bitstream_sha256']:
        raise ValueError('Local bitstream differs from frozen experiment.')
    loaded={}
    for key,folder in CASES.items():
        source=release/'real_recorded_inputs'/'prepared'/folder
        for name,expected in frozen['inputs'][key]['files'].items():
            if digest(source/name)!=expected:raise ValueError(f'Frozen input changed: {key}/{name}')
        data,meta,components=load_import_bundle(source/'input.wav')
        assert len(data)==64000 and data.dtype==np.dtype('int16')
        assert meta['config']['capacity']==4 and meta['fs_hz']==32000
        assert meta['reference_bundle_verified']
        meta['local_release_check']=local
        meta['experiment_definition_sha256']=digest(HERE/'experiment.json')
        loaded[key]=(data,meta,components)
    control,mixed=loaded['control'],loaded['mixed']
    assert control[1]['scale']==mixed[1]['scale']==frozen['common_gain']
    assert np.array_equal(control[2]['target'],mixed[2]['target'])
    assert all(not np.any(values) for name,values in control[2].items() if name.startswith('jam_'))
    report={'status':'INPUTS_VERIFIED','board_experiment_performed':False,
            'sample_rate_hz':32000,'samples_per_case':64000,'common_gain':mixed[1]['scale'],
            'reference':'Same received DRM recording, including original noise/channel/pilots',
            'bitstream_local_file_verified':True,'fpga_image_readback_verified':False,
            'input_payload_sha256':{k:sha256(v[0].astype('<i2').tobytes()).hexdigest() for k,v in loaded.items()},
            'max_abs_sample':{k:int(np.max(np.abs(v[0].astype(np.int32)))) for k,v in loaded.items()},
            'source_version':'Frozen existing 2026-10-24 DRM conversion; no new input selection'}
    return board_io,loaded,report

def choose_port(requested):
    from serial.tools import list_ports
    ports=list(list_ports.comports())
    candidates=[p for p in ports if p.vid==0x1A86 and p.pid==0x55D4]
    if requested:
        match=[p for p in candidates if p.device.upper()==requested.upper()]
        if len(match)!=1:raise ValueError('Explicit port is not a detected board CH9102 UART.')
        return match[0].device
    if len(candidates)==1:return candidates[0].device
    if not candidates:
        raise ValueError('No CH9102 board UART detected. Connect board power and its UART USB, then retry. Bluetooth/JTAG ports are not used.')
    raise ValueError('Multiple CH9102 ports found; run with --port COMx: '+', '.join(p.device for p in candidates))

def save_reference(folder,meta,components):
    write_json(folder/'scene.json',meta['config'])
    np.savez_compressed(folder/'source_components.npz',**components)

def resolve_gui_script(release, gui_script=None):
    release=Path(release).resolve()
    script=Path('comparison_ui/compare_app.py') if gui_script is None else Path(gui_script)
    script=(script if script.is_absolute() else release/script).resolve()
    if not script.is_relative_to(release) or not script.is_file():
        raise ValueError('GUI script must exist inside the release directory.')
    return script

def replay_entry(session,case,release,gui_script=None):
    release=Path(release).resolve()
    relative=str(resolve_gui_script(release,gui_script).relative_to(release))
    lines=['@echo off','setlocal']
    if (release/'python_env.cmd').is_file():
        lines += ['call "%~dp0..\\..\\..\\python_env.cmd"','if errorlevel 1 exit /b 2']
    else:
        lines += ['set "FPGA_PY=%~dp0..\\..\\..\\..\\.venv\\Scripts\\python.exe"',
                  'if defined FPGA_PYTHON set "FPGA_PY=%FPGA_PYTHON%"',
                  'if not exist "%FPGA_PY%" exit /b 2']
    lines += ['"%FPGA_PY%" -B "%~dp0..\\..\\..\\'+relative+'" --release-root "%~dp0..\\..\\.." --capacity 4 --capture "%~dp0'+case+'"',
              'if errorlevel 1 pause','endlocal','']
    (session/('OPEN_'+case.upper()+'_RESULT.bat')).write_bytes('\r\n'.join(lines).encode('utf-8'))

def run(release,port=None,open_gui=True,gui_script=None):
    gui_script=resolve_gui_script(release,gui_script)
    board_io,loaded,preflight=load_frozen(release)
    selected=choose_port(port)
    stamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S_%f')
    session=HERE/'captures'/('paired_'+stamp)
    session.mkdir(parents=True,exist_ok=False)
    write_json(session/'preflight.json',preflight)
    write_json(session/'session.json',{'status':'RUNNING','port':selected,'cases':list(CASES),
            'reference_definition':preflight['reference'],'experiment_sha256':digest(HERE/'experiment.json')})
    print('Board UART:',selected,flush=True)
    print('Same recording and gain. Running CONTROL, then MIXED. No firmware programming.',flush=True)
    try:
        for case,(data,meta,components) in loaded.items():
            folder=session/case
            print('\n=== '+case.upper()+' : 64000 real FPGA outputs required ===',flush=True)
            try:
                result=board_io.capture(selected,4,data,folder,source_metadata=meta,
                                        on_log=lambda message:print(message,flush=True))
            finally:
                if folder.exists():save_reference(folder,meta,components)
            if result['metadata'].get('board_verified') is not True or result['metadata'].get('transport_valid') is not True:
                raise ValueError(case+' did not pass real transport verification')
            if len(result['output'])!=64000:raise ValueError(case+' output is incomplete')
            replay_entry(session,case,Path(release).resolve(),gui_script)
        from analyze_pair import analyze_session
        analysis=analyze_session(session,Path(release).resolve())
        write_json(session/'session.json',{'status':'ACTUAL_BOARD_PAIR_COMPLETE',
            'port':selected,'cases':list(CASES),'analysis':'analysis.json',
            'quality_pass':None,'note':'Transport and fixed-input experiment completed; inspect measured quality, not an automatic 20dB/1dB pass.'})
        print('\nActual board pair saved:',session,flush=True)
        for key in CASES:
            print(key,'NRMSE full / stable:',analysis['runs'][key]['metrics']['full_common']['output_nrmse_pct'],
                  analysis['runs'][key]['metrics']['stable']['output_nrmse_pct'],flush=True)
        if open_gui:
            env=os.environ.copy()
            env['MPLCONFIGDIR']=str(session/'mplconfig')
            command=[sys.executable,str(gui_script),'--release-root',str(release),
                     '--capacity','4','--capture',str(session/'mixed')]
            with (session/'gui_replay.log').open('w',encoding='utf-8') as log:
                subprocess.Popen(command,stdout=log,stderr=log,env=env,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return session
    except BaseException as exc:
        write_json(session/'session.json',{'status':'INCOMPLETE','failure':str(exc),
            'port':selected,'quality_pass':None,'partial_data_preserved':True})
        print('Incomplete experiment; evidence preserved:',session,flush=True)
        raise

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-root',type=Path,default=DEFAULT_RELEASE)
    parser.add_argument('--port')
    parser.add_argument('--check',action='store_true',help='Verify frozen inputs/files only; never opens UART.')
    parser.add_argument('--no-gui',action='store_true')
    parser.add_argument('--gui-script',type=Path,help='Release-relative GUI script; default is the current comparison UI.')
    args=parser.parse_args()
    if args.check:
        _,_,report=load_frozen(args.release_root)
        write_json(HERE/'preparation_check.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    run(args.release_root,args.port,not args.no_gui,args.gui_script)
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print('EXPERIMENT NOT COMPLETE:',str(exc),file=sys.stderr)
        raise SystemExit(1)

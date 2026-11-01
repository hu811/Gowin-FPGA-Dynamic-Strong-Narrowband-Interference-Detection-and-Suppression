"""Offline package checks only: no serial open, GUI, RTL run, synthesis or P&R."""
from pathlib import Path
import argparse
import ast
import hashlib
import importlib
import json
import re
import sys
import unittest
import xml.etree.ElementTree as ET

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def verify(root):
    root=Path(root).resolve();c4=root/'c4'
    manifest=json.loads((c4/'manifest.json').read_text(encoding='utf-8'))
    project=ET.parse(c4/'pro_v2.gprj').getroot()
    device=project.find('Device')
    assert device.get('name')=='GW5A-25A' and device.get('pn')=='GW5A-LV25UG324C2/I1'
    references=[node.get('path') for node in project.findall('./FileList/File') if node.get('enable')=='1']
    assert references and all(not Path(name).is_absolute() and (c4/name).is_file() for name in references)
    for name,record in manifest['sources'].items():
        assert sha(c4/name)==record['sha256'],name
    bitstream=c4/manifest['bitstream']['filename']
    assert sha(bitstream)==manifest['bitstream']['sha256']
    assert bitstream.stat().st_size==manifest['bitstream']['bytes']
    assert sha(c4/'pro_v2.gprj')==manifest['GPR']['sha256']
    config=json.loads((c4/'impl/pro_v2_process_config.json').read_text(encoding='utf-8'))
    assert config['CPU'] is True and config['SSPI'] is True
    assert config['Correct_Hold_Violation'] is True
    assert config['TopModule']=='pro_v2_digital_top_4' and config['IncludePath']==['rtl']
    assert str(config['Place_Option'])=='3' and str(config['Route_Option'])=='0'
    timing=(c4/'constraints/pro_v1.sdc').read_text(encoding='utf-8')
    assert re.search(r'create_clock.*-period\s+20\.0',timing)
    roms=set()
    for path in (c4/'rtl').glob('*.v'):
        roms.update(re.findall(r'"(rom/[^"\r\n]+)"',path.read_text(encoding='utf-8')))
    assert roms and all((c4/name).is_file() for name in roms)
    build=(c4/'build.tcl').read_text(encoding='utf-8')
    for expected in ('-top_module pro_v2_digital_top_4','-frequency 50','-use_sspi_as_gpio 1','-use_cpu_as_gpio 1','-correct_hold_violation 1'):
        assert expected in build,expected
    python_files=list(root.rglob('*.py'))
    for path in python_files:ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
    for path in list(root.rglob('*.bat'))+list(root.rglob('*.cmd')):
        text=path.read_text(encoding='utf-8')
        assert 'Desktop' not in text and '%USERPROFILE%' not in text,str(path)
    sys.path[:0]=[str(root/'host_ui'),str(root/'recorded_experiment')]
    dependencies={}
    for name in ('numpy','scipy','matplotlib','serial','tkinter'):
        module=importlib.import_module(name)
        dependencies[name]=str(getattr(module,'__version__',getattr(module,'TkVersion','available')))
    import run_paired
    _,loaded,preflight=run_paired.load_frozen(root)
    from wav_bundle import load_import_bundle
    examples=[]
    for folder in (root/'example_inputs').iterdir():
        if folder.is_dir() and (folder/'input.wav').is_file():
            data,metadata,components=load_import_bundle(folder/'input.wav')
            assert metadata.get('reference_bundle_verified') is True
            examples.append(dict(name=folder.name,samples=len(data),has_reference='target' in components))
    suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='test_*.py')
    tested=unittest.TextTestRunner(verbosity=1).run(suite)
    assert tested.wasSuccessful()
    return dict(status='OFFLINE_PACKAGE_CHECK_PASS',project_file_references=len(references),rom_references=sorted(roms),
        source_files_hashed=len(manifest['sources']),bitstream_sha256=sha(bitstream),
        python_files_parsed=len(python_files),dependencies=dependencies,examples=examples,
        recorded_pair=preflight,unit_checks=tested.testsRun,
        hardware_or_fpga_validation='NOT EXECUTED',serial_opened=False,gui_opened=False,pnr_executed=False,
        scope='Structure, original bytes, frozen inputs and host numeric/source checks; no new FPGA performance result')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    report=verify(args.root)
    if args.report:args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

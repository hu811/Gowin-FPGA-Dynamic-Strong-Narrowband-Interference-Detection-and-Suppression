"""Resolve an immutable legacy manifest path without rewriting its JSON."""
from pathlib import Path
import argparse, hashlib, json
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[2]
def resolve(old):
    manifest=json.loads((ROOT/'legacy_path_map.json').read_text(encoding='utf8'))
    entry=manifest['paths'].get(old)
    if entry is None:
        record=next((x for x in manifest['installed_or_legacy'] if x['original']==old),None)
        if record is None:raise KeyError(old)
        entry=dict(sha256=record['sha256'])
    candidates=[]
    if 'path' in entry:candidates.append((ROOT/entry['path']).resolve())
    normalized=old.replace('\\','/')
    marker='/Desktop/agent/FPGA_Competition/'
    if marker in normalized:
        tail=normalized.split(marker,1)[1]
        candidates.extend([PROJECT/'支撑材料/项目资料'/tail,PROJECT/tail])
    prefix='d:/fpga_competition/'
    if normalized.lower().startswith(prefix):
        tail=normalized[len(prefix):]
        candidates.extend([PROJECT/'支撑材料/原始验证/D盘原记录'/tail,
                           PROJECT.parent/'FPGA_Competition_历史归档_20261027/D盘旧工程'/tail,
                           PROJECT/tail])
    for p in candidates:
        if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256']:return p
    raise ValueError('Frozen dependency unavailable or changed: '+old)
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('original_path');v=a.parse_args();print(resolve(v.original_path))

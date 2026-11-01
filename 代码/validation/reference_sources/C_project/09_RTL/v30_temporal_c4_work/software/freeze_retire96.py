"""Freeze the one authorized second diagnostic after rejecting RETIRE64."""
from pathlib import Path
import hashlib,json,shutil
HERE=Path(__file__).resolve().parent


def main():
    path=HERE/'retire96_policy.json';assert not path.exists()
    value=dict(name='RETIRING_ONLY96FADE',version=1,base='V27 adopted C4,controller902174; no EAP/FF or RETIRE64 mixed in.',
        retirement='Only an original actual committed configuration that removes at least one token, preserves every surviving token ID/frequency, and adds none.',
        fade='Q5alpha1..32, each repeated3accepted sample ticks, exactly96samples. Same18Q16banks/continuous history/Q15 generator; no fractional precision change.',
        other_updates='Entry/add/frequency/ID replacement use original32sample fade and actual original availability sample.',
        newstrong_priority='If a non-removal update arrives during extended retirement, immediately make the retiring target current and start the new ordinary32fade that same sample. Explicitly log this acceleration/discontinuity; no extra sample hold for new updates.',
        pending_retirement='A further pure-removal update during retirement occupies one latest pending update and starts when prior96fade finishes; record extra wait. No extra coefficients orFIRbranches.',
        permissions='All V27 candidate strong/lifecycle/ID/version/miss and task rules unchanged; no weak renewal or source truth.',
        primary='Original whole-primary NRMSE vs unchanged same21dB fixed1024; do not substitute dynamic energy or choose only favorable cases.',
        development='All3 previously seen C4 inputs,quiet_high,new gap2ms development input. New gap8ms heldout not used to alter this policy.',
        stop_rule='Exactly96 authorized, no fade-length sweep. If mixed/poor results, reject and return to frequency mechanism rather than continue retirement timing tuning.',
        limits=['Fixed original control/configuration availability, no new complete RTL execution timing.',
                'Actual controller backpressure/coefficient-service changes from a96fade must be tested in RTL before adoption.',
                'Emergency new-update priority can cause a discontinuity; do not hide its output/probe/target damage.'])
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    previous=HERE/'retire64_diagnostic/retire64_counterfactual_frozen.py'
    assert not previous.exists();shutil.copyfile(HERE/'retire64_counterfactual.py',previous)
    manifest=dict(status='FROZEN_BEFORE96_PERFORMANCE',policy_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        rejected64_results_sha256=hashlib.sha256((HERE/'retire64_diagnostic/results.json').read_bytes()).hexdigest(),
        rejected64_source_sha256=hashlib.sha256(previous.read_bytes()).hexdigest())
    (HERE/'retire96_freeze_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()

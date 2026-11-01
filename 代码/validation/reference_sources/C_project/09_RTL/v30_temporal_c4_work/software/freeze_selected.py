"""Choose unchanged V27 before accessing the new heldout's detector/output result."""
from pathlib import Path
import hashlib,json
HERE=Path(__file__).resolve().parent


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output=HERE/'selected_variant.json';assert not output.exists()
    freeze=json.loads((HERE/'freeze_manifest.json').read_text(encoding='utf8'))
    value=dict(status='FINAL_POLICY_FROZEN_BEFORE_NEW_HELDOUT_RESULT',choice='KEEP_V27',
        controller_sha256='902174283ea44636d59aa59c88a73cabfe551ccd4358599edd11fb56baa25897',
        no_algorithm_or_rtl_change=True,capacity=4,
        reasons=['RETIRE64 and RETIRING_ONLY96FADE both improved one old case but worsened other old cases; no net/no-loss upgrade.',
                 'Predeclared temporal-gap2ms development input already shows originalV27 advantage versus same21defined1024 software reference.',
                 'Application-case benefit is not new-code improvement. Full-period1dB midpoint failures and18dB negative sensitivities remain.'],
        rejected_candidates=[dict(policy='COMMITTED_SLOT_RETIRE64',
            policy_sha256=sha(HERE/'retire64_policy.json'),result=str(HERE/'retire64_diagnostic/results.json'),
            result_sha256=sha(HERE/'retire64_diagnostic/results.json')),
            dict(policy='RETIRING_ONLY96FADE',policy_sha256=sha(HERE/'retire96_policy.json'),
                 result=str(HERE/'retire96_diagnostic/results.json'),result_sha256=sha(HERE/'retire96_diagnostic/results.json'))],
        predeclared_inputs=freeze['cases'],input_plan_sha256=freeze['plan_sha256'],
        heldout_authorization='Root explicit message confirms frozenKEEP_V27 and one gap8ms actualV27+fixed21/18 evaluation now; keep outcome regardless of result.',
        no_further_search='No further algorithm or threshold/frequency/phase/seed changes this round, including3bin diagnostic/256window/retirement duration.',
        metric='At least20%relative reduction in ORIGINAL whole-primary NRMSE vs same21dB integerDTFT1024 is aspiration, not guaranteed. Retain all cases and18sensitivity.',
        timing='ActualV27=completeRTL at requested50MHz,not board measured. Software1024=zero analysis/config waiting,not quality bound. Common-backendHYBRID may be separate strengthening evidence only.',
        physical='No P&R run or newfs inV30. C4 prior physical failure remains outside this task.')
    output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(choice=value['choice'],sha256=sha(output)),indent=2))


if __name__=='__main__':main()

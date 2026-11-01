"""Compact V30 evidence with old negatives and heldout kept in one report."""
from pathlib import Path
import csv
import json
from collections import defaultdict
import score_selected as selected
bench=selected.bench
HERE=Path(__file__).resolve().parent


def f(value):return '—' if value is None else f'{value:.4f}'


def main():
    output=HERE/'results';data=bench.read(output/'results.json')
    with (output/'dynamic_energy.csv').open(encoding='utf-8-sig',newline='') as file:dynamic=list(csv.DictReader(file))
    energy=defaultdict(float);n_events=defaultdict(int);censored=defaultdict(int)
    for row in dynamic:
        if int(row['interval_start_ms'])==0 and int(row['interval_end_ms'])==50:
            key=(row['case'],row['method']);energy[key]+=float(row['error_energy_counts2'] or 0)
            n_events[key]+=1;censored[key]+=int(row['censored']=='True')
    dynamics=[]
    for comparison in data['comparisons']:
        case=comparison['case'];reference='conventional1024_fixed'+str(comparison['reference_threshold_db'])+'_dtft'
        a,b=energy[case,'actual_v27_q15'],energy[case,reference]
        if b:
            dynamics.append(dict(case=case,reference_threshold_db=comparison['reference_threshold_db'],
                current_error_energy_counts2=a,reference_error_energy_counts2=b,
                relative0to50ms_error_energy_reduction_percent=100*(1-a/b),
                events=n_events[case,'actual_v27_q15'],censored_events=censored[case,'actual_v27_q15']))
    hybrid=[]
    for path in sorted((HERE/'hybrid_results').glob('*/results.json')):
        value=bench.read(path)
        if value['status']=='COMPLETE_CASE_COMMON_BACKEND_HYBRID':
            hybrid.append(dict(path=str(path),sha256=bench.sha(path),**value))
    queue=[]
    import audit_queue_trace
    for case in selected.NEW:
        export=selected.folder(case)
        if (export/'verification.json').exists():
            value=audit_queue_trace.audit(export)
            queue.append(dict(case=case,**value))
    rejected=[]
    for name in ('retire64_diagnostic','retire96_diagnostic'):
        path=HERE/name/'results.json';value=bench.read(path)
        rejected.append(dict(name=name,source=str(path),sha256=bench.sha(path),
            decision='REJECTED_NO_RTL_IMPLEMENTATION',kind='FIXED_TRAJECTORY_COUNTERFACTUAL_NOT_COMPLETE_RTL',
            summaries=[dict(case=s['case'],method=s['method'],primary_nrmse_percent=s['primary_nrmse_percent'],
                target_nrmse_percent=s['primary_target_nrmse_percent'],full_worst_probe_db=s['worst_probe_amplitude_abs_change_db'])
                for s in value['summaries']],pending=value['pending']))
    compact=dict(status=data['status'],choice='KEEP_V27_UNCHANGED',
        policy_source_sha256=data['controller_sha256'],selection_sha256=data['selected_variant_sha256'],
        plan_sha256=data['plan_sha256'],scores_sha256=bench.sha(output/'results.json'),
        comparisons=data['comparisons'],summaries=data['summaries'],dynamic_energy_comparisons=dynamics,
        hybrid=hybrid,actual_queue=queue,rejected_candidates=rejected,
        primary_common_backend_comparisons=[entry['comparisons'][0] for entry in hybrid],
        temporal_mechanism=(bench.read(HERE/'diagnosis/temporal_mechanism.json')
                            if (HERE/'diagnosis/temporal_mechanism.json').exists() else None),
        recommendation='Report bounded temporal-close input benefit if observed; do not call it newly optimized code or universalC4 superiority.',
        unresolved='Full-period midpoint protection>1dB remains, stable target distortion remains, previous18sensitivities and old weak-benefit cases remain.',
        physical='NoP&R/no newfs/no board measurements; previousC4 physical failure not resolved by this task.',
        temporal_arrival_boundary='Both2/8ms second-pair source arrivals precede first-group firstcommit(10.5/12.03126ms). They test arrival during detection/configuration processing,not after priorgroup stable suppression. Later observed queuecontains active-group versionupdates.',
        definitions=dict(primary='Same original fixture range[firstjam,lastoff+50ms),reference target+probes delayed320samples,no fittedgain/phase/delay.',
            nrmse='100*norm(output-reference)/norm(reference),lowerbetter. Not percentusefulsignal lost or interference attenuation.',
            relative_gain='100*(1-currentNRMSE/referenceNRMSE).20% target uses this metric,not errorenergyreduction.',
            settled='Separate diagnostic: source positions40msafter previouschange and10msbefore next; not a replacement for whole-primary.',
            probe='Maximum absolute8ms energy-ratio dB at2ms step across fullvalidperiod,includingstartup/withdrawal.',
            dynamics='Every start/end event0–50ms,cut at nextchange andflagcensored; fourfixedsubintervals retained inCSV.',
            hybrid='Actualcommon RTL manager/Q15generator/queue/fade/FIR with replayed mixed-input frontend.1024 computationzero-time;not full1024RTL.',
            admission='Same numeric21dB gate and same physical187.5..750Hz reference band;512 uses10refs/side,1024 uses19. This is not proof of equal false-alarm probability.18dB prior-developed sensitivity is retained.',
            queue='Actualreceiving-edge configurationdescriptors;not inputsignalqueue or two physicalFIR branches.'))
    bench.write(output/'compact_summary.json',compact)
    bench.csv_write(output/'compact_summary.csv',data['comparisons']);bench.csv_write(output/'dynamic_energy_paired.csv',dynamics)
    lines=['# 四中心时间接近场景：完整RTL与固定1024对照','',
        '**最终选择是保持V27原算法和硬件不变。** 两个退休策略已因其他场景退步拒绝，没有采用V30新优化代码。'
        '新输入在测试前固定：组内125 Hz、组间相隔超过750 Hz；第二组比第一组晚2 ms（开发）或8 ms（独立测试），各组成员再错开1 ms。','',
        '第二组源进入时，第一组还没有首次提交（首组首次提交约+10.5/+12.031 ms）。'
        '准确说法是第一组仍在检测/配置处理中第二组已到来，不是第一组已稳定抑制。'
        '后续逐时钟队列重叠包含已active组的版本更新；旧175 ms输入才覆盖稳定后再加入，但没有跨组running且waiting。','',
        '主指标仍是整个原primary区间的NRMSE，未换成局部动态能量或挑选好看的窗口。'
        'NRMSE是输出误差范数除以参考范数，不是“丢失了多少百分比的有用信号”。','',
        '|输入|参照门槛|原V27 NRMSE %|1024软件 NRMSE %|相对误差减少 %|原V27全程最坏中点 dB|原V27/1024稳定 NRMSE %|',
        '|---|---:|---:|---:|---:|---:|---:|']
    for row in data['comparisons']:
        lines.append(f"|{row['case']}|{row['reference_threshold_db']}dB|{f(row['current_primary_nrmse_percent'])}|"
            f"{f(row['reference_primary_nrmse_percent'])}|{f(row['relative_primary_nrmse_reduction_percent'])}|"
            f"{f(row['current_full_worst_probe_db'])}|{f(row['current_settled_nrmse_percent'])}/{f(row['reference_settled_nrmse_percent'])}|")
    lines+=['','21 dB是统一数值门槛主表；原开发集选出的18 dB保留为敏感性对照。旧场景低收益及负收益全部保留。'
            '两边参考背景覆盖相同187.5–750 Hz物理范围，512每侧10点、1024每侧19点；同21 dB不等于已校准相同虚警率。'
            '软件1024采用原固定点FFT/DTFT、同Q15权重生成和641抽头合并FIR，但分析/配置计算等待为零；'
            '零等待不是质量上界，也不是完整1024硬件测量。','',
            '新输入的primary NRMSE仍为144%/164%量级，说明完整积分依然受强干扰动态残留支配；'
            '不能包装成已经高保真。相对旁路改善、稳定误差、每路衰减、目标分量和中点损伤必须同时看。','',
            '按输入事件时刻对原primary所有样本分摊，两新输入相对21软件参照节省的误差能量约88.64%/91.99%来自加入后0–50 ms，'
            '约11.36%/8.13%来自退出阶段，稳定段没有收益。这里是既有输出的描述性分摊，不是新增指标替换或窗长单独因果证明。'
            '开发输入的21参照210次更新中有150次在全四中心稳定段、四中心到四中心更新多数只变一格；'
            '独立输入稳定段却没有更新仍有收益，因此不能只归因于频率抖动，也不能把队列实际触发直接说成质量提升的原因。','',
            '旧四中心“两对错开”输入的组首出现间隔是175.75 ms，与这轮预先限定的2/8 ms输入不同。'
            '旧三中心与四中心也不是同一输入，不能把成绩差异全部归因于容量。','',
            '原软件参考下，新输入20%NRMSE连续三窗证据时间并没有更快：开发输入约76.075 ms对76.063 ms，'
            '独立输入约33.888 ms对31.875 ms。独立输入全primary积分的第0/2路干扰衰减约19.684/19.255 dB，'
            '仍不到20 dB；全程中点也超过1 dB。这些失败与全过程NRMSE相对优势同时保留。','',
            '## 同一实际配置与滤波后端的HYBRID对照','',
            '仅在有完整导出、自复现和独立评分后列入；没有完成的输入不借软件结果填充。','',
            '|输入|原V27 NRMSE %|1024/21 HYBRID NRMSE %|相对减少 %|原V27/参照最坏中点 dB|','|---|---:|---:|---:|---:|']
    for entry in hybrid:
        row=entry['comparisons'][0]
        lines.append(f"|{entry['case']}|{f(row['current_primary_nrmse_percent'])}|{f(row['baseline_primary_nrmse_percent'])}|"
                     f"{f(row['current_relative_nrmse_reduction_percent'])}|{f(row['current_full_probe_abs_db'])}/{f(row['baseline_full_probe_abs_db'])}|")
    lines+=['','HYBRID的配置管理、系数构建、队列、渐变和连续FIR运行真实RTL，前端1024计算仍是预计算、零耗时。'
            '原方案预服务控制轨迹须逐输出及绝对时钟复现完整RTL。它不是完整在线1024硬件，也不证明纯窗长因果或资源优势。','',
            '## 未采用的改动','',
            '- RETIRE64：固定延后已提交槽的退休64样本。虽然改善一个旧输入，但其他输入误差和中点损伤更大；未做RTL。',
            '- 仅退休96样本渐变：Q5 alpha每档重复3个样本，其他更新仍32样本；同样出现退步，未做RTL。',
            '- 这两项只是固定实际轨迹的离线反事实。未把零额外构建时间当作实际硬件收益，也未继续搜索时长。','',
            '## 保留的限制','',
            '- 全程中点1 dB失败没有被稳定诊断区间隐藏；信号刚出现时的局部假中心/估频偏差仍存在。',
            '- 稳定区间与动态全程分别报告；不少稳定结果基本相当或原方案略差，不能声称稳定滤波更强。',
            '- 新2/8 ms输入的定义和KEEP_V27选择都在独立测试结果之前冻结；无种子/相位/门槛重选。',
            '- 全部时间为请求50 MHz下的RTL仿真时钟，不是板测。本任务没有P&R，也没有新fs。','',
            '完整逐8 ms表、每路干扰衰减、目标分量、恢复事件、所有变化后0–50 ms及分段误差在results.json与同目录CSV中。',
            '相同图中的队列为配置任务等待，不是信号采样缓存；它证明该路径实际触发，不单独证明滤波质量提升。','']
    # Lead with actual common backend when it is available, keeping the software/18
    # reference and all earlier negative inputs in a separate, clearly labelled table.
    start=lines.index('## 同一实际配置与滤波后端的HYBRID对照')
    stop=lines.index('## 未采用的改动')
    block=lines[start:stop]
    block[0]='## 主结果：同一实际配置与滤波后端（HYBRID）'
    del lines[start:stop]
    target=next(i for i,line in enumerate(lines) if line.startswith('|输入|参照门槛|'))
    lines[target:target]=block+['## 软件21/18参考与所有旧输入结果','']
    (output/'results.md').write_text('\n'.join(lines),encoding='utf8')
    bench.write(output/'summary_manifest.json',dict(status=data['status'],files={str(p):bench.sha(p) for p in
        (output/'results.json',output/'compact_summary.json',output/'compact_summary.csv',output/'dynamic_energy_paired.csv',output/'results.md')},
        selected_variant_sha256=bench.sha(HERE/'selected_variant.json'),source_sha256=bench.sha(Path(__file__))))
    print(data['status'],bench.sha(output/'compact_summary.json'))


if __name__=='__main__':main()

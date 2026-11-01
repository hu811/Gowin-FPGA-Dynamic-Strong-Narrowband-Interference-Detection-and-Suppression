# 出现时间间隔扫描

本实验只改变干扰的出现时刻，比较当前完整四中心RTL与冻结1024点软件参考。不是两套硬件板测，也不是只改变窗长的单变量消融。当前512通路包含真实在线检测、1024辅助校频、授权、队列、系数准备、32样本渐变和连续641抽头FIR；1024参考的前端与系数准备计算等待为零，软件只保留最新待执行配置。

冻结文件为`experiment_plan.json`。16个条件对应15个唯一输入；两条曲线共享内部1ms／组间8ms这一输入。所有条件使用同一16000点目标、同一噪声、4个频率、固定正弦峰值幅度和同一前端增益。每路按开通时刻固定初相：`sin(2πf(t-onset)+φ)`；这不等于全局时间相位不变。输入、目标和所有原始输出均保留，不根据结果调参数或删除负值。

两条扫描：

- `internal_delta`：第一对100／101ms出现，第二对首成员108ms，末成员在108ms加δ出现；δ为0、1、2、4、8、16、32、64ms。
- `pair_gap`：两对内部均1ms，第一对首成员100ms，第二对首成员为100ms加gap；gap使用相同8档。

退出时刻固定为350／360／370／380ms。每路使用固定峰值幅度，名义J/S为+20dB，有限持续区间的实际J/S记录在计划中，不按各条件重新归一化。0ms是同步边界，不保证任何出现间隔必然触发排队。

主区间固定为0～490ms，固定补偿320样本，不拟合增益。进入诊断区间为90～230ms，稳定段为240～320ms，两者分别报告。曲线主指标为`100×(E1024−E512)/E1024`，E为对应区间NRMSE；负值表示该区间1024误差较低。进入、退出和剩余干扰共同影响全段成绩；不能用稳定段替代主区间，也不能将本次0.5s成绩与原报告其他2s或短动态主区间混用。

## 运行与核查

本机使用已有Python环境，不安装软件、不修改生产RTL：

```powershell
& 'D:/FPGA_Competition/.venv/Scripts/python.exe' -B verify_runtime.py
& 'D:/FPGA_Competition/.venv/Scripts/python.exe' -B run_sweep.py --prepare-only
& 'D:/FPGA_Competition/.venv/Scripts/python.exe' -B run_sweep.py
```

首次`verify_runtime.py`把现有完整RTL仿真程序与冻结ROM复制到独立ASCII工作目录，并运行4096点黄金前缀；已存在的黄金输出不覆盖。已有`golden_calibration.json`通过后不必重复。完整扫描采用最多两个native仿真进程；已通过且同输入SHA的case可复用，生产目录不写入。

`runtime_identity.json`核查所有仿真文件、现成程序SHA和当前工程。唯一非空白RTL差异是未由TB实例化的数字顶层OLED复位线；TB直接实例化`pro_v2_auto_core`。RAM使用已有等价同步仿真原语。4096点新运行与既有4096点、16000点黄金记录均已与相同输入的实际板测输出逐LSB对照。

`progress.json`记录完成、失败与运行中的case；每个case的`case_result.json`保留完整计数、输入输出SHA和三种窗口指标。`sweep_metrics.csv`为两轴×8点×3窗口的长表，`results.json`汇总15个唯一结果。失败保留日志和原始输出，不填造成功成绩。

`cases/case_XX/`保存混合输入、两路输出、1024决策与统计、完整RTL日志及CSV。ASCII运行工作副本位于`D:/FPGA_Competition/timing_sweep_work_20261028/`，结果复制回本目录。Python／native程序的墙钟运行时间只用于执行预算，不能当作FPGA响应延迟。

本次未进行新综合、布局布线、烧录或UART采集，也没有改最终报告和代码压缩包。完整输入及指标由另一个只读审查脚本独立复核后再出图。

# 干扰出现时间差扫描：评分复现包

本目录收录15个唯一混合输入、完整512通路RTL输出、1024软件参考输出及48行评分。16个条件分两条扫描，各8档；内部1 ms／组间8 ms这一输入由两轴共用。它们是完整RTL与软件参考的对照，不是两套硬件板测，也不是只改变窗长的消融实验。

## 一条命令复核

在代码包根目录、已配置的Python3.12环境运行：

```text
python -B validation/timing_sweep/verify_metrics.py
```

该命令只读取本包相对路径：核对257个原文件SHA、原计划与版本身份、15组三条int16数组、RTL最终计数，并独立从输出数组重算48行NRMSE和相对差异，写出`verification_result.json`。不访问原D盘支撑路径，不执行串口、原生RTL或P&R。NumPy是评分脚本唯一第三方依赖。输入重建的独立检查结果原样保存在`independent_plan_audit.json`，本评分脚本不重新生成输入。

每个条件使用同一16000点目标、噪声、前端增益、四个频率和固定正弦峰值幅度。每路开通时固定初相，公式为`sin(2πf(t-onset)+φ)`；这不是全局相位固定。频率2311.3／2436.3及4972.7／5097.7 Hz，每对相隔125 Hz。每路名义J/S为+20 dB；实际有限区间J/S在计划中保留，不按条件重新归一化。

- `internal_delta`：第一对100／101 ms，第二对首成员108 ms；仅改变第二对末成员相对首成员的δ。
- `pair_gap`：两对内部均1 ms；仅改变两对首成员之间的gap。
- 两轴均扫描0、1、2、4、8、16、32、64 ms；退出时刻固定350／360／370／380 ms。

主评分区间0～490 ms；进入诊断区间90～230 ms；稳定段240～320 ms。统一补偿320样本，不拟合增益、不补缺失输出。NRMSE为`100*||y_aligned-target||/||target||`；相对降低为`100*(E1024-E512)/E1024`，负值原样保留，不能以稳定段替代全段。

512结果来自50 MHz／32 kHz完整自动核，包含512检测、1024辅助、授权、队列、系数准备、渐变与FIR。1024是有限精度软件参考，仍积累1024点观测窗，额外检测/系数计算等待取零，配置更新调度也与RTL有差异。两路均只向检测器提供混合样本。时间间隔为0或很短不保证必然触发排队；Python/native墙钟时间不是FPGA响应时间。

## 数据与来源

- `experiment_plan.json`、`base_reference.npz`：未改字节的冻结计划、目标和噪声。计划内原绝对路径仅用于溯源，复核只使用`cases/case_XX/`。
- 每个case：输入、两个输出、`case_result.json`、软件决策/统计，以及`rtl_trace/`控制CSV和原`run.log`。
- `sweep_metrics.csv`、`results.json`：原48行长表及15个唯一结果。
- `runtime_identity.json`、`run_source_identity.json`、`golden_calibration.json`：原运行源绑定与黄金校准。原native程序本轮复用，未重新编译；黄金前缀及既有原生结果与实际板端输出的核对范围见校准记录。
- `frozen_files.json`：原文件到当前相对位置的逐文件SHA和大小。`provenance/`保留原扫描脚本，仅用于代码溯源；这些历史脚本含原工作路径，便携评分入口为本目录`verify_metrics.py`。

没有复制native/Verilator大型缓存、重复逐样本CSV或`input.mem`。相同数值已在NPY中保留；原完整trace和程序仍在比赛工作站支撑材料中。图与绘图脚本放在代码包外的支撑材料。

## 可选RTL重建说明（本轮未执行）

`rtl_rebuild/tb_full_quality.v`与`prim_fast_sim.v`为实际运行所用原文件；TB直接实例化四中心`pro_v2_auto_core`。源绑定到本包`c4/rtl`及`c4/rom`。与旧模拟快照唯一非空白差异为未由该TB实例化的数字顶层OLED复位连接；其余匹配情况详见`runtime_identity.json`。TB保留的早期版本注释不作为本轮版本依据。

安装Icarus Verilog后，可从代码包的`c4`目录执行以下命令；`files.f`中列出37个本包RTL及两个仿真文件，ROM相对路径仍从`c4/rom`读取。建议将整包放在ASCII路径下运行。下面是复现方式说明，未在本轮重新编译或运行验证，不能据此新增RTL通过结论：

```text
iverilog -g2012 -s tb_full_quality -I rtl -o ../validation/timing_sweep/rtl_rebuild/sim.vvp -c ../validation/timing_sweep/rtl_rebuild/files.f
```

在同一`c4`目录，把选中case的已存输入转为hex并新建输出目录（下面以case_00为例）：

```text
python -B -c "import numpy as n;from pathlib import Path;p=Path('rebuild_case00');p.mkdir();x=n.load('../validation/timing_sweep/cases/case_00/input.npy');(p/'input.mem').write_text(''.join(format(int(v)&65535,'04x')+'\n' for v in x),encoding='ascii')"
vvp ../validation/timing_sweep/rtl_rebuild/sim.vvp +SAMPLES=16000 +INPUT=rebuild_case00/input.mem +FOLDER=rebuild_case00/
```

Icarus速度可能明显低于原native运行方式；本目录不携带已编译程序。新运行应先检查最终计数/故障，再把新`outputs.csv`与对应`output_512_rtl.npy`逐样本比较，不能仅凭编译成功替代数值验证。实际Gowin重建与烧录仍使用本包原`c4/pro_v2.gprj`，本实验不产生新`.fs`。

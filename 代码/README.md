# 基于高云FPGA的动态强窄带干扰检测与抑制系统：可复现代码

对应报告：`基于高云FPGA的动态强窄带干扰检测与抑制系统_设计报告.docx`（位于代码目录或压缩包的同级目录）。

本目录是当前 V32 四中心工程的交付副本。RTL、ROM、管脚及时钟约束和现有烧录文件均保留；除下文列出的原始验证记录外，没有包含旧版本、三中心工程、其他历史采集和大型生成缓存。此次仅整理路径和构建入口，没有重新综合、布局布线或板测，不能把本目录的文件检查称为重新编译通过。

## FPGA 工程与烧录文件

- Gowin FPGA Designer **教育版 1.9.11.03**；ACG525，器件 **GW5A-LV25UG324C2/I1，GW5A-25A（A版），UBGA324**。
- 顶层 `pro_v2_digital_top_4`，时钟 50 MHz，采样率 32 kHz，512点主检测、1024点辅助校频，最多4中心，641抽头合并FIR。
- 在教育版打开 `c4/pro_v2.gprj`，工程工作目录必须为 `c4`；`rom/`是相对于这个目录的初始化文件。`impl/pro_v2_process_config.json`保留完整工具选项，IncludePath已改为相对`rtl`。
- 特别保留 **Use SSPI/CPU as GPIO**、Correct Hold Violation、50 MHz、Place Option 3、Route Option 0。OLED使用相应复用管脚，不能取消GPIO选项。
- 可在Gowin界面执行Synthesize与Place & Route，或运行 `c4/run_build.bat`。若安装目录不同，先设置环境变量 `GW_SH`为教育版`gw_sh.exe`完整路径。`build.tcl`从自身目录定位工程。
- `c4/ACG525_C4_V32_512_OLED.fs`是未修改的既有位流，SHA256见同目录`manifest.json`；重建后新产物为`c4/impl/pnr/pro_v2.fs`。GUI旧完成状态已清空，避免无缓存时显示“重复编译”。
- 断电保留使用外部Flash，操作见下节“Flash烧录与断电启动”；SRAM下载仅用于临时调试。

## Flash烧录与断电启动

1. 板子供电，连接Gowin USB下载器与板上JTAG，打开教育版Programmer，扫描并选择与本工程一致的GW5A-25A。
2. Device configuration中选择Access Mode：`External Flash Mode 5A`；Operation：`exFlash Erase,Program,Verify 5A`；Device：`Generic Flash`；Start Address：`0x000000`。
3. 选择`c4/ACG525_C4_V32_512_OLED.fs`，保存配置后启动操作。等待日志显示`Program and Verify Flash successfully`再断电；`Bulk Erase`仅擦除，不会写入程序。
4. 断开板子电源、UART USB和JTAG，待指示灯熄灭后重新供电，不再下载。接回板上UART并用上位机发送输入，确认实际输出回传。上电WAIT、计数0是等待数字输入的正常状态。

上述参数来自本项目已归档的Flash写入和校验记录。烧录保留的是固件，输入、检测历史和临时计数在重新上电后复位。


## Python 与上位机

安装64位Python **3.12**，运行`SETUP_PYTHON.bat`，按`requirements-lock.txt`安装固定版本。若没有`py`启动器，可设置`PYTHON312`为Python3.12解释器路径。包内`.venv`不预置；依赖安装需要可用的软件包源。

`RUN_C4_GUI.bat`启动带同输入1024离线对照入口的新版上位机。入口优先使用本目录`.venv`，也可设置`FPGA_PYTHON`；在本工作站的目录布局下可回退到`D:/FPGA_Competition/.venv`。直接拷贝Windows虚拟环境通常仍依赖其`pyvenv.cfg`指向的基础Python，不等于跨电脑便携安装。

电脑负责准备输入、UART传输、显示与离线评价；在线检测、授权、系数构建及FIR均在FPGA中执行。导入参考与标签只留在电脑。普通WAV需32kHz、单声道PCM16；不要只移动带参考实验包中的WAV。

## 固定实录目标＋人工干扰实验

`RUN_RECORDED_CONTROL.bat`预载无附加干扰输入，`RUN_RECORDED_DRM.bat`预载加入干扰输入；打开界面并不启动串口。`recorded_experiment/CHECK_INPUTS_ONLY.bat`只检查冻结输入及本地文件，不访问串口。接板后可运行`RUN_RECORDED_PAIRED_TEST.bat`重新执行真实UART成对收发：程序先做无附加干扰组，再做人工加干扰组，最后默认显示人工加干扰组结果。

接收参考包含原录音噪声、信道与导频，不是无噪声发射真值。两组均2秒、64000点，使用相同前端增益。评价固定对齐320样本，不拟合增益。混合频带抑制和中点谱变化不能替代独立20dB/1dB分量验收，也不证明BER改善。结果保存在`recorded_experiment/captures/`；生成的回放入口只显示已保存的实际输出，不再次连接串口。

录音来源说明见`real_recorded_inputs/来源与操作说明.txt`。厂商录音未找到明确开放再分发许可，当前文件用于本地比赛验证；公开发布代码时须先核实录音再分发权限。历史来源路径仅作溯源，运行只读取本目录配套文件。

## 离线检查与限制

`verify_delivery.py`核对工程引用、ROM、配置、位流、冻结输入和Python语法／依赖；不会连接串口、启动GUI、运行FPGA仿真或P&R。资源及时序成绩沿用报告中的原版本实测证据，本包不生成新成绩。已有数字样本可重复处理；模拟ADC、射频前端、解调与误码率评价未接入。


## 报告结果与验证源码

`validation/RUN_VERIFY.bat`以包内数据重新计算报告所列两组动态NRMSE、表4(b)四路衰减、8 ms/2 ms分量联合窗计数、组件切换暂态及原始资源XML，并进行整数FIR轨迹重放。参考算法、原始验证TB/ROM及冻结清单已放入`validation/`；独立的报告绘图脚本留在外部支撑材料。完整旧流水线重跑范围和未执行步骤见该目录README。

## 报告上位机截图对应的实际记录

2026年10月27日的两组实录完整板测已保存，每组均输入、接收64000点。原始数据位置：

- 无附加干扰组：`host_ui/captures/c4_20261027_223512_015471/`。
- 人工加干扰组：`host_ui/captures/c4_20261027_223556_432532/`。
- 前述数字生成输入实验A、B：`validation/board_gui_records/digital_a/`、`digital_b/`。

复制的旧记录逐文件SHA256及本次实录记录清单见`validation/board_gui_records/manifest.json`。原元数据保留原采集路径用于溯源，读取和离线复核只需本包内文件。截图保存在代码包外的报告支撑材料，代码包保留原始输入、实际输出、参考分量、场景及状态。

评价采用两种固定窗口：全段公共区间0～1.99 s，稳定段0.4～1.2 s；两者均固定对齐320样本（10 ms），不拟合增益、不填补缺失输出。NRMSE相对于同增益的原始目标参考，实录组参考为未人工加干扰的接收实录；不以无附加干扰组的滤波输出作为加干扰组参考。稳定段指标不能替代含进入、退出过程的全段指标。

三个启动文件行为保持不变：`RUN_RECORDED_CONTROL.bat`和`RUN_RECORDED_DRM.bat`只预载输入，需在界面手动点击开始收发；`RUN_RECORDED_PAIRED_TEST.bat`自动执行两组真实板端收发。保存的输出可通过上位机的“打开收发记录”查看，查看旧记录不重新连接串口。这些记录均为当前512主检测方案，不代表1024方案已在板上实测。

## 同一实录输入的1024点软件参考

`validation/recorded_1024_reference/`收录本次新增离线参考的源码、ROM、原始软件输出、决策及结果CSV。可在本代码包根目录执行`python -B validation/recorded_1024_reference/run_reference.py`；使用既定Python3.12环境即可，无需桌面或D盘支撑目录，该命令保留原固定实验复现方式，不新增1024硬件工程；可调输入的GUI入口见下节。

比较使用同一条实录目标＋人工干扰输入，512为实际板端回传，1024为有限精度软件参考：全段公共0～1.99s的NRMSE分别105.060124%、127.502390%，本方案相对降低17.601447%；稳定0.4～1.2s分别16.738723%、16.263575%，1024略好。本次不是两套板端硬件对照，也不能套用其他输入的30%以上结果。

参考保留原1024周期Hann、64样本步进、21dB原始局部谱准入、整数FFT/DTFT、641抽头Q16 FIR与32样本渐变；前端与配置计算等待取零，软件待更新配置调度与板端不同。固定对齐320样本，不拟合增益。冻结源码SHA、旧软件后端逐位复用检查及完整限制见该目录README、source_manifest.json和results.json。截图与绘图脚本仍留在代码包外的报告支撑材料。

## 可调输入与同样本1024软件对照

运行 `RUN_C4_GUI.bat`。可在原有风格的主界面修改场景并预览，也可导入32 kHz、单声道PCM16 WAV或一维signed-int16 NPY（NPY按32 kHz解释）。打开界面不会自动收发；选择板上UART串口后，点击“开始收发”，由现有512主检测固件实际处理输入。

板测完成后点击“1024离线对照”，再点击“计算1024”。对照窗口冻结本次完全相同的int16混合样本；蓝色标识FPGA实际回传，紫色标识1024软件参考。更换主窗口输入后重新打开对照窗口，旧输入结果不能冒充新输入结果；载入的FPGA记录必须通过样本、计数和哈希核对。

两路共用320样本对齐和观察区间。有经过核验的目标参考时显示恢复误差；普通WAV/NPY没有独立目标参考时仍可看波形、频谱，但不计算恢复误差。标签和参考只用于离线评价，不送入在线检测核。

当前板端在线检测范围1～7 kHz，最多四个中心；这些限制与输入电平、合法强单音等适用条件仍然有效，导入成功不等于对所有输入都能有效滤波。1024是离线数值参考，FFT/检测和系数准备的额外计算等待取零，仍保留1024点观测窗的样本积累；配置调度与板端有差异。不能将Python用时解释为FPGA响应时间，也不是两套板端硬件对照。对照窗口的新增计算不改变原FPGA固件、原上位机收发模块或原三个实录实验的冻结样本与手动/自动执行方式。

无需串口的检查可在代码包根目录运行：

```text
python -B -m unittest discover -s comparison_ui -p 'test_*.py'
```

测试包括数值参考回归、记录完整性/同区间评分及隐藏Tk界面检查；界面测试仅注入归档软件输出验证显示，不表示新的板测。

## 干扰出现时间差实验

`validation/timing_sweep/`保存两条出现时间差扫描：近邻一对内部时间差δ、两对首成员间隔gap，均为0、1、2、4、8、16、32、64 ms。16个条件对应15个唯一16000点输入，各有完整512通路RTL输出和1024软件参考输出，保留原计划、统计、控制日志及负值结果。它们不是新增板测；主评分固定0～490 ms，进入诊断90～230 ms，稳定段240～320 ms，不与其他时长实验混用。

在代码包根目录运行下列命令，可逐文件核对SHA并从原输出数组独立复核48行评分，结果写入该目录`verification_result.json`：

```text
python -B validation/timing_sweep/verify_metrics.py
```

该复核不打开UART、不运行RTL、不绘图。实际运行版本、数值精度与调度差异见子目录README；TB、仿真原语和引用本包`c4/rtl`、`c4/rom`的Icarus重建说明也已提供，本轮打包没有重新编译或运行该重建流程。图和绘图脚本仍放在代码包外的支撑材料。

## 统一启动入口

代码根目录保留以下入口，所有上位机入口均使用同一新版界面：

| 启动文件 | 用途 | 打开后是否自动收发 |
| --- | --- | --- |
| `RUN_C4_GUI.bat` | 通用上位机，可调整输入并使用1024离线对照 | 否，手动点击开始收发 |
| `RUN_RECORDED_CONTROL.bat` | 预载无附加干扰的实录输入 | 否，手动点击开始收发 |
| `RUN_RECORDED_DRM.bat` | 预载实录目标叠加人工干扰输入 | 否，手动点击开始收发 |
| `RUN_RECORDED_PAIRED_TEST.bat` | 选择板上UART后依次执行上述两组真实收发，结束后显示加干扰组结果 | 是，无需在GUI再点开始 |
| `SETUP_PYTHON.bat` | 首次建立Python环境并安装依赖 | 否，不连接板子 |

通用上位机入口统一为`RUN_C4_GUI.bat`，保留“1024离线对照”按钮。两个预载入口只选择不同输入；自动成对入口直接调用`recorded_experiment/run_paired.py`，子目录不再另设重复启动文件。

`python_env.cmd`是其他入口共用的环境辅助文件，不需要双击。重新编译使用`c4/run_build.bat`；离线复算使用`validation/RUN_VERIFY.bat`；`recorded_experiment/CHECK_INPUTS_ONLY.bat`只检查输入。历史记录中的`OPEN_CONTROL_RESULT.bat`和`OPEN_MIXED_RESULT.bat`仅回放已保存输出，不启动新的板测。

仅改变窗口入口与结果显示。原`host_ui`模块仍由新版界面复用，冻结样本、FPGA收发和原评分未更改；`experiment.json`的输入、位流及原主机源码哈希继续有效。打开输入/回放界面不自动收发；只有自动成对实验入口会按原流程启动串口。1024软件计算仍由操作者点击启动。

## 非正弦AM数字板测补充

`validation/am_narrowband/`保存报告AM实验的冻结WAV/NPY输入、分量及场景、16000点真实UART回传、同输入1024软件输出和来源记录。在代码包根目录运行：

```text
python -B validation/am_narrowband/verify_metrics.py
```

该命令仅校验并重算已有数组：公共0～490 ms的NRMSE为206.572005%／266.907439%，本方案相对降低22.605377%；固定稳定240～320 ms为40.200869%／37.377557%，参考较低。250个64样本帧完整回收，输入ID`701f2becab0b`与报告截图一致。它不连接串口、不运行新检测或RTL，也不表示重新编译/布线通过。导入`validation/am_narrowband/prepared/input.wav`可手动执行新的板端收发，需保留同目录配套文件。原GUI同输入对照记录保留在`comparison_results/20261028_230454_196243_701f2bec`。详细条件与限制见子目录README。

## 录音来源与文件身份

实录目标来源：WiNRADiO Radio Luxembourg 6095 kHz中频录音。来源页：https://winradio.com/home/g313-drm.htm ；具体下载文件：https://winradio.com/home/drm-6095.if.zip （访问日期2026-11-01）。

解压原始文件`drm-6095.if.wav`，SHA256为`5fa96dcb2e92b3dd2c4da80f68baa116b570d0eae1602902d7ad8ed2c9171860`。实验固定截取原录音10～12秒，转换和配对输入身份见`real_recorded_inputs/来源与操作说明.txt`、各输入目录`source_metadata.json`及`recorded_experiment/experiment.json`。该哈希指原录音，不是转换后的输入WAV。

## 公开副本的隐私处理

历史来源路径中的个人用户名统一替换为 `User`，有关元数据文件的校验值同步更新。此匿名路径只用于记录与旧路径映射，不要求电脑上存在该用户目录。RTL、位流、ROM、Python程序、输入及实际输出数组均保持原始字节；匿名化不构成新的综合、布局布线或板测。

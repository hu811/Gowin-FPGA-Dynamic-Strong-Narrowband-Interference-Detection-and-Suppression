# 复现范围与操作

所有命令在 Windows、Python 3.12 环境执行。先进入 `代码`，运行 `SETUP_PYTHON.bat`；以下命令均以该目录为工作目录。

```bat
.venv\Scripts\python.exe -B verify_delivery.py
validation\RUN_VERIFY.bat
.venv\Scripts\python.exe -B validation/timing_sweep/verify_metrics.py
.venv\Scripts\python.exe -B validation/am_narrowband/verify_metrics.py
```

第一条检查工程、ROM、既有位流、冻结输入、Python 依赖和必要单元检查。后面三条从冻结数组和资源 XML 复算报告结果，不连接串口，不重新运行检测器、RTL、综合或 P&R。

若已安装其他独立 Python 环境，可直接用其解释器替代 `.venv\Scripts\python.exe`；批处理入口可设置 `FPGA_PYTHON`。不依赖原作者桌面路径。保留的原始元数据中包含历史工作路径，仅作溯源。

## 硬件重建

在教育版 Gowin FPGA Designer 1.9.11.03 中打开 `c4/pro_v2.gprj`，工作目录为 `c4`，执行 Synthesize、Place & Route；也可设置 `GW_SH` 后运行 `c4/run_build.bat`。器件为 GW5A-25A / GW5A-LV25UG324C2/I1，顶层 `pro_v2_digital_top_4`。

工程文件、工具过程配置 JSON、TCL、约束、RTL 与 ROM 一起保留。CPU/SSPI 管脚复用选项用于 OLED，不能仅复制 `.gprj` 而漏掉配套文件。成功后检查新报告与时序，再使用新 `.fs`。打包检查不代表新 P&R 通过。

## 真实板端收发

`RUN_C4_GUI.bat`进入新版上位机，保留同输入1024离线对照功能。`RUN_RECORDED_CONTROL.bat` / `RUN_RECORDED_DRM.bat` 分别预载无附加干扰/加人工干扰的实录输入，等待手动开始；`RUN_RECORDED_PAIRED_TEST.bat`直接调用`recorded_experiment/run_paired.py`，选择串口后自动执行两组真实收发；子目录不再保留重复启动入口。1024 参考由操作者点击后在电脑离线计算。

## 全 RTL 重跑与证据

配对仿真、组件仿真、时间差扫描的原始 TB、所需 ROM、数值源码与重建说明在 `validation/README.md` 及对应子目录。重跑所需 Icarus/Gowin 工具应单独安装。本次交付保留原始输出和既有位流，不把数组复算称为新的 RTL/板测。

仓库没有包含 Gowin 安装程序、Python 虚拟环境、缓存、视频工程或全部历史开发目录。报告图表生成脚本属于外部写作支撑材料；验证结论所依赖的数据和复算源码包含在本包中。

## Public-copy privacy maintenance

Personal usernames in archived source paths are replaced with `User`. Dependent metadata hashes are refreshed. Executable source, FPGA image/ROM, numerical input/output and scoring definitions remain byte-identical to the previous release. This is metadata maintenance, not a new hardware run.

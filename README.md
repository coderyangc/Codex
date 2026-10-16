# Android App Performance Monitor v2.0.9

使用 Windows CMD 和 ADB 采集 Android 应用 `com.uusafe.portal` 的运行性能，结果保存为 CSV。

当前交付版本：`v2.0.9`。版本号统一记录在工具标题、设备端采集脚本、配置文件、报告和 Windows EXE 文件名中。

本次发布压缩包：`android_app_perf_monitor_v2.0.9.zip`。

本工具不依赖 Python、PowerShell 或 Android Studio。采集逻辑在 Android 设备端执行，Windows 端只负责启动 ADB 命令并保存结果。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `run_android_app_perf_monitor.bat` | Windows CMD 启动入口 |
| `android_app_perf_monitor.sh` | Android 设备端采集脚本 |
| `generate_perf_report.bat` | Windows 报告生成入口 |
| `generate_perf_report.py` | CSV 汇总和离线 HTML 报告生成器 |
| `采集配置.conf` | 采集项目开关；每行都有中文注释，可直接编辑 |
| `collector_config.conf` | Windows 打包兼容副本，内容与中文配置文件一致 |
| `environment_validator_gui.py` | 中文图形化设备环境检查工具源码 |
| `build_environment_validator.bat` | Windows 一键生成环境检查 EXE |
| `performance_monitor_gui.py` | 完整性能采集 GUI 源码，可配置时长、间隔、设备、包名、报告类型 |
| `run_multi_device_monitor.sh` | macOS 多设备并行采集入口；按 ADB serial 分别生成 CSV 和日志 |
| `run_android_local_monitor.sh` | Android 终端 App 本地采集入口；不需要电脑或 ADB 连接 |
| `xlsx_writer.py` | 无第三方依赖的 Excel XLSX 写入器 |
| `build_performance_monitor.bat` | Windows 一键生成完整性能采集 EXE |
| `build_tools.py` | Windows 打包逻辑，避免 CMD 编码和多行命令解析问题 |
| `README.md` | 使用说明 |
| `VERSION.txt` | 当前发布版本号 |

## 使用前准备

1. Windows 已安装 ADB，并且 `adb` 已加入 PATH。
2. Android 设备已开启开发者选项和 USB 调试。
3. 用数据线连接设备，并在设备上确认 USB 调试授权。
4. 在 CMD 中确认设备状态：

```bat
adb devices -l
```

应用 `com.uusafe.portal` 应保持运行。若连接多台设备，必须使用 `--serial` 指定目标设备。

## macOS 多设备采集

在 macOS 上可直接使用 `run_multi_device_monitor.sh`。脚本会发现所有状态为 `device` 的 ADB 设备，并为每台设备并行生成独立 CSV 和日志，不会把多台设备的数据混在一起：

```bash
chmod +x run_multi_device_monitor.sh
./run_multi_device_monitor.sh --duration 10m --interval 10
```

只监控指定设备：

```bash
./run_multi_device_monitor.sh \
  --serial ABC123,192.168.1.20:5555 \
  --packages com.uusafe.portal \
  --duration 30m \
  --interval 5 \
  --output-dir ./reports
```

采集前只查看设备：

```bash
./run_multi_device_monitor.sh --list
```

查看将执行的命令但不开始采集：

```bash
./run_multi_device_monitor.sh --serial ABC123 --dry-run
```

输出文件格式：

```text
reports/portal_<serial>_<timestamp>.csv
reports/portal_<serial>_<timestamp>.log
```

每条采集命令都带有 `adb -s <serial>`；设备状态不是 `device` 时不会进入采集队列。

## Android 设备本地采集（不连接 Mac）

如果某台 Android 设备不能连接电脑，但设备上有终端 App，可以把下面两个文件放到设备同一目录：

```text
android_app_perf_monitor.sh
run_android_local_monitor.sh
```

然后在 Android 终端 App 中执行：

```sh
chmod +x android_app_perf_monitor.sh run_android_local_monitor.sh
./run_android_local_monitor.sh 10m 10 com.uusafe.portal ./portal_10m.csv
```

参数依次为：监控时长、采样间隔、包名、CSV 输出路径。也可以直接运行默认配置：

```sh
./run_android_local_monitor.sh
```

脚本不调用 `adb`，会在设备本地执行 `dumpsys`、`top`、`ps` 等 Android 命令，并在设备本地生成 CSV 和 `.log`。临时文件会自动选择终端 App 可写目录；不再固定要求 `/data/local/tmp`。

普通终端 App 的权限可能不足以读取其他应用的精确 I/O、私有目录、部分 PSS 或 Logcat。遇到权限不足时，CSV 保留空字段并由状态/日志说明，不会把不可读指标当成 0。

## 快速运行

在本目录打开 CMD：

```bat
run_android_app_perf_monitor.bat --duration 30m --interval 5 --output portal_30m.csv
```

指定设备序列号：

```bat
run_android_app_perf_monitor.bat --duration 2h --interval 10 --serial ABC123 --output portal_2h.csv
```

一次采集多个 App（包名之间使用英文逗号分隔）：

```bat
run_android_app_perf_monitor.bat --packages com.uusafe.portal,com.example.app --duration 30m --interval 5 --output multi_app_30m.csv
```

每个采样时间点会为每个包名输出一行。包名必须只包含字母、数字、点、下划线或连字符；重复包名会自动去重。

启动性能测试可选冷启动或热启动模式：

```bat
run_android_app_perf_monitor.bat --packages com.uusafe.portal --launch cold --duration 30s --interval 1 --output cold_start.csv
run_android_app_perf_monitor.bat --packages com.uusafe.portal --launch hot --duration 30s --interval 1 --output hot_start.csv
```

`cold` 会先强制停止目标应用再启动，`hot` 直接启动，二者都会使用首次前台观测时间作为启动耗时近似值。默认 `none` 不会主动操作应用，只记录首次发现进程和前台状态。

采集过程中按 `Ctrl+C` 可以提前停止，已经写入的 CSV 行仍会保留。

## 配置采集项目

编辑目录中的 `采集配置.conf`，把对应项目改为 `1` 开启、`0` 关闭。配置文件中的每个开关都有中文注释；当前默认只检测 CPU 和内存，其他项目默认关闭。

CSV 显示列由 `采集配置.conf` 中的 `DISPLAY_*` 开关控制。默认显示 CPU、RSS/PSS 内存和采集状态；Java Heap、Native Heap、Code、Graphics、Stack、ADB 状态默认隐藏。`COLLECT_*` 控制是否执行采集，`DISPLAY_*` 只控制是否输出 CSV 列。所有开关都有中文注释，CSV 本身不再写入开关状态列。

采集器会在 CSV 文件开头写入 UTF-8 BOM，Windows Excel 可直接双击打开中文表头；如果使用旧版 Excel 仍出现乱码，请通过“数据 → 自文本/CSV”导入并选择 UTF-8 编码。

使用默认配置文件：

```bat
run_android_app_perf_monitor.bat --config collector_config.conf --duration 30m --interval 5 --output portal.csv
```

GUI 中可以在高级设置里选择其他配置文件路径。命令行入口使用 ASCII 参数，避免 Windows CMD 编码问题。可配置项目如下：

| 配置项 | 控制内容 |
| --- | --- |
| `COLLECT_PROCESS` | 进程数、PID、主进程 PID、线程数 |
| `COLLECT_CPU` | 全核 CPU、单核标准 CPU、主线程 CPU |
| `COLLECT_MEMORY` | RSS、PSS、Java/Native/Code/Graphics/Stack 内存 |
| `COLLECT_DISK_IO` | 磁盘读写速率和累计读写量 |
| `COLLECT_NETWORK` | UID、网络收发速率、累计流量、网络类型 |
| `COLLECT_BATTERY` | 电量、充电状态、电压、电流、应用估算耗电量 |
| `COLLECT_THERMAL` | 电池温度、最高热区温度、热限制状态 |
| `COLLECT_UI` | 总帧数、卡顿帧数、卡顿比例 |
| `COLLECT_STORAGE` | 应用数据、APK、应用总存储 |
| `COLLECT_STARTUP` | 前后台状态、切换、首次进程/前台、启动观测 |
| `COLLECT_STABILITY` | 崩溃、ANR、Logcat 错误计数 |
| `COLLECT_SYSTEM` | 整机 CPU、负载、内存、可用存储 |
| `COLLECT_DEVICE` | 设备序列号、厂商、型号、Android 版本等 |

大类父开关和细项开关均可配置；父开关为 `0` 时，该大类下所有细项都不会输出：

| 大类 | 父开关 | 可单独控制的细项 |
| --- | --- | --- |
| 进程 | `DISPLAY_PROCESS` | `DISPLAY_PROCESS_COUNT`、`DISPLAY_PROCESS_PIDS`、`DISPLAY_PROCESS_MAIN_PID`、`DISPLAY_PROCESS_THREAD_COUNT` |
| CPU | `DISPLAY_CPU` | `DISPLAY_CPU_PCT_ALL_CORES`、`DISPLAY_CPU_PCT_ONE_CORE`、`DISPLAY_MAIN_THREAD_CPU_PCT` |
| 内存 | `DISPLAY_MEMORY` | `DISPLAY_MEMORY_RSS`、`DISPLAY_MEMORY_PSS`、`DISPLAY_MEMORY_JAVA_HEAP`、`DISPLAY_MEMORY_NATIVE_HEAP`、`DISPLAY_MEMORY_CODE`、`DISPLAY_MEMORY_GRAPHICS`、`DISPLAY_MEMORY_STACK` |
| 磁盘 I/O | `DISPLAY_DISK_IO` | `DISPLAY_DISK_READ_BPS`、`DISPLAY_DISK_WRITE_BPS`、`DISPLAY_IO_READ_BYTES`、`DISPLAY_IO_WRITE_BYTES` |
| 网络 | `DISPLAY_NETWORK` | `DISPLAY_NETWORK_UID`、`DISPLAY_NETWORK_RX_BPS`、`DISPLAY_NETWORK_TX_BPS`、`DISPLAY_NETWORK_RX_BYTES`、`DISPLAY_NETWORK_TX_BYTES`、`DISPLAY_NETWORK_TYPE` |
| 电量 | `DISPLAY_BATTERY` | `DISPLAY_BATTERY_LEVEL_PCT`、`DISPLAY_BATTERY_STATUS`、`DISPLAY_BATTERY_TEMPERATURE_C`、`DISPLAY_BATTERY_VOLTAGE_MV`、`DISPLAY_BATTERY_CURRENT_UA`、`DISPLAY_APP_POWER_MAH` |
| 温度 | `DISPLAY_THERMAL` | `DISPLAY_MAX_THERMAL_TEMP_C`、`DISPLAY_THERMAL_STATUS` |
| 图形/UI | `DISPLAY_UI` | `DISPLAY_FRAMES_TOTAL`、`DISPLAY_JANKY_FRAMES`、`DISPLAY_JANK_PCT` |
| 存储 | `DISPLAY_STORAGE` | `DISPLAY_APP_DATA_KB`、`DISPLAY_APK_KB`、`DISPLAY_APP_STORAGE_KB` |
| 启动/前后台 | `DISPLAY_STARTUP` | `DISPLAY_FOREGROUND`、`DISPLAY_FOREGROUND_TRANSITION`、`DISPLAY_FIRST_PROCESS_SEEN_S`、`DISPLAY_FIRST_FOREGROUND_SEEN_S`、`DISPLAY_STARTUP_OBSERVED_S`、`DISPLAY_STARTUP_STATUS` |
| 稳定性 | `DISPLAY_STABILITY` | `DISPLAY_CRASH_COUNT`、`DISPLAY_ANR_COUNT`、`DISPLAY_LOGCAT_ERROR_COUNT` |
| 系统资源 | `DISPLAY_SYSTEM` | `DISPLAY_SYSTEM_CPU_PCT`、`DISPLAY_SYSTEM_LOAD_1M`、`DISPLAY_SYSTEM_MEM_TOTAL_MB`、`DISPLAY_SYSTEM_MEM_AVAILABLE_MB`、`DISPLAY_SYSTEM_STORAGE_FREE_KB` |
| 设备信息 | `DISPLAY_DEVICE` | `DISPLAY_DEVICE_SERIAL`、`DISPLAY_DEVICE_MANUFACTURER`、`DISPLAY_DEVICE_BRAND`、`DISPLAY_DEVICE_MODEL`、`DISPLAY_ANDROID_VERSION`、`DISPLAY_ANDROID_SDK`、`DISPLAY_BUILD_ID` |
| 公共状态 | 无父开关 | `DISPLAY_ADB_STATE`、`DISPLAY_STATUS` |

关闭采集项目后，对应 CSV 字段会留空，不会被当作 0 参与报告统计；关闭显示项目后，对应列不会写入 CSV。采集/显示开关只从 `采集配置.conf` 读取，不会写入 CSV。配置文件只会被推送到设备临时目录，采集结束后自动删除。

小白用户建议直接使用中文 GUI EXE；`run_android_app_perf_monitor.bat` 是稳定的命令行入口，支持 `--packages`、`--duration`、`--interval`、`--serial`、`--output` 和 `--config` 参数。

## 图形化环境检查 EXE

双击 `build_environment_validator.bat`，或在 Windows CMD 中执行：

```bat
build_environment_validator.bat
```

脚本会生成：

```text
dist/AndroidDeviceEnvironmentCheck_v2.0.9.exe
```

说明：当前交付工作机为 macOS，未伪造不可运行的 Windows EXE；`build_environment_validator.bat` 是 Windows 原生打包入口。Windows 上执行时窗口会保留在屏幕上，成功或失败都会提示；详细信息写入 `build_environment_validator.log`。

打开工具后，工具会自动执行 `adb devices -l`：如果只有一台在线设备，会自动绑定该设备；多台设备时会在“连接的手机”下拉框中列出设备供选择。界面按“三步”设计：先确认应用和手机，再选择检查内容，最后点击“开始检查并生成报告”。高级的 ADB/Python 路径默认隐藏，只有环境异常时才需要展开。

- `adb.exe` 路径
- `python.exe` 路径
- 设备序列号
- 一个或多个应用包名
- Excel 报告保存路径

检测项目覆盖 ADB 连接、应用安装、设备信息、CPU、内存/PSS、磁盘 I/O、网络 UID、电量/耗电、温度、gfxinfo、应用存储、Logcat 稳定性和 Python 报告环境；GUI 默认只勾选 CPU 和内存。检测完成后默认生成真正的 `.xlsx` Excel 报告；GUI 中可以自由勾选 Excel、HTML、JSON 任意组合，至少选择一种。Excel 内含“检测汇总、检测结果、设备上下文、运行参数”四个中文工作表。默认报告目录优先使用 Windows 桌面或文档目录，避免小白用户遇到安装目录无写入权限。EXE 本身不捆绑 ADB，实际检测使用界面中指定的 ADB 路径；报告生成器和 `采集配置.conf` 会由打包脚本同步到 `dist` 目录。

命令行启动器在未提供 `--serial` 时也会自动绑定唯一在线设备；如果检测到多台设备，会提示使用 `--serial` 指定目标，避免采集到错误设备。

## 生成 HTML 报告

采集完成后，可使用 Python 3 生成离线 HTML 报告。报告生成器只使用 Python 标准库，不需要安装第三方包：

```bat
generate_perf_report.bat --input portal_30m.csv --output portal_30m_report
```

生成目录包含：

```text
portal_30m_report/
├─ report.html       便携式 HTML 报告，可直接用浏览器打开
├─ summary.csv       汇总指标，可用 Excel 打开
├─ 字段说明.csv      CSV 字段中文名称、单位和说明
├─ summary.json      结构化汇总结果
├─ device_info.json  设备信息
└─ run_config.json   本次报告生成配置
```

报告包括设备信息、技术摘要、CPU/PSS/卡顿趋势图、平均值、P95、最大值、最后有效值、状态分布、缺失指标和限制说明。图表内嵌在 HTML 中，不依赖网络。

如果 CSV 包含多个包名，报告目录会变为：

```text
multi_app_30m_report/
├─ report.html                 多应用汇总入口
├─ package_summary.csv         每个包名的采样和状态汇总
└─ packages/
   ├─ com.uusafe.portal/
   │  └─ report.html           单个 App 的完整中文报告
   └─ com.example.app/
      └─ report.html
```

多应用报告会按包名分别计算指标，不会把不同 App 的 CPU、内存或卡顿数据混成一个统计值。

原始采集 CSV 默认使用“中文（英文）”双语字段名，英文标识保持稳定，便于脚本和 Excel 继续处理；`字段说明.csv` 提供每个字段对应的中文名称、单位、含义和数据来源。HTML 报告的“指标统计”表中也会直接显示中文说明。

HTML 报告顶部提供中文菜单，可快速跳转到技术摘要、关键指标、趋势证据、指标统计、数据质量、采集方法、限制说明、建议下一步和待确认问题。

也可以直接双击 `generate_perf_report.bat`，通过中文菜单输入 CSV 路径并生成报告。

如果 Windows 没有 Python 3，仍然可以正常使用 ADB 采集器和 CSV 输出；只是不能运行本地 HTML 报告生成器。

## 完整性能采集 EXE（推荐小白使用）

`environment_validator_gui.py` 只负责环境检测；需要真正采集 Android 应用运行性能时，请使用新的完整采集工具：

1. 在 Windows 上双击 `build_performance_monitor.bat`，生成 `dist/AndroidAppPerformanceMonitor_v2.0.9.exe`。
2. 双击 EXE，工具会自动识别 ADB；只有一台在线设备时会自动绑定，连接多台时在“设备序列号”下拉框选择。
3. 填写一个或多个包名、采集时长（例如 `30s`、`10m`、`2h`）和采样间隔，然后点击“开始采集并生成报告”。
4. 默认生成 CSV 原始数据和 Excel 报告；也可以勾选 HTML、JSON。报告保存目录和 `logs` 日志目录都可在界面中配置。
5. 在“第三步：勾选采集项目”中可以查看全部采集大类和细项：CPU、内存、磁盘 I/O、网络、电量、温度、图形/UI、存储、启动/前后台、稳定性、系统资源、设备信息和公共状态。勾选“采集此类”决定是否执行该大类；勾选“显示到 CSV”及其细项决定报告显示哪些列。
6. “全部勾选”“清空选择”“恢复默认”“保存配置文件”均可直接操作；界面勾选结果会保存为中文注释配置文件，并用于本次采集。

完整采集 EXE 会直接调用界面中指定的 `adb.exe`，运行时不需要电脑安装 Python，也不会修改 ADB 或 Android 设备。Windows 打包脚本会在工具目录创建独立的 `.build_venv`，PyInstaller 只安装到这个打包环境，不会向电脑当前 Python 环境安装依赖。设备端执行的是目录内的 `android_app_perf_monitor.sh`，采集结束后只清理本次推送的临时配置文件。

日志规则固定为：

- 日志目录：`报告保存目录\logs\`；文件名为 `YYYY-MM-DD.log`，按自然日切割。
- 保留最近 7 个自然日，启动采集和写入日志时自动清理过期文件。
- 日志目录总量最大 1GB；超过上限时优先删除最旧日志，极端情况下当天日志只保留末尾内容。

Excel 报告包含四个中文工作表：

- `采集数据`：每个包名、每个采样点的原始 App 性能数据。
- `采集摘要`：行数、时长、CPU、PSS 内存、状态分布等汇总。
- `运行参数`：设备、包名、时长、间隔、报告目录和日志策略。
- `设备信息`：设备序列号、厂商、型号、Android 版本和 SDK 等上下文信息。

完整采集工具的报告是 App 进程维度为主：CPU、RSS/PSS、主线程 CPU、磁盘 I/O、网络 UID、电量/温度、启动/前后台、稳定性等字段由 `采集配置.conf` 的 `COLLECT_*` 控制，CSV 字段由 `DISPLAY_*` 控制。当前默认采集 CPU 和内存；细分类别既可以在配置文件中逐项开关，也可以直接在 EXE 的“采集项目”面板中勾选。

`v2.0.3` 修复了 GUI 采集时设备端脚本未通过 ADB 标准输入传入的问题；`v2.0.4` 增加了设备端 stderr、ADB 返回码和失败阶段的详细诊断；`v2.0.5` 重写命令行入口，解决 CMD 中文编码和混合换行导致的 `'r' is not recognized` 问题；`v2.0.6` 修复 `adb shell sh -s` 参数错位导致配置路径被识别为 `none` 的问题；`v2.0.7` 增加完整采集项目可视化勾选和配置保存；`v2.0.8` 将 GUI 调整为浅色、低对比边框、青绿色主操作和 macOS 风格标题栏；`v2.0.9` 修复部分 Android 系统 `awk` 不兼容包名去重表达式导致的空包名错误，并兼容旧版参数占位符。

注意：当前交付工作机为 macOS，无法在本机直接生成可运行的 Windows EXE；`build_performance_monitor.bat` 是 Windows 原生打包入口。Windows 上首次打包会下载 PyInstaller 到工具目录的 `.build_venv`，不会安装到系统 Python，也不会修改 ADB 或 Android 设备。窗口不会直接闪退，详细信息写入 `build_performance_monitor.log`。

## 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--duration` | `10m` | 监控时长，支持秒数或 `s`、`m`、`h`，例如 `600`、`30s`、`10m`、`2h` |
| `--interval` | `5` | 采样间隔，单位为秒，也支持 `5s`、`1m` |
| `--packages` | `com.uusafe.portal` | 一个或多个包名，多个包名使用英文逗号分隔 |
| `--launch` | `none` | 启动模式：`none`、`cold` 或 `hot`；`cold` 会 force-stop 并重新启动应用 |
| `--config` | 程序目录下的 `采集配置.conf` | 采集开关配置文件；Windows 端会自动推送到 Android 临时目录 |
| `--serial` | 自动选择 | ADB 设备序列号，多设备连接时使用 |
| `--output` | `android_app_perf.csv` | CSV 输出路径，可以是相对路径或绝对路径 |

例如，运行 600 秒、每 3 秒采样一次：

```bat
run_android_app_perf_monitor.bat --duration 600 --interval 3 --output D:\reports\portal.csv
```

## 输出报告

默认输出文件是 `android_app_perf.csv`，相对路径以执行 CMD 时的当前目录为准，不一定是脚本所在目录。

主要字段如下：

- `cpu_pct_all_cores`：应用占整机所有 CPU 核心总算力的百分比。
- `cpu_pct_all_cores`：目标 App 占设备所有 CPU 核心总算力的比例。例如 8 核设备使用 12.5%，表示占整机总算力约 12.5%。
- `cpu_pct_one_core`：把整机总算力换算成“单个 CPU 核心满载=100%”的口径，计算方式约为 `cpu_pct_all_cores × CPU 核心数`。例如 8 核设备使用整机 12.5%，单核标准约为 100%，可理解为大约占满 1 个核心；该值超过 100% 是正常的，表示同时使用了超过 1 个核心。
- `rss_mb`、`pss_mb`：进程 RSS 和应用 PSS 内存，单位 MB。
- `java_heap_mb`、`native_heap_mb`、`code_mb`、`graphics_mb`、`stack_mb`：应用内存分区，单位 MB。
- `network_rx_Bps`、`network_tx_Bps`、`network_rx_bytes`、`network_tx_bytes`：应用 UID 网络收发速率和累计流量。
- `battery_level_pct`、`battery_temperature_c`、`battery_voltage_mv`、`battery_current_ua`：电池状态信息。
- `app_power_mah`：系统 batterystats 能提供时的应用估算耗电量。
- `max_thermal_temp_c`、`thermal_status`：热区最高温度和系统热限制状态。
- `foreground`、`foreground_transition`：应用前后台状态和切换事件。
- `startup_observed_s`、`startup_status`：使用 `--launch cold/hot` 时的启动观测结果；按采样粒度近似，不等同于精确首帧耗时。
- `crash_count`、`anr_count`、`logcat_error_count`：采集期间 Logcat 中观察到的崩溃、ANR 和错误行。
- `main_thread_cpu_pct`、`thread_count`：当前 `main_thread_cpu_pct` 实际是主进程 CPU（包含主进程下线程合计），`thread_count` 是主进程线程数；字段名为历史命名，不代表严格的 UI 主线程 CPU。
- `system_cpu_pct`、`system_load_1m`、`system_mem_available_mb`、`system_storage_free_kb`：整机资源上下文；系统内存单位 MB。
- `disk_read_Bps`、`disk_write_Bps`：采样期间磁盘读写速率，单位 B/s。
- `io_read_bytes`、`io_write_bytes`：进程累计磁盘读写量，单位字节。
- `frames_total`、`janky_frames`、`jank_pct`：`dumpsys gfxinfo` 可读取时的渲染帧和卡顿帧数据。
- `app_data_kb`、`apk_kb`、`app_storage_kb`：应用数据、APK 和合计占用，单位 KB。
- `status`：`ok` 表示主要指标可读；`no_process` 表示应用进程未运行；`partial` 表示进程存在但部分指标受权限或系统版本限制。
- 采集/显示开关：不写入 CSV，请查看本次使用的 `采集配置.conf`；配置文件中的每个项目均有中文说明。

应用内存和系统内存统一使用 MB；旧版 CSV 中的 `_kb` 字段仍可由报告生成器读取并自动换算为 MB。应用数据目录、APK 和存储占用仍保留 KB 字段，因为它们属于存储空间而不是运行内存。

CSV 默认使用中英文双语表头，例如 `CPU 占用率（单核标准）（cpu_pct_one_core）`、`网络接收速率（network_rx_Bps）`，可以直接在 Excel 中阅读，同时保留英文标识便于脚本处理。如需恢复纯英文表头，把 `采集配置.conf` 中的 `CSV_HEADER_MODE=zh-en` 改为 `CSV_HEADER_MODE=en`。

`字段说明.csv` 已细化为字段名、中文名、数据范围、指标类型、单位、详细含义、计算口径、数据来源、限制说明和显示开关 10 列。查看 `data_scope` 可区分应用级指标、设备级上下文和公共状态；查看 `display_switch` 可定位对应的 `DISPLAY_*` 配置项。

每条记录还会附带设备上下文：

- `device_serial`：设备序列号，部分系统可能不允许读取。
- `device_manufacturer`、`device_brand`、`device_model`：设备厂商、品牌和型号。
- `android_version`、`android_sdk`：Android 系统版本和 SDK 级别。
- `build_id`：系统 Build ID。

首次采样的 CPU 百分比和磁盘读写速率可能为空，因为这些指标需要前后两个采样点计算。读取不到的指标会留空，不会伪造为 0。

## 常见问题

### `No ready ADB device`

执行以下命令检查：

```bat
adb devices -l
```

如果显示 `unauthorized`，请解锁设备并确认 USB 调试授权；如果显示 `offline`，重新插拔数据线或重启 ADB 服务。

### 多台设备连接

使用：

```bat
run_android_app_perf_monitor.bat --serial 设备序列号 --duration 30m
```

### CSV 中出现 `partial`

这通常表示 Android 系统限制了 `/proc`、PSS、磁盘 I/O 或 `gfxinfo` 的读取。保留空值是预期行为；请不要把空值当成 0。

### CSV 中出现 `no_process`

说明采样时没有发现 `com.uusafe.portal` 或其子进程。请确认应用已经启动，并在整个采集期间保持运行。

## 直接调用 Android 端脚本

也可以绕过批处理文件，手工执行。先把配置文件推送到设备：

```bat
adb push collector_config.conf /data/local/tmp/android_app_perf_monitor_config.conf
adb shell sh -s 10m 5 com.uusafe.portal none /data/local/tmp/android_app_perf_monitor_config.conf < android_app_perf_monitor.sh > portal.csv
```

手工采集多个包名：

```bat
adb push collector_config.conf /data/local/tmp/android_app_perf_monitor_config.conf
adb shell sh -s 10m 5 com.uusafe.portal,com.example.app none /data/local/tmp/android_app_perf_monitor_config.conf < android_app_perf_monitor.sh > multi_app.csv
```

手工执行冷启动测量：

```bat
adb push collector_config.conf /data/local/tmp/android_app_perf_monitor_config.conf
adb shell sh -s 30s 1 com.uusafe.portal cold /data/local/tmp/android_app_perf_monitor_config.conf < android_app_perf_monitor.sh > cold_start.csv
```

该命令含义是运行 10 分钟、每 5 秒采样一次，并将结果写入当前目录的 `portal.csv`。

## 注意事项

- 需要持续可用的 USB ADB 或网络 ADB 连接。
- 应用进程、Android 版本、厂商权限策略可能影响可读取的指标。
- 网络 UID 统计、应用耗电量、热区温度、Logcat、PSS 和内存分区可能受 Android 版本或权限限制；不可读时保留空值并标记 `partial`。
- `--launch cold` 会执行 `am force-stop` 和 `monkey`，会改变目标应用运行状态；普通监控请使用默认的 `--launch none`。
- `frames_total` 和 `janky_frames` 是设备 `gfxinfo` 提供的累计值，具体格式可能随 Android 版本变化。
- 本工具只采集和保存数据，不会自动启动、停止或操作目标应用。

#!/usr/bin/env python3
"""Generate a self-contained HTML report from android_app_perf_monitor CSV output."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


APP_VERSION = "2.0.9"

METRICS = [
    ("cpu_pct_one_core", "CPU（单核标准）", "%"),
    ("cpu_pct_all_cores", "CPU（整机总算力）", "%"),
    ("main_thread_cpu_pct", "主进程 CPU（字段名 main_thread_cpu_pct）", "%"),
    ("rss_mb", "RSS 内存", "MB"),
    ("pss_mb", "PSS 内存", "MB"),
    ("java_heap_mb", "Java Heap", "MB"),
    ("native_heap_mb", "Native Heap", "MB"),
    ("code_mb", "Code 内存", "MB"),
    ("graphics_mb", "Graphics 内存", "MB"),
    ("stack_mb", "Stack 内存", "MB"),
    ("disk_read_Bps", "磁盘读取速率", "B/s"),
    ("disk_write_Bps", "磁盘写入速率", "B/s"),
    ("network_rx_Bps", "网络接收速率", "B/s"),
    ("network_tx_Bps", "网络发送速率", "B/s"),
    ("network_rx_bytes", "累计网络接收量", "字节"),
    ("network_tx_bytes", "累计网络发送量", "字节"),
    ("battery_level_pct", "电池电量", "%"),
    ("battery_temperature_c", "电池温度", "°C"),
    ("battery_voltage_mv", "电池电压", "mV"),
    ("battery_current_ua", "电池电流", "μA"),
    ("app_power_mah", "应用耗电量", "mAh"),
    ("max_thermal_temp_c", "最高热区温度", "°C"),
    ("jank_pct", "卡顿比例", "%"),
    ("frames_total", "总渲染帧数", "帧"),
    ("janky_frames", "卡顿帧数", "帧"),
    ("first_process_seen_s", "首次发现进程耗时", "秒"),
    ("first_foreground_seen_s", "首次进入前台耗时", "秒"),
    ("startup_observed_s", "启动观测耗时", "秒"),
    ("crash_count", "崩溃次数", "次"),
    ("anr_count", "ANR 次数", "次"),
    ("logcat_error_count", "Logcat 错误数", "条"),
    ("system_cpu_pct", "整机 CPU 占用率", "%"),
    ("system_load_1m", "系统 1 分钟负载", ""),
    ("system_mem_total_mb", "整机总内存", "MB"),
    ("system_mem_available_mb", "整机可用内存", "MB"),
    ("system_storage_free_kb", "整机可用存储", "KB"),
    ("app_storage_kb", "应用总存储", "KB"),
]

DEVICE_FIELDS = [
    ("device_serial", "设备序列号"),
    ("device_manufacturer", "厂商"),
    ("device_brand", "品牌"),
    ("device_model", "设备型号"),
    ("android_version", "Android 版本"),
    ("android_sdk", "Android SDK"),
    ("build_id", "系统 Build ID"),
]

COLLECTION_FLAG_LABELS = {
    "process": "基础进程信息",
    "cpu": "CPU",
    "memory": "内存",
    "disk_io": "磁盘 I/O",
    "network": "网络",
    "battery": "电量",
    "thermal": "温度与热限制",
    "ui": "图形/UI",
    "storage": "应用存储",
    "startup": "启动与前后台",
    "stability": "崩溃/ANR/Logcat",
    "system": "系统资源",
    "device": "设备信息",
}

FIELD_DEFINITIONS = [
    ("timestamp", "时间戳", "—", "采样发生时 Android 设备的本地时间", "date"),
    ("elapsed_s", "已运行时长", "秒", "从本次采集开始计算的已运行时间", "采集脚本"),
    ("package", "应用包名", "—", "被监控的 Android 应用包名", "采集参数"),
    ("process_count", "进程数量", "个", "目标包名及其子进程的数量", "ps"),
    ("pids", "进程 PID", "—", "目标应用及子进程的进程号，多个 PID 使用竖线分隔", "ps"),
    ("main_pid", "主进程 PID", "—", "用于主线程和线程数观测的首个目标进程号", "ps"),
    ("thread_count", "线程数量", "个", "主进程当前线程数量", "/proc/pid/task"),
    ("cpu_pct_all_cores", "CPU 占用率（整机总算力）", "%", "应用 CPU ticks 占设备所有 CPU 核心总 ticks 的比例", "/proc/stat、/proc/pid/stat"),
    ("cpu_pct_one_core", "CPU 占用率（单核标准）", "%", "将整机总算力占用率换算为单核 100% 标准", "/proc/stat、/proc/pid/stat"),
    ("main_thread_cpu_pct", "主进程 CPU（字段名 main_thread_cpu_pct）", "%", "当前实现读取首个目标主进程的 CPU ticks；包含该主进程下线程的合计，不是单独 UI 主线程占用率", "/proc/pid/stat"),
    ("rss_mb", "RSS 内存", "MB", "目标进程常驻物理内存总量", "/proc/pid/status，KB 转换为 MB"),
    ("pss_mb", "PSS 内存", "MB", "按共享内存比例分摊后的应用内存占用", "dumpsys meminfo，KB 转换为 MB"),
    ("java_heap_mb", "Java Heap", "MB", "Java 堆内存占用", "dumpsys meminfo，KB 转换为 MB"),
    ("native_heap_mb", "Native Heap", "MB", "Native 堆内存占用", "dumpsys meminfo，KB 转换为 MB"),
    ("code_mb", "Code 内存", "MB", "代码段及相关映射占用", "dumpsys meminfo，KB 转换为 MB"),
    ("graphics_mb", "Graphics 内存", "MB", "图形相关内存占用", "dumpsys meminfo，KB 转换为 MB"),
    ("stack_mb", "Stack 内存", "MB", "栈内存占用", "dumpsys meminfo，KB 转换为 MB"),
    ("disk_read_Bps", "磁盘读取速率", "B/s", "相邻采样期间的进程磁盘读取平均速率", "/proc/pid/io"),
    ("disk_write_Bps", "磁盘写入速率", "B/s", "相邻采样期间的进程磁盘写入平均速率", "/proc/pid/io"),
    ("io_read_bytes", "累计磁盘读取量", "字节", "目标进程从采集开始前后累计读取的字节数", "/proc/pid/io"),
    ("io_write_bytes", "累计磁盘写入量", "字节", "目标进程从采集开始前后累计写入的字节数", "/proc/pid/io"),
    ("network_uid", "应用 UID", "—", "应用在 Android 上对应的 Linux UID", "dumpsys package"),
    ("network_rx_Bps", "网络接收速率", "B/s", "应用 UID 的相邻采样接收速率，依赖 xt_qtaguid", "/proc/net/xt_qtaguid/stats"),
    ("network_tx_Bps", "网络发送速率", "B/s", "应用 UID 的相邻采样发送速率，依赖 xt_qtaguid", "/proc/net/xt_qtaguid/stats"),
    ("network_rx_bytes", "累计网络接收量", "字节", "应用 UID 的累计接收字节数", "/proc/net/xt_qtaguid/stats"),
    ("network_tx_bytes", "累计网络发送量", "字节", "应用 UID 的累计发送字节数", "/proc/net/xt_qtaguid/stats"),
    ("network_type", "网络类型", "—", "当前系统网络类型，例如 WIFI；设备格式不同可能为空", "dumpsys connectivity"),
    ("battery_level_pct", "电池电量", "%", "设备当前电池百分比", "dumpsys battery"),
    ("battery_status", "充电状态", "—", "设备当前电池状态码", "dumpsys battery"),
    ("battery_temperature_c", "电池温度", "°C", "设备电池温度", "dumpsys battery"),
    ("battery_voltage_mv", "电池电压", "mV", "设备电池电压", "dumpsys battery"),
    ("battery_current_ua", "电池电流", "μA", "设备当前电流，部分 Android 版本不提供", "dumpsys battery"),
    ("app_power_mah", "应用耗电量", "mAh", "batterystats 可读取时的应用估算耗电量", "dumpsys batterystats"),
    ("max_thermal_temp_c", "最高热区温度", "°C", "可读取 thermal zone 中的最高温度，不等同于 CPU 温度", "/sys/class/thermal"),
    ("thermal_status", "热限制状态", "—", "系统 thermalservice 状态码", "dumpsys thermalservice"),
    ("frames_total", "总渲染帧数", "帧", "gfxinfo 返回的累计渲染帧数", "dumpsys gfxinfo"),
    ("janky_frames", "卡顿帧数", "帧", "gfxinfo 返回的累计卡顿帧数", "dumpsys gfxinfo"),
    ("jank_pct", "卡顿比例", "%", "gfxinfo 返回的卡顿帧比例", "dumpsys gfxinfo"),
    ("app_data_kb", "应用数据占用", "KB", "应用数据目录占用空间", "dumpsys package、du"),
    ("apk_kb", "APK 占用", "KB", "应用 APK 文件占用空间总和", "pm path、du"),
    ("app_storage_kb", "应用总存储", "KB", "应用数据占用与 APK 占用之和", "计算值"),
    ("foreground", "前后台状态", "0/1", "1=当前恢复的 Activity 属于目标包，0=不在前台", "dumpsys activity"),
    ("foreground_transition", "前后台切换", "—", "当前采样点发生 foreground 或 background 切换", "dumpsys activity"),
    ("first_process_seen_s", "首次发现进程耗时", "秒", "从采集开始到首次发现目标进程的时间，不等同于启动耗时", "采集脚本"),
    ("first_foreground_seen_s", "首次进入前台耗时", "秒", "从采集开始到首次发现目标包在前台的时间", "dumpsys activity"),
    ("startup_observed_s", "启动观测耗时", "秒", "使用 --launch cold/hot 时的首次前台观测时间，按采样粒度近似", "采集脚本、dumpsys activity"),
    ("startup_status", "启动测量状态", "—", "observed=使用启动模式；not_launched=未主动启动；not_observed=未观测到前台", "采集脚本"),
    ("crash_count", "崩溃次数", "次", "本次采集期间从 Logcat 事件中观察到的崩溃计数", "logcat"),
    ("anr_count", "ANR 次数", "次", "本次采集期间从 Logcat 事件中观察到的 ANR 计数", "logcat"),
    ("logcat_error_count", "Logcat 错误数", "条", "当前 Logcat 中匹配目标包的错误、异常、崩溃或 ANR 行数", "logcat"),
    ("system_cpu_pct", "整机 CPU 占用率", "%", "设备所有 CPU 核心合计的系统 CPU 占用率", "/proc/stat"),
    ("system_load_1m", "系统 1 分钟负载", "", "设备 /proc/loadavg 的 1 分钟负载值", "/proc/loadavg"),
    ("system_mem_total_mb", "整机总内存", "MB", "设备总内存", "/proc/meminfo，KB 转换为 MB"),
    ("system_mem_available_mb", "整机可用内存", "MB", "设备当前可用内存", "/proc/meminfo，KB 转换为 MB"),
    ("system_storage_free_kb", "整机可用存储", "KB", "/data 分区可用空间", "df"),
    ("adb_state", "ADB 状态", "—", "当前 Android 端脚本仍在运行即记录为 connected；主机断连会停止新增行", "ADB"),
    ("status", "采集状态", "—", "ok=正常；partial=部分指标不可读；no_process=应用未运行", "采集脚本"),
    ("device_serial", "设备序列号", "—", "Android 系统属性中的设备序列号，部分系统可能限制读取", "getprop"),
    ("device_manufacturer", "设备厂商", "—", "设备制造商名称", "getprop"),
    ("device_brand", "设备品牌", "—", "设备品牌名称", "getprop"),
    ("device_model", "设备型号", "—", "设备型号名称", "getprop"),
    ("android_version", "Android 版本", "—", "Android 系统版本号", "getprop"),
    ("android_sdk", "Android SDK", "级别", "Android SDK API Level", "getprop"),
    ("build_id", "系统 Build ID", "—", "Android 系统构建版本标识", "getprop"),
]
FIELD_DEFINITION_MAP = {item[0]: item[1:] for item in FIELD_DEFINITIONS}

# 字段级文档元数据。CSV 字段说明不仅列出名称和来源，还明确数据归属、计算口径、限制和显示开关。
DISPLAY_SWITCH_BY_FIELD = {
    "timestamp": "固定输出",
    "elapsed_s": "固定输出",
    "package": "固定输出",
    "process_count": "DISPLAY_PROCESS + DISPLAY_PROCESS_COUNT",
    "pids": "DISPLAY_PROCESS + DISPLAY_PROCESS_PIDS",
    "main_pid": "DISPLAY_PROCESS + DISPLAY_PROCESS_MAIN_PID",
    "thread_count": "DISPLAY_PROCESS + DISPLAY_PROCESS_THREAD_COUNT",
    "cpu_pct_all_cores": "DISPLAY_CPU + DISPLAY_CPU_PCT_ALL_CORES",
    "cpu_pct_one_core": "DISPLAY_CPU + DISPLAY_CPU_PCT_ONE_CORE",
    "main_thread_cpu_pct": "DISPLAY_CPU + DISPLAY_MAIN_THREAD_CPU_PCT",
    "rss_mb": "DISPLAY_MEMORY + DISPLAY_MEMORY_RSS",
    "pss_mb": "DISPLAY_MEMORY + DISPLAY_MEMORY_PSS",
    "java_heap_mb": "DISPLAY_MEMORY + DISPLAY_MEMORY_JAVA_HEAP",
    "native_heap_mb": "DISPLAY_MEMORY + DISPLAY_MEMORY_NATIVE_HEAP",
    "code_mb": "DISPLAY_MEMORY + DISPLAY_MEMORY_CODE",
    "graphics_mb": "DISPLAY_MEMORY + DISPLAY_MEMORY_GRAPHICS",
    "stack_mb": "DISPLAY_MEMORY + DISPLAY_MEMORY_STACK",
    "disk_read_Bps": "DISPLAY_DISK_IO + DISPLAY_DISK_READ_BPS",
    "disk_write_Bps": "DISPLAY_DISK_IO + DISPLAY_DISK_WRITE_BPS",
    "io_read_bytes": "DISPLAY_DISK_IO + DISPLAY_IO_READ_BYTES",
    "io_write_bytes": "DISPLAY_DISK_IO + DISPLAY_IO_WRITE_BYTES",
    "network_uid": "DISPLAY_NETWORK + DISPLAY_NETWORK_UID",
    "network_rx_Bps": "DISPLAY_NETWORK + DISPLAY_NETWORK_RX_BPS",
    "network_tx_Bps": "DISPLAY_NETWORK + DISPLAY_NETWORK_TX_BPS",
    "network_rx_bytes": "DISPLAY_NETWORK + DISPLAY_NETWORK_RX_BYTES",
    "network_tx_bytes": "DISPLAY_NETWORK + DISPLAY_NETWORK_TX_BYTES",
    "network_type": "DISPLAY_NETWORK + DISPLAY_NETWORK_TYPE",
    "battery_level_pct": "DISPLAY_BATTERY + DISPLAY_BATTERY_LEVEL_PCT",
    "battery_status": "DISPLAY_BATTERY + DISPLAY_BATTERY_STATUS",
    "battery_temperature_c": "DISPLAY_BATTERY + DISPLAY_BATTERY_TEMPERATURE_C",
    "battery_voltage_mv": "DISPLAY_BATTERY + DISPLAY_BATTERY_VOLTAGE_MV",
    "battery_current_ua": "DISPLAY_BATTERY + DISPLAY_BATTERY_CURRENT_UA",
    "app_power_mah": "DISPLAY_BATTERY + DISPLAY_APP_POWER_MAH",
    "max_thermal_temp_c": "DISPLAY_THERMAL + DISPLAY_MAX_THERMAL_TEMP_C",
    "thermal_status": "DISPLAY_THERMAL + DISPLAY_THERMAL_STATUS",
    "frames_total": "DISPLAY_UI + DISPLAY_FRAMES_TOTAL",
    "janky_frames": "DISPLAY_UI + DISPLAY_JANKY_FRAMES",
    "jank_pct": "DISPLAY_UI + DISPLAY_JANK_PCT",
    "app_data_kb": "DISPLAY_STORAGE + DISPLAY_APP_DATA_KB",
    "apk_kb": "DISPLAY_STORAGE + DISPLAY_APK_KB",
    "app_storage_kb": "DISPLAY_STORAGE + DISPLAY_APP_STORAGE_KB",
    "foreground": "DISPLAY_STARTUP + DISPLAY_FOREGROUND",
    "foreground_transition": "DISPLAY_STARTUP + DISPLAY_FOREGROUND_TRANSITION",
    "first_process_seen_s": "DISPLAY_STARTUP + DISPLAY_FIRST_PROCESS_SEEN_S",
    "first_foreground_seen_s": "DISPLAY_STARTUP + DISPLAY_FIRST_FOREGROUND_SEEN_S",
    "startup_observed_s": "DISPLAY_STARTUP + DISPLAY_STARTUP_OBSERVED_S",
    "startup_status": "DISPLAY_STARTUP + DISPLAY_STARTUP_STATUS",
    "crash_count": "DISPLAY_STABILITY + DISPLAY_CRASH_COUNT",
    "anr_count": "DISPLAY_STABILITY + DISPLAY_ANR_COUNT",
    "logcat_error_count": "DISPLAY_STABILITY + DISPLAY_LOGCAT_ERROR_COUNT",
    "system_cpu_pct": "DISPLAY_SYSTEM + DISPLAY_SYSTEM_CPU_PCT",
    "system_load_1m": "DISPLAY_SYSTEM + DISPLAY_SYSTEM_LOAD_1M",
    "system_mem_total_mb": "DISPLAY_SYSTEM + DISPLAY_SYSTEM_MEM_TOTAL_MB",
    "system_mem_available_mb": "DISPLAY_SYSTEM + DISPLAY_SYSTEM_MEM_AVAILABLE_MB",
    "system_storage_free_kb": "DISPLAY_SYSTEM + DISPLAY_SYSTEM_STORAGE_FREE_KB",
    "adb_state": "DISPLAY_ADB_STATE",
    "status": "DISPLAY_STATUS",
    "device_serial": "DISPLAY_DEVICE + DISPLAY_DEVICE_SERIAL",
    "device_manufacturer": "DISPLAY_DEVICE + DISPLAY_DEVICE_MANUFACTURER",
    "device_brand": "DISPLAY_DEVICE + DISPLAY_DEVICE_BRAND",
    "device_model": "DISPLAY_DEVICE + DISPLAY_DEVICE_MODEL",
    "android_version": "DISPLAY_DEVICE + DISPLAY_ANDROID_VERSION",
    "android_sdk": "DISPLAY_DEVICE + DISPLAY_ANDROID_SDK",
    "build_id": "DISPLAY_DEVICE + DISPLAY_BUILD_ID",
}

DEVICE_SCOPE_FIELDS = {
    "network_type", "battery_level_pct", "battery_status", "battery_temperature_c",
    "battery_voltage_mv", "battery_current_ua", "max_thermal_temp_c", "thermal_status",
    "system_cpu_pct", "system_load_1m", "system_mem_total_mb", "system_mem_available_mb",
    "system_storage_free_kb", "device_serial", "device_manufacturer", "device_brand",
    "device_model", "android_version", "android_sdk", "build_id",
}
PUBLIC_SCOPE_FIELDS = {"timestamp", "elapsed_s", "package", "adb_state", "status"}
RUN_STATE_FIELDS = {
    "foreground", "foreground_transition", "first_process_seen_s", "first_foreground_seen_s",
    "startup_observed_s", "startup_status", "crash_count", "anr_count", "logcat_error_count",
}

FIELD_CALCULATION_BY_FIELD = {
    "timestamp": "每个采样点调用 date 读取 Android 设备本地时间；格式为 ISO 风格时间戳。",
    "elapsed_s": "当前时间减去采集开始时间，单位秒；不是 App 自身运行时长。",
    "package": "来自 --package/--packages 参数；每个包名单独输出采样行。",
    "process_count": "在 ps 结果中匹配包名及其子进程名后的 PID 数量。",
    "pids": "将匹配到的 PID 按竖线拼接；空值表示采样时未发现目标进程。",
    "main_pid": "取匹配结果中的首个 PID 作为主进程观测对象；不等同于 Android UI 主线程 TID。",
    "thread_count": "统计主进程 /proc/<pid>/task 下的线程目录数量。",
    "cpu_pct_all_cores": "约等于 100 × App 进程 CPU ticks 增量 ÷ 设备全部 CPU ticks 增量；反映 App 占整机总算力比例。",
    "cpu_pct_one_core": "约等于 cpu_pct_all_cores × 设备 CPU 核心数；将单个核心满载定义为 100%。",
    "main_thread_cpu_pct": "当前实现约等于主进程 CPU ticks 增量 ÷ 设备全部 CPU ticks 增量 × 核心数；统计主进程合计，不是单独 UI 主线程。",
    "disk_read_Bps": "相邻采样点 read_bytes 的增量 ÷ 采样间隔秒数；单位 B/s。",
    "disk_write_Bps": "相邻采样点 write_bytes 的增量 ÷ 采样间隔秒数；单位 B/s。",
    "io_read_bytes": "目标进程 read_bytes 的累计值，多个目标进程求和；单位字节。",
    "io_write_bytes": "目标进程 write_bytes 的累计值，多个目标进程求和；单位字节。",
    "network_rx_Bps": "应用 UID 网络接收累计值的相邻采样增量 ÷ 采样间隔；单位 B/s。",
    "network_tx_Bps": "应用 UID 网络发送累计值的相邻采样增量 ÷ 采样间隔；单位 B/s。",
    "network_rx_bytes": "应用 UID 在 xt_qtaguid 统计中的累计接收字节数。",
    "network_tx_bytes": "应用 UID 在 xt_qtaguid 统计中的累计发送字节数。",
    "network_type": "从 dumpsys connectivity 解析当前设备网络类型；不是 App 独占网络类型。",
    "app_power_mah": "从 batterystats 读取按包名过滤的耗电估算；通常是统计周期累计值，不是瞬时功率。",
    "max_thermal_temp_c": "遍历可读 thermal zone，取最大温度原始值并除以 1000；不代表某个 App 的温度。",
    "thermal_status": "读取 thermalservice 的系统热限制状态码。",
    "frames_total": "读取 dumpsys gfxinfo 返回的累计渲染帧数。",
    "janky_frames": "读取 dumpsys gfxinfo 返回的累计卡顿帧数。",
    "jank_pct": "读取 dumpsys gfxinfo 返回的卡顿帧比例。",
    "app_storage_kb": "app_data_kb 与 apk_kb 之和；单位 KB。",
    "foreground": "当前恢复的 Activity 包名等于目标包名时为 1，否则为 0。",
    "foreground_transition": "与上一采样点比较 foreground 值，记录 foreground 或 background 切换。",
    "first_process_seen_s": "采集开始到首次发现目标进程的时间；不能单独证明是启动耗时。",
    "first_foreground_seen_s": "采集开始到首次发现目标包进入前台的时间。",
    "startup_observed_s": "使用 --launch cold/hot 时记录首次前台观测时间，受采样间隔影响。",
    "crash_count": "以采集开始前 Logcat 计数为基线，统计采集期间目标包新增的崩溃关键字数量。",
    "anr_count": "以采集开始前 Logcat 计数为基线，统计采集期间目标包新增的 ANR 关键字数量。",
    "logcat_error_count": "统计当前 Logcat 中按目标包名过滤的 error/exception/fatal/crash/anr 等匹配行数。",
    "system_cpu_pct": "约等于 100 × 设备非空闲 CPU ticks 增量 ÷ 设备总 CPU ticks 增量；整机口径。",
    "system_load_1m": "读取 /proc/loadavg 第一列；表示设备 1 分钟负载，不是 CPU 百分比。",
    "system_mem_total_mb": "读取 MemTotal 的 KB 后除以 1024，转换为 MB。",
    "system_mem_available_mb": "读取 MemAvailable 的 KB 后除以 1024，转换为 MB。",
    "system_storage_free_kb": "读取 /data 分区可用空间；单位 KB。",
    "adb_state": "Android 端脚本仍能输出采样行时记录 connected；不是 USB 链路质量评分。",
    "status": "进程存在且主要数据可读为 ok；无目标进程为 no_process；部分指标不可读时为 partial。",
}
FIELD_CALCULATION_BY_FIELD.update({
    "rss_mb": "对目标进程逐个读取 VmRSS，多个进程求和后由 KB 除以 1024，保留两位小数。",
    "pss_mb": "读取 dumpsys meminfo 的 TOTAL PSS，按 KB 除以 1024 转换为 MB。",
    "java_heap_mb": "读取 dumpsys meminfo 的 Java Heap，按 KB 除以 1024 转换为 MB。",
    "native_heap_mb": "读取 dumpsys meminfo 的 Native Heap，按 KB 除以 1024 转换为 MB。",
    "code_mb": "读取 dumpsys meminfo 的 Code 区域，按 KB 除以 1024 转换为 MB。",
    "graphics_mb": "读取 dumpsys meminfo 的 Graphics 区域，按 KB 除以 1024 转换为 MB。",
    "stack_mb": "读取 dumpsys meminfo 的 Stack 区域，按 KB 除以 1024 转换为 MB。",
    "battery_level_pct": "直接读取 dumpsys battery 的 level 字段，单位百分比。",
    "battery_status": "直接读取 dumpsys battery 的 status 字段，通常为 Android 状态码。",
    "battery_temperature_c": "读取电池温度原始值（通常为 0.1°C），除以 10 转换为 °C。",
    "battery_voltage_mv": "直接读取 dumpsys battery 的 voltage 字段，单位 mV。",
    "battery_current_ua": "读取 dumpsys battery 的 current now 字段，单位 μA；部分版本没有该字段。",
    "frames_total": "从 gfxinfo 读取 Total frames rendered 字段；通常是清理以来的累计值。",
    "janky_frames": "从 gfxinfo 读取 Janky frames 字段；通常是清理以来的累计值。",
    "jank_pct": "从 gfxinfo 读取 Janky frames 括号中的百分比。",
    "app_data_kb": "通过应用 dataDir 执行 du -sk，单位 KB。",
    "apk_kb": "通过 pm path 获取 APK 路径，对各 APK 执行 du -k 后求和，单位 KB。",
    "foreground_transition": "当前前台值与上一采样点不同才输出 foreground 或 background，否则为空。",
    "startup_status": "none 模式为 not_launched；cold/hot 模式观测到前台为 observed，否则为 not_observed。",
})

FIELD_LIMITATION_BY_FIELD = {
    "main_pid": "首个匹配 PID 是脚本的主进程近似，不保证等于应用清单定义的主进程。",
    "cpu_pct_all_cores": "首次采样没有前一时刻基线时为空；受进程短暂退出和 Android 权限影响。",
    "cpu_pct_one_core": "首次采样没有前一时刻基线时为空；核心数为设备逻辑 CPU 数的近似。",
    "main_thread_cpu_pct": "字段名沿用历史命名；当前不读取单独主线程 TID，不能用于严格 UI 主线程瓶颈判断。",
    "pss_mb": "dumpsys meminfo 格式和权限随 Android 版本变化；空值不等于 0。",
    "network_rx_Bps": "现代 Android 版本可能移除 xt_qtaguid，统计可能为空。",
    "network_tx_Bps": "现代 Android 版本可能移除 xt_qtaguid，统计可能为空。",
    "network_type": "设备网络上下文，不代表 App 的实际每连接网络类型。",
    "app_power_mah": "依赖 batterystats 权限和 Android 实现，精度低于专用功耗分析工具。",
    "max_thermal_temp_c": "热区名称未绑定到 CPU/GPU/App，不能归因到目标 App。",
    "frames_total": "gfxinfo 通常是累计值，可能受系统版本和清理时机影响。",
    "janky_frames": "gfxinfo 格式可能变化；累计值不等同于本采样区间新增卡顿帧。",
    "jank_pct": "gfxinfo 格式可能变化；无有效数据时留空，不能解释为 0% 卡顿。",
    "startup_observed_s": "按采样粒度近似，不等同于首帧、首屏或端到端启动耗时。",
    "crash_count": "基于 Logcat 关键字匹配，可能漏报或重复，不等同于系统完整崩溃统计。",
    "anr_count": "基于 Logcat 关键字匹配，可能漏报或重复，不等同于系统完整 ANR 统计。",
    "system_cpu_pct": "这是整机上下文，不是目标 App CPU；首次采样通常为空。",
    "battery_level_pct": "设备级电量，不是目标 App 电量。",
    "battery_temperature_c": "设备电池温度，不是目标 App 或 CPU 温度。",
}


def field_scope(field: str) -> str:
    if field in PUBLIC_SCOPE_FIELDS:
        return "公共/状态"
    if field in DEVICE_SCOPE_FIELDS:
        return "设备级"
    if field in RUN_STATE_FIELDS:
        return "应用运行状态"
    return "应用级"


def field_metric_type(field: str) -> str:
    if field in {"timestamp", "elapsed_s", "package", "pids", "main_pid", "network_uid", "device_serial", "device_manufacturer", "device_brand", "device_model", "android_version", "android_sdk", "build_id"}:
        return "标识/元数据"
    if field in PUBLIC_SCOPE_FIELDS or field in {"battery_status", "thermal_status", "startup_status"}:
        return "状态"
    if field in DEVICE_SCOPE_FIELDS:
        return "设备环境"
    if field in RUN_STATE_FIELDS:
        return "运行状态"
    if field in {"frames_total", "janky_frames", "jank_pct"}:
        return "图形/UI"
    if field in {"crash_count", "anr_count", "logcat_error_count"}:
        return "稳定性"
    if field in {"app_data_kb", "apk_kb", "app_storage_kb"}:
        return "存储"
    return "应用性能"


def field_calculation(field: str) -> str:
    return FIELD_CALCULATION_BY_FIELD.get(field, "按采样点从 Android 端读取或汇总；空值表示当前采样未读到有效值。")


def field_limitation(field: str) -> str:
    return FIELD_LIMITATION_BY_FIELD.get(field, "受 Android 版本、权限策略、进程状态或命令输出格式影响；空值不等于 0。")


def field_display_switch(field: str) -> str:
    return DISPLAY_SWITCH_BY_FIELD.get(field, "未配置")


LEGACY_MEMORY_FIELDS = {
    "rss_mb": "rss_kb",
    "pss_mb": "pss_kb",
    "java_heap_mb": "java_heap_kb",
    "native_heap_mb": "native_heap_kb",
    "code_mb": "code_kb",
    "graphics_mb": "graphics_kb",
    "stack_mb": "stack_kb",
    "system_mem_total_mb": "system_mem_total_kb",
    "system_mem_available_mb": "system_mem_available_kb",
}


def parse_number(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def normalize_csv_field_name(value: Optional[str]) -> str:
    """Accept English headers and the collector's Chinese-English headers."""
    text = (value or "").strip().lstrip("\ufeff")
    if text in FIELD_DEFINITION_MAP:
        return text
    match = re.match(r"^.*[（(]\s*([A-Za-z][A-Za-z0-9_]*)\s*[）)]$", text)
    if match and match.group(1) in FIELD_DEFINITION_MAP:
        return match.group(1)
    return text


def fmt_number(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    if abs(value - round(value)) < 1e-9 and digits > 0:
        digits = 0
    return f"{value:,.{digits}f}"


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时 {minutes}分 {secs}秒"
    if minutes:
        return f"{minutes}分 {secs}秒"
    return f"{secs}秒"


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV 没有表头")
        reader.fieldnames = [normalize_csv_field_name(name) for name in reader.fieldnames]
        required = {"timestamp", "elapsed_s", "package", "status"}
        missing = sorted(required - set(reader.fieldnames))
        if missing:
            raise ValueError("CSV 缺少字段: " + ", ".join(missing))
        return [dict(row) for row in reader]


def metric_values(rows: Iterable[Dict[str, str]], field: str) -> List[float]:
    values: List[float] = []
    for row in rows:
        status = (row.get("status") or "").strip()
        if status not in {"ok", "partial"}:
            continue
        value = metric_row_value(row, field)
        if value is not None:
            values.append(value)
    return values


def last_metric_value(rows: Sequence[Dict[str, str]], field: str) -> Optional[float]:
    ordered = sorted(
        [row for row in rows if (row.get("status") or "").strip() in {"ok", "partial"}],
        key=lambda row: parse_number(row.get("elapsed_s"))
        if parse_number(row.get("elapsed_s")) is not None
        else -1,
    )
    for row in reversed(ordered):
        value = metric_row_value(row, field)
        if value is not None:
            return value
    return None


def metric_row_value(row: Dict[str, str], field: str) -> Optional[float]:
    value = parse_number(row.get(field))
    if value is not None:
        return value
    legacy_field = LEGACY_MEMORY_FIELDS.get(field)
    legacy_value = parse_number(row.get(legacy_field)) if legacy_field else None
    return legacy_value / 1024 if legacy_value is not None else None


def collect_device_info(rows: Sequence[Dict[str, str]]) -> Dict[str, str]:
    info: Dict[str, str] = {}
    for field, _ in DEVICE_FIELDS:
        value = ""
        for row in rows:
            candidate = (row.get(field) or "").strip()
            if candidate:
                value = candidate
                break
        info[field] = value
    return info


def collect_flags(rows: Sequence[Dict[str, str]], field: str = "collection_flags") -> Dict[str, str]:
    """Read a collector switch snapshot stored on each CSV row."""
    raw = ""
    for row in rows:
        candidate = (row.get(field) or "").strip()
        if candidate:
            raw = candidate
            break
    flags: Dict[str, str] = {}
    for item in raw.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        flags[key.strip()] = value.strip()
    return flags


def summarize_metric(rows: Sequence[Dict[str, str]], field: str, unit: str) -> Dict[str, object]:
    values = metric_values(rows, field)
    return {
        "field": field,
        "unit": unit,
        "count": len(values),
        "missing": max(0, len(rows) - len(values)),
        "mean": statistics.fmean(values) if values else None,
        "p95": percentile(values, 0.95),
        "max": max(values) if values else None,
        "last": last_metric_value(rows, field),
    }


def downsample(points: Sequence[Tuple[float, float]], max_points: int = 240) -> List[Tuple[float, float]]:
    if len(points) <= max_points:
        return list(points)
    step = (len(points) - 1) / (max_points - 1)
    return [points[round(index * step)] for index in range(max_points)]


def svg_line_chart(
    title: str,
    rows: Sequence[Dict[str, str]],
    field: str,
    color: str,
    unit: str,
) -> str:
    raw: List[Tuple[float, float]] = []
    for index, row in enumerate(rows):
        x = parse_number(row.get("elapsed_s"))
        y = metric_row_value(row, field)
        if y is not None:
            raw.append((x if x is not None else float(index), y))
    points = downsample(raw)
    if len(points) < 2:
        return (
            '<div class="chart empty"><strong>'
            + html.escape(title)
            + "</strong><span>没有足够的有效数据绘制趋势图。</span></div>"
        )

    width, height = 960, 280
    left, right, top, bottom = 70, 24, 36, 48
    chart_width = width - left - right
    chart_height = height - top - bottom
    x_min, x_max = points[0][0], points[-1][0]
    if x_max <= x_min:
        x_max = x_min + 1
    y_max = max(value for _, value in points)
    y_min = 0.0
    if y_max <= 0:
        y_max = 1.0
    else:
        y_max *= 1.10

    def x_pos(x: float) -> float:
        return left + (x - x_min) / (x_max - x_min) * chart_width

    def y_pos(y: float) -> float:
        return top + (y_max - y) / y_max * chart_height

    polyline = " ".join(f"{x_pos(x):.1f},{y_pos(y):.1f}" for x, y in points)
    mid_value = y_max / 2
    grid = []
    for value in (0, mid_value, y_max):
        y = y_pos(value)
        grid.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" class="grid"/>')
        grid.append(f'<text x="{left-10}" y="{y+4:.1f}" text-anchor="end" class="axis">{html.escape(fmt_number(value))}</text>')
    labels = (
        f'<text x="{left}" y="{height-12}" class="axis">0s</text>'
        f'<text x="{width-right}" y="{height-12}" text-anchor="end" class="axis">{html.escape(fmt_duration(x_max))}</text>'
    )
    return f"""
    <div class="chart">
      <div class="chart-title">{html.escape(title)}</div>
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">
        <title>{html.escape(title)}</title>
        {''.join(grid)}
        <line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" class="axis-line"/>
        <line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" class="axis-line"/>
        <polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>
        {labels}
        <text x="{width-right}" y="20" text-anchor="end" class="unit">{html.escape(unit)}</text>
      </svg>
    </div>
    """


def summary_csv(path: Path, summaries: Sequence[Dict[str, object]]) -> None:
    fields = ["field", "chinese_name", "unit", "description", "count", "missing", "mean", "p95", "max", "last"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for summary in summaries:
            row = dict(summary)
            definition = FIELD_DEFINITION_MAP.get(str(row.get("field")), ("", "", "", ""))
            row["chinese_name"] = definition[0]
            row["description"] = definition[2]
            for key in ("mean", "p95", "max", "last"):
                if isinstance(row.get(key), float):
                    row[key] = round(row[key], 2)
            writer.writerow(row)


def write_field_dictionary(path: Path) -> None:
    fields = [
        "field", "chinese_name", "data_scope", "metric_type", "unit",
        "description", "calculation", "source", "limitations", "display_switch",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for field, chinese_name, unit, description, source in FIELD_DEFINITIONS:
            writer.writerow({
                "field": field,
                "chinese_name": chinese_name,
                "data_scope": field_scope(field),
                "metric_type": field_metric_type(field),
                "unit": unit,
                "description": description,
                "calculation": field_calculation(field),
                "source": source,
                "limitations": field_limitation(field),
                "display_switch": field_display_switch(field),
            })


def build_report(input_path: Path, output_dir: Path, package_filter: Optional[str] = None) -> Dict[str, object]:
    all_rows = read_rows(input_path)
    rows = [row for row in all_rows if (row.get("package") or "").strip() == package_filter] if package_filter is not None else all_rows
    if not rows:
        if package_filter:
            raise ValueError(f"CSV 没有包名 {package_filter!r} 的数据行")
        raise ValueError("CSV 没有数据行")

    package_counts = Counter((row.get("package") or "").strip() for row in rows)
    package = package_counts.most_common(1)[0][0] or "未知包名"
    status_counts = Counter((row.get("status") or "unknown").strip() or "unknown" for row in rows)
    elapsed_values = [value for value in (parse_number(row.get("elapsed_s")) for row in rows) if value is not None]
    duration = max(elapsed_values) if elapsed_values else None
    interval_values = [
        elapsed_values[index] - elapsed_values[index - 1]
        for index in range(1, len(elapsed_values))
        if elapsed_values[index] > elapsed_values[index - 1]
    ]
    interval = statistics.median(interval_values) if interval_values else None
    summaries = [summarize_metric(rows, field, unit) for field, _, unit in METRICS]
    summary_by_field = {item["field"]: item for item in summaries}
    device_info = collect_device_info(rows)
    collection_flags = collect_flags(rows)
    display_flags = collect_flags(rows, "display_flags")

    total = len(rows)
    ok_count = status_counts.get("ok", 0)
    valid_ratio = ok_count / total if total else 0
    latest_jank = summary_by_field["jank_pct"]["last"]
    cpu = summary_by_field["cpu_pct_one_core"]
    pss = summary_by_field["pss_mb"]
    no_process = status_counts.get("no_process", 0)
    partial = status_counts.get("partial", 0)
    adb_error = sum(count for status, count in status_counts.items() if status.startswith("adb_error"))

    generated_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    metadata = {
        "tool_version": APP_VERSION,
        "input_file": input_path.name,
        "generated_at": generated_at,
        "package": package,
        "package_filter": package_filter,
        "rows": total,
        "duration_s": duration,
        "median_interval_s": interval,
        "status_counts": dict(status_counts),
        "device_info": device_info,
        "collection_flags": collection_flags,
        "display_flags": display_flags,
        "metrics": summaries,
        "metrics_by_field": summary_by_field,
    }

    report_title = f"{package} 性能监控报告 v{APP_VERSION}"
    executive_points = [
        f"本次记录 {total:,} 个采样点，覆盖约 {fmt_duration(duration)}；中位采样间隔为 {fmt_number(interval)} 秒。",
        f"单核标准 CPU 平均 {fmt_number(cpu['mean'])}%、峰值 {fmt_number(cpu['max'])}%；PSS 内存峰值 {fmt_number(pss['max'])} MB。",
        f"数据完整采样占比 {valid_ratio:.1%}；partial {partial:,} 条，no_process {no_process:,} 条，ADB 错误 {adb_error:,} 条。",
    ]
    if latest_jank is not None:
        executive_points.append(f"最后一个有效采样点的卡顿比例为 {fmt_number(latest_jank)}%。")
    else:
        executive_points.append("本次没有读取到有效的 gfxinfo 卡顿比例，不能据此判断 UI 流畅度。")

    metric_rows = []
    for field, label, unit in METRICS:
        item = summary_by_field[field]
        metric_rows.append(
            "<tr>"
            f"<td>{html.escape(label)}</td><td><code>{html.escape(field)}</code></td>"
            f"<td>{html.escape(unit)}</td><td>{html.escape(FIELD_DEFINITION_MAP[field][2])}</td>"
            f"<td>{item['count']:,}</td><td>{item['missing']:,}</td>"
            f"<td>{html.escape(fmt_number(item['mean']))}</td><td>{html.escape(fmt_number(item['p95']))}</td>"
            f"<td>{html.escape(fmt_number(item['max']))}</td><td>{html.escape(fmt_number(item['last']))}</td>"
            "</tr>"
        )

    status_rows = "".join(
        f"<tr><td><code>{html.escape(status)}</code></td><td>{count:,}</td><td>{count/total:.1%}</td></tr>"
        for status, count in sorted(status_counts.items())
    )
    device_rows = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(device_info.get(field) or '未采集')}</td></tr>"
        for field, label in DEVICE_FIELDS
    )
    if collection_flags:
        collection_rows = "".join(
            f"<tr><th>{html.escape(label)}</th><td>{'开启' if collection_flags.get(key) == '1' else '关闭'}</td></tr>"
            for key, label in COLLECTION_FLAG_LABELS.items()
        )
        collection_note = "检测到旧格式 CSV 中的采集开关快照；当前版本请以“采集配置.conf”为准。"
    else:
        collection_rows = '<tr><td colspan="2">CSV 不写入采集开关；请以本次使用的“采集配置.conf”文件为准。</td></tr>'
        collection_note = "采集和显示开关只在“采集配置.conf”中配置，CSV 不再附带开关列。"
    if display_flags:
        display_rows = "".join(
            f"<tr><th>{html.escape(label)}</th><td>{'显示' if display_flags.get(key) == '1' else '隐藏'}</td></tr>"
            for key, label in COLLECTION_FLAG_LABELS.items()
        )
    else:
        display_rows = '<tr><td colspan="2">CSV 不写入显示开关；请以本次使用的“采集配置.conf”文件为准。</td></tr>'

    chart_cpu = svg_line_chart("CPU 使用率趋势（单核标准）", rows, "cpu_pct_one_core", "#2563eb", "%")
    chart_pss = svg_line_chart("PSS 内存趋势", rows, "pss_mb", "#b45309", "MB")
    chart_jank = svg_line_chart("卡顿比例趋势", rows, "jank_pct", "#be185d", "%")
    chart_network_rx = svg_line_chart("网络接收速率趋势", rows, "network_rx_Bps", "#0f766e", "B/s")
    chart_network_tx = svg_line_chart("网络发送速率趋势", rows, "network_tx_Bps", "#7c3aed", "B/s")
    chart_battery = svg_line_chart("电池电量趋势", rows, "battery_level_pct", "#ca8a04", "%")
    chart_temperature = svg_line_chart("热区温度趋势", rows, "max_thermal_temp_c", "#dc2626", "°C")

    safe_title = html.escape(report_title)
    points_html = "".join(f"<li>{html.escape(point)}</li>" for point in executive_points)
    source_name = html.escape(input_path.name)
    report_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: light dark; --bg:#f7f8fa; --surface:#fff; --ink:#18212f; --muted:#687386; --line:#dfe4eb; --accent:#2563eb; --code:#eef2f7; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg:#101419; --surface:#171d24; --ink:#edf2f7; --muted:#a4afbd; --line:#303a46; --code:#222b36; }} }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; }}
    main {{ max-width:1180px; margin:0 auto; padding:36px 24px 64px; }}
    h1 {{ margin:0 0 8px; font-size:30px; letter-spacing:-.02em; }}
    h2 {{ margin:38px 0 12px; font-size:21px; border-bottom:1px solid var(--line); padding-bottom:8px; }}
    h3 {{ margin:26px 0 8px; font-size:17px; }}
    p {{ margin:8px 0 14px; }}
    .subtitle,.muted {{ color:var(--muted); }}
    .summary {{ background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:20px 24px; box-shadow:0 4px 18px rgba(31,41,55,.05); }}
    .device-table {{ max-width:760px; }}
    .device-table th {{ width:190px; }}
    .toc {{ display:flex; flex-wrap:wrap; gap:8px; margin:18px 0 24px; padding:12px; background:var(--surface); border:1px solid var(--line); border-radius:12px; }}
    .toc-title {{ width:100%; color:var(--muted); font-size:13px; font-weight:650; }}
    .toc a {{ color:var(--accent); text-decoration:none; border:1px solid var(--line); border-radius:999px; padding:4px 11px; background:var(--bg); }}
    .toc a:hover,.toc a:focus {{ text-decoration:underline; outline:2px solid color-mix(in srgb, var(--accent) 35%, transparent); outline-offset:2px; }}
    .cards {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:18px 0; }}
    .card {{ background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:15px; }}
    .card-label {{ color:var(--muted); font-size:13px; }}
    .card-value {{ font-size:22px; font-weight:700; margin-top:4px; }}
    ul {{ padding-left:22px; }}
    table {{ border-collapse:collapse; width:100%; background:var(--surface); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
    th,td {{ border-bottom:1px solid var(--line); padding:9px 10px; text-align:left; white-space:nowrap; }}
    th {{ color:var(--muted); font-weight:600; background:color-mix(in srgb, var(--surface) 84%, var(--line)); }}
    tr:last-child td {{ border-bottom:0; }}
    code {{ background:var(--code); padding:2px 5px; border-radius:4px; font-size:.9em; }}
    .table-wrap {{ overflow-x:auto; }}
    .chart {{ background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:14px 14px 6px; margin:14px 0; }}
    .chart-title {{ font-weight:650; margin:0 0 2px 4px; }}
    .chart svg {{ display:block; width:100%; height:auto; }}
    .grid {{ stroke:var(--line); stroke-width:1; stroke-dasharray:3 4; }}
    .axis-line {{ stroke:var(--muted); stroke-width:1; }}
    .axis,.unit {{ fill:var(--muted); font-size:12px; }}
    .empty {{ min-height:120px; display:flex; flex-direction:column; justify-content:center; gap:5px; color:var(--muted); }}
    .note {{ border-left:4px solid var(--accent); padding:10px 14px; background:color-mix(in srgb, var(--accent) 8%, var(--surface)); }}
    footer {{ margin-top:40px; color:var(--muted); font-size:13px; }}
    @media(max-width:760px) {{ main {{ padding:22px 14px 42px; }} .cards {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} h1 {{ font-size:25px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>{safe_title}</h1>
    <div class="subtitle">工具版本：v{APP_VERSION} · 目标包名：<code>{html.escape(package)}</code> · 数据文件：<code>{source_name}</code> · 生成时间：{html.escape(generated_at)}</div>
  </header>

  <nav class="toc" aria-label="报告中文菜单">
    <div class="toc-title">报告菜单</div>
    <a href="#technical-summary">技术摘要</a>
    <a href="#device-info">设备信息</a>
    <a href="#collection-config">采集开关</a>
    <a href="#headline-metrics">关键指标</a>
    <a href="#visual-evidence">趋势证据</a>
    <a href="#metric-definitions">指标统计</a>
    <a href="#status-quality">数据质量</a>
    <a href="#methodology">采集方法</a>
    <a href="#limitations">限制说明</a>
    <a href="#next-steps">建议下一步</a>
    <a href="#open-questions">待确认问题</a>
  </nav>

  <section class="summary" aria-labelledby="technical-summary">
    <h2 id="technical-summary">技术摘要</h2>
    <ul>{points_html}</ul>
  </section>

  <section aria-labelledby="device-info">
    <h2 id="device-info">设备信息</h2>
    <p>以下信息由 Android 设备采集开始时的系统属性提供，并随 CSV 一起保存。</p>
    <div class="table-wrap"><table class="device-table">
      <tbody>{device_rows}</tbody>
    </table></div>
  </section>

  <section aria-labelledby="collection-config">
    <h2 id="collection-config">采集与 CSV 显示开关</h2>
    <p>{collection_note}关闭项目的对应字段会留空，不会按 0 参与统计。</p>
    <div class="table-wrap"><table class="device-table"><tbody>{collection_rows}</tbody></table></div>
    <h3>CSV 显示列</h3>
    <p>显示开关只决定 CSV 是否输出对应列，不会改变采集动作。</p>
    <div class="table-wrap"><table class="device-table"><tbody>{display_rows}</tbody></table></div>
  </section>

  <section aria-labelledby="headline-metrics">
    <h2 id="headline-metrics">关键指标</h2>
    <div class="cards">
      <div class="card"><div class="card-label">采样点</div><div class="card-value">{total:,}</div></div>
      <div class="card"><div class="card-label">实际覆盖时长</div><div class="card-value">{html.escape(fmt_duration(duration))}</div></div>
      <div class="card"><div class="card-label">CPU 峰值（单核标准）</div><div class="card-value">{html.escape(fmt_number(cpu['max']))}%</div></div>
      <div class="card"><div class="card-label">PSS 峰值</div><div class="card-value">{html.escape(fmt_number(pss['max']))} MB</div></div>
    </div>
  </section>

  <section aria-labelledby="visual-evidence">
    <h2 id="visual-evidence">关键发现与趋势证据</h2>
    <p>以下趋势图按采样时间排序；长时间数据会均匀降采样到最多 240 个点，只影响图形密度，不改变原始 CSV。</p>
    {chart_cpu}
    <p class="muted">CPU 图用于观察应用负载是否持续升高或出现尖峰；CPU 首个采样点为空属于正常情况，因为 CPU 占用率需要前后两个采样点计算。</p>
    {chart_pss}
    <p class="muted">PSS 图用于观察应用内存水平和持续增长趋势；部分设备可能因权限限制无法读取 PSS。</p>
    {chart_jank}
    <p class="muted">卡顿图依赖设备的 <code>dumpsys gfxinfo</code> 输出。没有有效数据时，只能判定“未采集到”，不能判定为无卡顿。</p>
    <h3>网络、电量与温度</h3>
    <p>以下扩展趋势用于观察网络吞吐、电池电量和热区温度变化；应用网络流量依赖设备是否暴露 UID 级网络统计。</p>
    {chart_network_rx}
    {chart_network_tx}
    {chart_battery}
    {chart_temperature}
  </section>

  <section aria-labelledby="metric-definitions">
    <h2 id="metric-definitions">指标定义与统计值</h2>
    <p>平均值、P95、最大值和最后有效值均基于当前 CSV 的非空数值；缺失值单独统计，不作为 0 参与计算。</p>
    <div class="table-wrap"><table>
      <thead><tr><th>指标</th><th>CSV 字段</th><th>单位</th><th>中文说明</th><th>有效样本</th><th>缺失样本</th><th>平均</th><th>P95</th><th>最大</th><th>最后有效</th></tr></thead>
      <tbody>{''.join(metric_rows)}</tbody>
    </table></div>
  </section>

  <section aria-labelledby="status-quality">
    <h2 id="status-quality">数据质量与运行状态</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>状态</th><th>样本数</th><th>占比</th></tr></thead>
      <tbody>{status_rows}</tbody>
    </table></div>
    <div class="note"><strong>解释：</strong> <code>partial</code> 表示进程存在但部分指标不可读；<code>no_process</code> 表示采样时未发现目标应用进程；ADB 错误会按 <code>adb_error...</code> 前缀单独计数。</div>
  </section>

  <section aria-labelledby="methodology">
    <h2 id="methodology">采集方法</h2>
    <ul>
      <li>进程范围：目标包名及其以 <code>包名:</code> 开头的子进程。</li>
      <li>CPU：通过进程 CPU ticks 与系统总 CPU ticks 的相邻采样差计算。</li>
      <li>内存：RSS 来自进程 status，PSS 来自 <code>dumpsys meminfo</code>。</li>
      <li>磁盘 I/O：读取进程 <code>/proc/&lt;pid&gt;/io</code> 的累计值，并计算相邻采样速率。</li>
      <li>UI：读取 <code>dumpsys gfxinfo</code> 的累计帧和卡顿帧字段。</li>
      <li>网络：按应用 UID 读取 <code>/proc/net/xt_qtaguid/stats</code>；新系统或权限限制可能无法提供。</li>
      <li>启动：默认只记录首次发现进程/前台的时间；使用 <code>--launch cold</code> 或 <code>--launch hot</code> 才会主动启动应用并记录启动观测值。</li>
      <li>崩溃/ANR/日志：以采集开始时的 Logcat 基线为准计算新增匹配行，不是系统历史全量诊断。</li>
      <li>采集范围：由目录中的 <code>采集配置.conf</code> 控制；关闭项目的 CSV 字段留空，开关状态不写入 CSV。</li>
    </ul>
  </section>

  <section aria-labelledby="limitations">
    <h2 id="limitations">限制与稳健性说明</h2>
    <ul>
      <li>该报告是描述性统计，不等同于性能根因诊断，也不能单独证明卡顿原因。</li>
      <li>Android 厂商版本和权限策略会影响 <code>/proc</code>、PSS、I/O、gfxinfo 的可读性。</li>
      <li>如果应用在监控期间退出，报告会保留 <code>no_process</code>，不能把这段时间的指标推断为 0。</li>
      <li>趋势图是可读性视图；原始数据仍以输入 CSV 为准。</li>
    </ul>
  </section>

  <section aria-labelledby="next-steps">
    <h2 id="next-steps">建议下一步</h2>
    <ol>
      <li>先检查 <code>partial</code>、<code>no_process</code> 和 ADB 错误比例，再解释 CPU、内存或卡顿峰值。</li>
      <li>对异常时间点结合应用日志、系统日志或 Perfetto trace 做根因分析。</li>
      <li>对比正常设备、正常网络或不同版本应用的报告，避免仅凭单次采集下结论。</li>
    </ol>
  </section>

  <section aria-labelledby="open-questions">
    <h2 id="open-questions">待确认问题</h2>
    <ul>
      <li>目标设备是否允许读取 PSS、进程 I/O 和 gfxinfo？</li>
      <li>卡顿阈值按项目标准还是按 Android 默认 gfxinfo 口径判断？</li>
      <li>是否需要增加网络流量、电量消耗和 Perfetto 深度追踪？</li>
    </ul>
  </section>

  <footer>本报告由本地脚本离线生成，原始数据文件：{source_name}。</footer>
</main>
</body>
</html>
"""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.html").write_text(report_html, encoding="utf-8")
    summary_csv(output_dir / "summary.csv", summaries)
    (output_dir / "summary.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "device_info.json").write_text(json.dumps(device_info, ensure_ascii=False, indent=2), encoding="utf-8")
    write_field_dictionary(output_dir / "字段说明.csv")
    (output_dir / "run_config.json").write_text(
        json.dumps(
            {
                "input_file": str(input_path),
                "output_dir": str(output_dir),
                "generated_at": generated_at,
                "generator": "generate_perf_report.py",
                "tool_version": APP_VERSION,
                "collection_flags": collection_flags,
                "display_flags": display_flags,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return metadata


def safe_path_component(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ".-_" else "_" for char in value)
    return safe or "unknown_package"


def write_multi_package_summary(path: Path, entries: Sequence[Tuple[str, Dict[str, object], str]]) -> None:
    fields = ["package", "rows", "duration_s", "median_interval_s", "ok", "partial", "no_process", "max_cpu_pct", "max_pss_mb", "max_jank_pct", "max_crash_count", "max_anr_count", "report"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for package, metadata, report_path in entries:
            statuses = metadata.get("status_counts", {})
            writer.writerow({
                "package": package,
                "rows": metadata.get("rows", 0),
                "duration_s": metadata.get("duration_s") or "",
                "median_interval_s": metadata.get("median_interval_s") or "",
                "ok": statuses.get("ok", 0),
                "partial": statuses.get("partial", 0),
                "no_process": statuses.get("no_process", 0),
                "max_cpu_pct": metadata.get("metrics_by_field", {}).get("cpu_pct_one_core", {}).get("max", ""),
                "max_pss_mb": metadata.get("metrics_by_field", {}).get("pss_mb", {}).get("max", ""),
                "max_jank_pct": metadata.get("metrics_by_field", {}).get("jank_pct", {}).get("max", ""),
                "max_crash_count": metadata.get("metrics_by_field", {}).get("crash_count", {}).get("max", ""),
                "max_anr_count": metadata.get("metrics_by_field", {}).get("anr_count", {}).get("max", ""),
                "report": report_path,
            })


def write_multi_package_index(
    input_path: Path,
    output_dir: Path,
    entries: Sequence[Tuple[str, Dict[str, object], str]],
) -> None:
    generated_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    package_rows = []
    for package, metadata, report_path in entries:
        statuses = metadata.get("status_counts", {})
        metrics = metadata.get("metrics_by_field", {})
        package_rows.append(
            "<tr>"
            f"<td><code>{html.escape(package)}</code></td>"
            f"<td>{int(metadata.get('rows') or 0):,}</td>"
            f"<td>{html.escape(fmt_duration(parse_number(metadata.get('duration_s'))))}</td>"
            f"<td>{int(statuses.get('ok', 0)):,}</td>"
            f"<td>{int(statuses.get('partial', 0)):,}</td>"
            f"<td>{int(statuses.get('no_process', 0)):,}</td>"
            f"<td>{html.escape(fmt_number(metrics.get('cpu_pct_one_core', {}).get('max')))}%</td>"
            f"<td>{html.escape(fmt_number(metrics.get('pss_mb', {}).get('max')))} MB</td>"
            f"<td>{html.escape(fmt_number(metrics.get('jank_pct', {}).get('max')))}%</td>"
            f'<td><a href="{html.escape(report_path)}">打开中文报告</a></td>'
            "</tr>"
        )
    table = "".join(package_rows)
    report_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>多应用性能监控报告 v{APP_VERSION}</title>
  <style>
    :root {{ color-scheme: light dark; --bg:#f7f8fa; --surface:#fff; --ink:#18212f; --muted:#687386; --line:#dfe4eb; --accent:#2563eb; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg:#101419; --surface:#171d24; --ink:#edf2f7; --muted:#a4afbd; --line:#303a46; }} }}
    * {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; }}
    main {{ max-width:1100px; margin:0 auto; padding:36px 24px 64px; }} h1 {{ margin:0 0 8px; font-size:30px; }} h2 {{ margin:34px 0 12px; font-size:21px; border-bottom:1px solid var(--line); padding-bottom:8px; }}
    .subtitle,.muted {{ color:var(--muted); }} .summary {{ background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:20px 24px; margin:22px 0; }}
    .toc {{ display:flex; gap:8px; margin:18px 0 24px; padding:12px; background:var(--surface); border:1px solid var(--line); border-radius:12px; }} .toc a {{ color:var(--accent); text-decoration:none; }}
    table {{ border-collapse:collapse; width:100%; background:var(--surface); border:1px solid var(--line); }} th,td {{ border-bottom:1px solid var(--line); padding:9px 10px; text-align:left; white-space:nowrap; }} th {{ color:var(--muted); }}
    .table-wrap {{ overflow-x:auto; }} code {{ background:color-mix(in srgb, var(--line) 35%, transparent); padding:2px 5px; border-radius:4px; }} a {{ color:var(--accent); }} footer {{ margin-top:36px; color:var(--muted); font-size:13px; }}
  </style>
</head>
<body><main>
  <h1>多应用性能监控报告 v{APP_VERSION}</h1>
  <div class="subtitle">工具版本：v{APP_VERSION} · 数据文件：<code>{html.escape(input_path.name)}</code> · 应用数量：{len(entries)} · 生成时间：{html.escape(generated_at)}</div>
  <nav class="toc" aria-label="报告中文菜单"><a href="#summary">监控概览</a><a href="#packages">应用报告</a><a href="#notes">说明</a></nav>
  <section id="summary" class="summary"><h2>监控概览</h2><p>本次采集包含 {len(entries)} 个应用。每个应用均已单独计算 CPU、内存、磁盘 I/O、卡顿和运行状态，避免跨应用混合统计。</p></section>
  <section id="packages"><h2>应用报告</h2><div class="table-wrap"><table><thead><tr><th>包名</th><th>采样点</th><th>覆盖时长</th><th>正常</th><th>部分数据</th><th>未运行</th><th>CPU 峰值</th><th>PSS 峰值</th><th>卡顿峰值</th><th>报告</th></tr></thead><tbody>{table}</tbody></table></div></section>
  <section id="notes"><h2>说明</h2><p class="muted">每个应用的完整中文报告位于 <code>packages</code> 子目录；当前页面是汇总入口。原始 CSV 字段说明位于本目录的 <code>字段说明.csv</code>。</p></section>
  <footer>本报告由 Android App Performance Monitor v{APP_VERSION} 本地脚本离线生成。</footer>
</main></body></html>
"""
    (output_dir / "report.html").write_text(report_html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="从 Android 应用性能 CSV 生成中文 HTML 报告")
    parser.add_argument("--input", required=True, help="采集 CSV 文件路径")
    parser.add_argument("--output", default=None, help="报告输出目录；默认使用 <csv>_report")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        parser.error(f"找不到输入 CSV：{input_path}")
    output_dir = Path(args.output).expanduser() if args.output else input_path.with_name(input_path.stem + "_report")
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    output_dir = output_dir.resolve()
    try:
        all_rows = read_rows(input_path)
        packages = sorted({(row.get("package") or "").strip() for row in all_rows if (row.get("package") or "").strip()})
        if len(packages) <= 1:
            metadata = build_report(input_path, output_dir)
            print(f"报告已生成：{output_dir / 'report.html'}")
            print(f"汇总 CSV 已生成：{output_dir / 'summary.csv'}")
            print(f"字段说明已生成：{output_dir / '字段说明.csv'}")
            print(f"采样行数：{metadata['rows']}；包名：{metadata['package']}；时长：{fmt_duration(metadata['duration_s'])}")
            return 0

        output_dir.mkdir(parents=True, exist_ok=True)
        package_entries = []
        for package in packages:
            package_dir_name = safe_path_component(package)
            package_dir = output_dir / "packages" / package_dir_name
            metadata = build_report(input_path, package_dir, package)
            package_entries.append((package, metadata, f"packages/{package_dir_name}/report.html"))
        write_multi_package_index(input_path, output_dir, package_entries)
        write_multi_package_summary(output_dir / "package_summary.csv", package_entries)
        write_field_dictionary(output_dir / "字段说明.csv")
        device_info = package_entries[0][1].get("device_info", {})
        (output_dir / "device_info.json").write_text(json.dumps(device_info, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "summary.json").write_text(
            json.dumps({
                "tool_version": APP_VERSION,
                "input_file": input_path.name,
                "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "package_count": len(package_entries),
                "packages": [metadata for _, metadata, _ in package_entries],
                "device_info": device_info,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "run_config.json").write_text(
            json.dumps({
                "input_file": str(input_path),
                "output_dir": str(output_dir),
                "package_count": len(package_entries),
                "generator": "generate_perf_report.py",
                "tool_version": APP_VERSION,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"多应用汇总报告已生成：{output_dir / 'report.html'}")
        print(f"应用数量：{len(package_entries)}；应用明细目录：{output_dir / 'packages'}")
        print(f"应用汇总 CSV 已生成：{output_dir / 'package_summary.csv'}")
        print(f"字段说明已生成：{output_dir / '字段说明.csv'}")
        return 0
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

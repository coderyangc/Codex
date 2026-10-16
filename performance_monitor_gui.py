"""Android 应用性能采集 GUI。

这是完整采集工具，不是环境验证器。它直接调用 Windows 上已有的 adb，
把设备端 shell 采集结果转换为 CSV、XLSX、HTML 或 JSON，并写入按日期切割的日志。
"""

from __future__ import annotations

import csv
import html
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Callable
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from xlsx_writer import write_workbook


APP_VERSION = "2.0.9"
APP_TITLE = f"Android 应用性能采集工具 v{APP_VERSION}"
DURATION_RE = re.compile(r"^(?:\d+(?:\.\d+)?)(?:s|m|h)?$", re.IGNORECASE)
PACKAGE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
NUMERIC_FIELDS = {
    "elapsed_s", "process_count", "main_pid", "thread_count",
    "cpu_pct_all_cores", "cpu_pct_one_core", "main_thread_cpu_pct",
    "rss_mb", "pss_mb", "java_heap_mb", "native_heap_mb", "code_mb",
    "graphics_mb", "stack_mb", "read_bps", "write_bps", "io_read_bytes",
    "io_write_bytes", "uid", "rx_bps", "tx_bps", "rx_bytes", "tx_bytes",
    "battery_level_pct", "battery_temperature_c", "battery_voltage_mv",
    "battery_current_ua", "app_power_mah", "max_thermal_temp_c",
    "frames_total", "janky_frames", "jank_pct", "app_data_kb", "apk_kb",
    "app_storage_kb", "first_process_seen_s", "first_foreground_seen_s",
    "startup_observed_s", "crash_count", "anr_count", "logcat_error_count",
    "system_cpu_pct", "system_load_1m", "system_mem_total_mb",
    "system_mem_available_mb", "system_storage_free_kb",
}


# 配置文件中的所有可操作开关。采集开关控制设备端是否执行该类采集；
# CSV 显示开关控制对应列是否写入报告。这样既保留了配置文件的兼容性，
# 又可以让不熟悉配置文件的用户直接在界面中勾选。
PROJECT_GROUPS = [
    {
        "key": "process", "title": "进程信息", "description": "进程数量、PID、主进程和线程数",
        "collect_key": "COLLECT_PROCESS", "display_key": "DISPLAY_PROCESS",
        "items": [
            ("DISPLAY_PROCESS_COUNT", "进程数量"), ("DISPLAY_PROCESS_PIDS", "进程 PID"),
            ("DISPLAY_PROCESS_MAIN_PID", "主进程 PID"), ("DISPLAY_PROCESS_THREAD_COUNT", "线程数量"),
        ],
    },
    {
        "key": "cpu", "title": "CPU", "description": "全核、单核标准和主线程 CPU 占用率",
        "collect_key": "COLLECT_CPU", "display_key": "DISPLAY_CPU",
        "items": [
            ("DISPLAY_CPU_PCT_ALL_CORES", "全核 CPU 占用率（%）"),
            ("DISPLAY_CPU_PCT_ONE_CORE", "单核标准 CPU 占用率（%）"),
            ("DISPLAY_MAIN_THREAD_CPU_PCT", "主线程 CPU 占用率（%）"),
        ],
    },
    {
        "key": "memory", "title": "内存", "description": "RSS、PSS 和 Java/Native/图形内存，单位 MB",
        "collect_key": "COLLECT_MEMORY", "display_key": "DISPLAY_MEMORY",
        "items": [
            ("DISPLAY_MEMORY_RSS", "RSS 内存（MB）"), ("DISPLAY_MEMORY_PSS", "PSS 内存（MB）"),
            ("DISPLAY_MEMORY_JAVA_HEAP", "Java Heap（MB）"), ("DISPLAY_MEMORY_NATIVE_HEAP", "Native Heap（MB）"),
            ("DISPLAY_MEMORY_CODE", "Code 内存（MB）"), ("DISPLAY_MEMORY_GRAPHICS", "Graphics 内存（MB）"),
            ("DISPLAY_MEMORY_STACK", "Stack 内存（MB）"),
        ],
    },
    {
        "key": "disk", "title": "磁盘 I/O", "description": "应用读写速率和累计读写字节数",
        "collect_key": "COLLECT_DISK_IO", "display_key": "DISPLAY_DISK_IO",
        "items": [
            ("DISPLAY_DISK_READ_BPS", "读取速率（B/s）"), ("DISPLAY_DISK_WRITE_BPS", "写入速率（B/s）"),
            ("DISPLAY_IO_READ_BYTES", "累计读取字节"), ("DISPLAY_IO_WRITE_BYTES", "累计写入字节"),
        ],
    },
    {
        "key": "network", "title": "网络", "description": "应用 UID、收发速率、累计流量和网络类型",
        "collect_key": "COLLECT_NETWORK", "display_key": "DISPLAY_NETWORK",
        "items": [
            ("DISPLAY_NETWORK_UID", "应用 UID"), ("DISPLAY_NETWORK_RX_BPS", "接收速率（B/s）"),
            ("DISPLAY_NETWORK_TX_BPS", "发送速率（B/s）"), ("DISPLAY_NETWORK_RX_BYTES", "累计接收字节"),
            ("DISPLAY_NETWORK_TX_BYTES", "累计发送字节"), ("DISPLAY_NETWORK_TYPE", "网络类型"),
        ],
    },
    {
        "key": "battery", "title": "电量", "description": "电池状态、电压、电流和应用估算耗电",
        "collect_key": "COLLECT_BATTERY", "display_key": "DISPLAY_BATTERY",
        "items": [
            ("DISPLAY_BATTERY_LEVEL_PCT", "电量（%）"), ("DISPLAY_BATTERY_STATUS", "充电状态"),
            ("DISPLAY_BATTERY_TEMPERATURE_C", "电池温度（℃）"), ("DISPLAY_BATTERY_VOLTAGE_MV", "电压（mV）"),
            ("DISPLAY_BATTERY_CURRENT_UA", "电流（μA）"), ("DISPLAY_APP_POWER_MAH", "应用耗电（mAh）"),
        ],
    },
    {
        "key": "thermal", "title": "温度 / 热限制", "description": "最高热区温度和系统热限制状态",
        "collect_key": "COLLECT_THERMAL", "display_key": "DISPLAY_THERMAL",
        "items": [
            ("DISPLAY_MAX_THERMAL_TEMP_C", "最高热区温度（℃）"), ("DISPLAY_THERMAL_STATUS", "热限制状态"),
        ],
    },
    {
        "key": "ui", "title": "图形 / UI", "description": "渲染帧、卡顿帧和卡顿比例",
        "collect_key": "COLLECT_UI", "display_key": "DISPLAY_UI",
        "items": [
            ("DISPLAY_FRAMES_TOTAL", "总渲染帧数"), ("DISPLAY_JANKY_FRAMES", "卡顿帧数"),
            ("DISPLAY_JANK_PCT", "卡顿比例（%）"),
        ],
    },
    {
        "key": "storage", "title": "应用存储", "description": "应用数据、APK 和应用总占用",
        "collect_key": "COLLECT_STORAGE", "display_key": "DISPLAY_STORAGE",
        "items": [
            ("DISPLAY_APP_DATA_KB", "应用数据（KB）"), ("DISPLAY_APK_KB", "APK 大小（KB）"),
            ("DISPLAY_APP_STORAGE_KB", "应用总占用（KB）"),
        ],
    },
    {
        "key": "startup", "title": "启动 / 前后台", "description": "前后台状态、切换和启动观测时间",
        "collect_key": "COLLECT_STARTUP", "display_key": "DISPLAY_STARTUP",
        "items": [
            ("DISPLAY_FOREGROUND", "当前前台状态"), ("DISPLAY_FOREGROUND_TRANSITION", "前后台切换"),
            ("DISPLAY_FIRST_PROCESS_SEEN_S", "首次发现进程（秒）"),
            ("DISPLAY_FIRST_FOREGROUND_SEEN_S", "首次进入前台（秒）"),
            ("DISPLAY_STARTUP_OBSERVED_S", "启动观测时间（秒）"), ("DISPLAY_STARTUP_STATUS", "启动状态"),
        ],
    },
    {
        "key": "stability", "title": "稳定性", "description": "崩溃、ANR 和 Logcat 错误计数",
        "collect_key": "COLLECT_STABILITY", "display_key": "DISPLAY_STABILITY",
        "items": [
            ("DISPLAY_CRASH_COUNT", "崩溃次数"), ("DISPLAY_ANR_COUNT", "ANR 次数"),
            ("DISPLAY_LOGCAT_ERROR_COUNT", "Logcat 错误次数"),
        ],
    },
    {
        "key": "system", "title": "系统资源", "description": "整机 CPU、负载、内存和可用存储",
        "collect_key": "COLLECT_SYSTEM", "display_key": "DISPLAY_SYSTEM",
        "items": [
            ("DISPLAY_SYSTEM_CPU_PCT", "整机 CPU 占用率（%）"), ("DISPLAY_SYSTEM_LOAD_1M", "系统负载（1 分钟）"),
            ("DISPLAY_SYSTEM_MEM_TOTAL_MB", "系统总内存（MB）"),
            ("DISPLAY_SYSTEM_MEM_AVAILABLE_MB", "系统可用内存（MB）"),
            ("DISPLAY_SYSTEM_STORAGE_FREE_KB", "系统可用存储（KB）"),
        ],
    },
    {
        "key": "device", "title": "设备信息", "description": "序列号、厂商、型号和 Android 版本",
        "collect_key": "COLLECT_DEVICE", "display_key": "DISPLAY_DEVICE",
        "items": [
            ("DISPLAY_DEVICE_SERIAL", "设备序列号"), ("DISPLAY_DEVICE_MANUFACTURER", "厂商"),
            ("DISPLAY_DEVICE_BRAND", "品牌"), ("DISPLAY_DEVICE_MODEL", "型号"),
            ("DISPLAY_ANDROID_VERSION", "Android 版本"), ("DISPLAY_ANDROID_SDK", "Android SDK"),
            ("DISPLAY_BUILD_ID", "Build ID"),
        ],
    },
    {
        "key": "status", "title": "公共状态", "description": "不需要单独采集，控制报告中的状态列",
        "collect_key": None, "display_key": None,
        "items": [("DISPLAY_ADB_STATE", "ADB 状态"), ("DISPLAY_STATUS", "采集状态")],
    },
]


def read_switch_config(path: Path) -> dict[str, bool]:
    """读取配置文件中的 COLLECT_* / DISPLAY_* 开关。"""
    values: dict[str, bool] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return values
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split("=", 1)
        key = key.strip()
        if not (key.startswith("COLLECT_") or key.startswith("DISPLAY_")):
            continue
        values[key] = raw.strip().lower() in {"1", "true", "yes", "on"}
    return values


def default_config_path() -> Path:
    """返回可写的用户配置路径，避免 onefile EXE 写入临时解包目录。"""
    local = application_dir() / "collector_config.conf"
    if local.exists() or not getattr(sys, "frozen", False):
        return local
    appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return appdata / "AndroidAppPerformanceMonitor" / "collector_config.conf"


def write_switch_config(target: Path, values: dict[str, bool]) -> None:
    """在保留中文注释的前提下更新配置开关。"""
    target = Path(target)
    template = resource_path("采集配置.conf")
    source = target if target.exists() else template
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"无法读取配置模板：{source}\n{exc}") from exc
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, _ = line.split("=", 1)
            key = key.strip()
            if key in values:
                output.append(f"{key}={'1' if values[key] else '0'}")
                seen.add(key)
                continue
        output.append(line)
    missing = [key for key in values if key not in seen]
    if missing:
        output.extend(["", "# 由 GUI 生成的开关"])
        output.extend(f"{key}={'1' if values[key] else '0'}" for key in missing)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(output) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise RuntimeError(f"无法保存配置文件：{target}\n{exc}") from exc


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        names = [name]
        if name == "采集配置.conf":
            names.append("collector_config.conf")
        for bundled_name in names:
            candidate = Path(bundle_dir) / bundled_name
            if candidate.exists():
                return candidate
    return application_dir() / name


def command_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {"encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def run_capture(command: list[str], timeout: int = 20) -> tuple[int, str, str]:
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, **command_kwargs())
        return result.returncode, result.stdout or "", result.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def parse_devices(text: str) -> list[dict[str, str]]:
    devices = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        pieces = line.split()
        if len(pieces) >= 2 and pieces[1] == "device":
            details = {"serial": pieces[0], "label": pieces[0]}
            for piece in pieces[2:]:
                if ":" in piece:
                    key, value = piece.split(":", 1)
                    if key in {"model", "device", "product", "transport_id"}:
                        details[key] = value
            suffix = details.get("model") or details.get("device")
            if suffix:
                details["label"] = f"{pieces[0]}  ({suffix})"
            devices.append(details)
    return devices


def safe_packages(raw: str) -> list[str]:
    values = [value.strip() for value in raw.replace("，", ",").split(",") if value.strip()]
    unique: list[str] = []
    for value in values:
        if not PACKAGE_RE.fullmatch(value):
            raise ValueError(f"包名格式不正确：{value}")
        if value not in unique:
            unique.append(value)
    if not unique:
        raise ValueError("至少填写一个应用包名")
    return unique


def valid_time_value(value: str, label: str) -> str:
    value = value.strip()
    if not DURATION_RE.fullmatch(value) or float(re.match(r"^\d+(?:\.\d+)?", value).group(0)) <= 0:
        raise ValueError(f"{label}必须是大于 0 的秒数，或带单位的值，例如 30s、10m、2h")
    return value


class RotatingLogger:
    """按自然日切割日志，保留 7 天，日志目录总量不超过 1 GiB。"""

    def __init__(self, log_dir: Path, callback: Callable[[str], None] | None = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.callback = callback
        self.max_bytes = 1024 * 1024 * 1024
        self.retention_days = 7
        self._handle = None
        self._day: date | None = None
        self._line_count = 0
        self.prune()

    def _path_for(self, day: date) -> Path:
        return self.log_dir / f"{day.isoformat()}.log"

    def _open_for_today(self) -> None:
        today = date.today()
        if self._handle is not None and self._day == today:
            return
        if self._handle is not None:
            self._handle.close()
        self._day = today
        path = self._path_for(today)
        new_file = not path.exists() or path.stat().st_size == 0
        self._handle = path.open("a", encoding="utf-8", newline="")
        if new_file:
            self._handle.write(f"# Android 应用性能采集工具 v{APP_VERSION} 日志\n")
            self._handle.flush()
        self.prune()

    def write(self, message: str, level: str = "INFO") -> None:
        self._open_for_today()
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{level}] {message}".rstrip()
        self._handle.write(line + "\n")
        self._handle.flush()
        self._line_count += 1
        if self.callback:
            self.callback(line)
        if self._line_count % 100 == 0:
            self.prune()

    def info(self, message: str) -> None:
        self.write(message, "INFO")

    def warning(self, message: str) -> None:
        self.write(message, "WARN")

    def error(self, message: str) -> None:
        self.write(message, "ERROR")

    def prune(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        today = date.today()
        oldest_kept = today - timedelta(days=self.retention_days - 1)
        log_files = list(self.log_dir.glob("*.log"))
        for path in log_files:
            try:
                file_day = date.fromisoformat(path.stem)
            except ValueError:
                file_day = None
            if file_day is not None and file_day < oldest_kept and path != self._path_for(today):
                try:
                    path.unlink()
                except OSError:
                    pass
        log_files = [path for path in self.log_dir.glob("*.log") if path.exists()]
        total = sum(path.stat().st_size for path in log_files)
        if total <= self.max_bytes:
            return
        for path in sorted(log_files, key=lambda item: item.stat().st_mtime):
            if path == self._path_for(today):
                continue
            try:
                total -= path.stat().st_size
                path.unlink()
            except OSError:
                continue
            if total <= self.max_bytes:
                break
        # 极端情况下单个当天日志超过上限，保留末尾，避免日志目录继续增长。
        current = self._path_for(today)
        if total > self.max_bytes and current.exists():
            marker = "# 日志已超过 1GB，已保留最新内容。\n".encode("utf-8")
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            try:
                with current.open("rb") as source:
                    source.seek(max(0, current.stat().st_size - self.max_bytes + len(marker)))
                    tail = source.read(self.max_bytes - len(marker))
                with current.open("wb") as target:
                    target.write(marker + tail)
            except OSError:
                pass

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def field_alias(field_name: str) -> str:
    match = re.search(r"[(（]([^()（）]+)[)）]$", field_name)
    if match:
        return match.group(1)
    return field_name


def as_number(value: Any) -> int | float | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"na", "n/a", "null", "none", "-"}:
        return None
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def numeric_field(headers: list[str], alias: str) -> str | None:
    for header in headers:
        if field_alias(header) == alias:
            return header
    return None


def make_typed_rows(headers: list[str], rows: list[dict[str, str]]) -> list[list[Any]]:
    typed = [headers]
    for row in rows:
        typed.append([
            as_number(row.get(header)) if field_alias(header) in NUMERIC_FIELDS else row.get(header, "")
            for header in headers
        ])
    return typed


def build_summary(headers: list[str], rows: list[dict[str, str]], packages: list[str], status: str) -> list[dict[str, Any]]:
    def values(alias: str) -> list[float]:
        field = numeric_field(headers, alias)
        return [float(number) for number in (as_number(row.get(field)) for row in rows) if field and number is not None]

    elapsed = values("elapsed_s")
    one_core = values("cpu_pct_one_core")
    all_cores = values("cpu_pct_all_cores")
    pss = values("pss_mb")
    statuses = Counter(row.get(numeric_field(headers, "status") or "status", "") for row in rows)

    def avg(items: list[float]) -> str:
        return "" if not items else f"{sum(items) / len(items):.2f}"

    def max_value(items: list[float]) -> str:
        return "" if not items else f"{max(items):.2f}"

    return [
        {"字段": "工具版本", "值": APP_VERSION, "说明": "完整性能采集 GUI 版本"},
        {"字段": "采样行数", "值": len(rows), "说明": "所有包名、所有采样点的有效 CSV 数据行数"},
        {"字段": "应用包名", "值": ", ".join(packages), "说明": "本次采集的目标应用"},
        {"字段": "实际采集时长（秒）", "值": max_value(elapsed), "说明": "CSV 中 elapsed_s 的最大值"},
        {"字段": "单核标准 CPU 平均值（%）", "值": avg(one_core), "说明": "应用进程 CPU 时间相对于单个 CPU 核心的占用比例"},
        {"字段": "单核标准 CPU 最大值（%）", "值": max_value(one_core), "说明": "应用进程单核标准 CPU 的峰值"},
        {"字段": "全核 CPU 平均值（%）", "值": avg(all_cores), "说明": "应用进程 CPU 时间相对于整机所有核心的占用比例"},
        {"字段": "PSS 内存平均值（MB）", "值": avg(pss), "说明": "应用进程的比例集大小，按 MB 展示"},
        {"字段": "PSS 内存最大值（MB）", "值": max_value(pss), "说明": "应用进程 PSS 峰值"},
        {"字段": "状态分布", "值": "; ".join(f"{key or '空'}={value}" for key, value in statuses.items()), "说明": "采集状态字段的计数"},
        {"字段": "完成状态", "值": status, "说明": "completed=正常完成，stopped=用户提前停止"},
    ]


def build_html(path: Path, headers: list[str], rows: list[dict[str, str]], summary: list[dict[str, Any]], device: dict[str, str]) -> None:
    cards = "".join(
        f'<div class="card"><span>{html.escape(str(item["字段"]))}</span><b>{html.escape(str(item["值"]))}</b></div>'
        for item in summary[:9]
    )
    table_rows = []
    for row in rows[:500]:
        table_rows.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(header, '')))}</td>" for header in headers) + "</tr>")
    device_rows = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in device.items()
    )
    path.write_text(
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>Android 应用性能报告</title>"
        "<style>body{font-family:'Microsoft YaHei',Arial;margin:28px;color:#243447;background:#f5f7fb}"
        "h1{color:#123b5d}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}"
        ".card,section{background:white;border-radius:10px;padding:16px;box-shadow:0 2px 10px #d9e1ea}"
        ".card span{display:block;color:#6b7a8c;font-size:13px}.card b{display:block;margin-top:8px;font-size:20px}"
        "table{border-collapse:collapse;width:100%;background:#fff}th,td{border:1px solid #dce3ea;padding:7px;text-align:left;font-size:12px}"
        "th{background:#e9f2f9}section{margin-top:18px;overflow:auto}</style>"
        f"<body><h1>Android 应用性能报告</h1><p>生成时间：{html.escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</p>"
        f"<div class='grid'>{cards}</div><section><h2>设备信息</h2><table>{device_rows}</table></section>"
        f"<section><h2>采样明细（最多展示前 500 行）</h2><table><tr>{''.join(f'<th>{html.escape(h)}</th>' for h in headers)}</tr>{''.join(table_rows)}</table></section>"
        "</body></html>", encoding="utf-8"
    )


class PerformanceCollector:
    def __init__(self, options: dict[str, Any], logger: RotatingLogger, events: queue.Queue):
        self.options = options
        self.logger = logger
        self.events = events
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def emit(self, kind: str, *payload: Any) -> None:
        self.events.put((kind, *payload))

    def _device_info(self, adb: str, serial: str) -> dict[str, str]:
        code, output, error = run_capture([adb, "-s", serial, "shell", "getprop"])
        if error.strip():
            self.logger.warning(f"读取设备信息提示：{error.strip()}")
        values: dict[str, str] = {"设备序列号": serial}
        wanted = {
            "ro.product.manufacturer": "厂商",
            "ro.product.brand": "品牌",
            "ro.product.model": "型号",
            "ro.build.version.release": "Android 版本",
            "ro.build.version.sdk": "Android SDK",
            "ro.build.id": "Build ID",
            "ro.product.device": "设备代号",
        }
        for line in output.splitlines():
            match = re.match(r"\[([^]]+)\]: \[([^]]*)\]", line.strip())
            if match and match.group(1) in wanted:
                values[wanted[match.group(1)]] = match.group(2)
        if code != 0:
            values["读取状态"] = "失败"
        else:
            values["读取状态"] = "成功"
        return values

    def _run_adb(self, adb: str, serial: str, duration: str, interval: str, packages: list[str], config: Path, script: Path, csv_path: Path) -> tuple[int, bool, str]:
        remote = f"/data/local/tmp/android_app_perf_monitor_gui_{os.getpid()}_{int(time.time())}.conf"
        push_code, push_output, push_error = run_capture([adb, "-s", serial, "push", str(config), remote], timeout=30)
        if push_code != 0:
            detail = push_error.strip() or push_output.strip() or "adb push 返回非 0"
            raise RuntimeError(f"配置文件推送失败（ADB 返回码 {push_code}）：{detail}")
        # adb shell 会把 sh -s 后的第一个参数作为设备端脚本的 $1，
        # 因此不能额外插入脚本名，否则 duration/配置路径会整体错位。
        command = [adb, "-s", serial, "shell", "sh", "-s", duration, interval, ",".join(packages),
                   self.options["launch"], remote]
        self.logger.info("开始执行设备端性能采集")
        self.logger.info("目标设备：" + serial + "；目标包名：" + ", ".join(packages))
        self.logger.info("采集时长：" + duration + "；采样间隔：" + interval)
        stopped = False
        process = None
        stderr_lines: list[str] = []
        try:
            # `adb shell sh -s ...` 从标准输入读取设备端脚本；不能只启动命令而不传脚本，
            # 否则 sh 会立即结束并生成空 CSV。
            with script.open("rb") as script_input, csv_path.open("w", encoding="utf-8-sig", newline="") as output:
                process = subprocess.Popen(command, stdin=script_input, stdout=output, stderr=subprocess.PIPE,
                                            text=True, **command_kwargs())

                def drain_stderr() -> None:
                    if process is None or process.stderr is None:
                        return
                    for line in process.stderr:
                        message = line.strip()
                        if message:
                            stderr_lines.append(message)
                            self.logger.info("ADB：" + message)

                reader = threading.Thread(target=drain_stderr, daemon=True)
                reader.start()
                while process.poll() is None:
                    if self.stop_event.is_set():
                        stopped = True
                        self.logger.warning("收到停止请求，正在保留已采集数据")
                        process.terminate()
                        break
                    time.sleep(0.2)
                process.wait(timeout=20)
                reader.join(timeout=3)
            return process.returncode or 0, stopped, "\n".join(stderr_lines)
        finally:
            run_capture([adb, "-s", serial, "shell", "rm", "-f", remote], timeout=20)

    def run(self) -> None:
        output_dir = Path(self.options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        adb = self.options["adb"]
        config = Path(self.options["config"])
        script = resource_path("android_app_perf_monitor.sh")
        if not config.exists():
            raise RuntimeError(f"找不到采集配置文件：{config}")
        if not script.exists():
            raise RuntimeError(f"找不到设备端采集脚本：{script}")
        code, output, error = run_capture([adb, "devices", "-l"])
        if code != 0:
            raise RuntimeError(f"ADB 不可用：{error.strip() or output.strip()}")
        devices = parse_devices(output)
        serial = self.options["serial"].strip()
        if not serial:
            if len(devices) != 1:
                labels = "、".join(item["serial"] for item in devices) or "没有在线设备"
                raise RuntimeError(f"无法自动选择设备，当前在线设备：{labels}")
            serial = devices[0]["serial"]
        if not any(item["serial"] == serial for item in devices):
            raise RuntimeError(f"设备序列号不在线：{serial}")
        self.options["serial"] = serial
        device = self._device_info(adb, serial)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = output_dir / f"android_perf_{stamp}"
        csv_path = base.with_suffix(".csv")
        self.logger.info(f"报告目录：{output_dir}")
        return_code, stopped, adb_error = self._run_adb(adb, serial, self.options["duration"], self.options["interval"], self.options["packages"], config, script, csv_path)
        if return_code != 0 and not stopped:
            detail = adb_error or "设备端没有返回错误文本；请检查 USB 调试授权、设备存储权限和 ADB 版本。"
            raise RuntimeError(f"设备端采集失败，ADB 返回码：{return_code}\n设备端错误：{detail}")
        if not csv_path.exists() or csv_path.stat().st_size == 0:
            detail = adb_error or "设备端没有输出 CSV；请检查包名是否已安装、配置文件是否可读，以及设备是否允许 shell 读取进程信息。"
            raise RuntimeError(f"没有生成有效采集数据。\n设备端信息：{detail}")
        with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            headers = reader.fieldnames or []
            rows = list(reader)
        if not headers:
            detail = adb_error or "CSV 只有空内容或没有表头。"
            raise RuntimeError(f"CSV 没有表头，无法生成报告。\n设备端信息：{detail}")
        state = "stopped" if stopped else "completed"
        summary = build_summary(headers, rows, self.options["packages"], state)
        params = [
            ["参数", "值"], ["工具版本", APP_VERSION], ["设备序列号", serial],
            ["应用包名", ", ".join(self.options["packages"])], ["采集时长", self.options["duration"]],
            ["采样间隔", self.options["interval"]], ["启动模式", self.options["launch"]],
            ["采集项目", self.options.get("selected_collect", "")],
            ["CSV 显示项目", self.options.get("selected_display", "")],
            ["采集配置文件", str(self.options.get("config", ""))],
            ["报告目录", str(output_dir)], ["日志目录", str(self.logger.log_dir)],
            ["日志策略", "按日期切割；保留 7 天；总量最大 1GB"],
            ["开始时间", self.options["started_at"]], ["结束时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ]
        output_paths: list[Path] = []
        types = self.options["report_types"]
        if "csv" in types:
            output_paths.append(csv_path)
        if "excel" in types:
            xlsx_path = base.with_suffix(".xlsx")
            summary_rows = [[item["字段"], item["值"], item["说明"]] for item in summary]
            device_rows = [[key, value] for key, value in device.items()]
            write_workbook(xlsx_path, [
                ("采集数据", make_typed_rows(headers, rows)),
                ("采集摘要", [["字段", "值", "说明"]] + summary_rows),
                ("运行参数", params),
                ("设备信息", [["项目", "值"]] + device_rows),
            ])
            output_paths.append(xlsx_path)
        if "json" in types:
            json_path = base.with_suffix(".json")
            json_path.write_text(json.dumps({"version": APP_VERSION, "summary": summary,
                                              "device": device, "headers": headers, "rows": rows},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
            output_paths.append(json_path)
        if "html" in types:
            html_path = base.with_suffix(".html")
            build_html(html_path, headers, rows, summary, device)
            output_paths.append(html_path)
        if "csv" not in types:
            try:
                csv_path.unlink()
            except OSError:
                pass
        self.logger.info(f"采集完成：{len(rows)} 行；状态：{state}")
        self.logger.info("已生成：" + "、".join(str(item) for item in output_paths))
        self.logger.prune()
        self.emit("done", [str(item) for item in output_paths], str(self.logger.log_dir / f"{date.today().isoformat()}.log"), state, len(rows))


class PerformanceApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x900")
        self.root.minsize(980, 760)
        self.events: queue.Queue = queue.Queue()
        self.collector: PerformanceCollector | None = None
        self.logger: RotatingLogger | None = None
        self.serial_values: list[dict[str, str]] = []
        self.advanced_visible = False
        self.default_config_values = read_switch_config(resource_path("采集配置.conf"))
        self.collect_vars: dict[str, tk.BooleanVar] = {}
        self.display_parent_vars: dict[str, tk.BooleanVar] = {}
        self.display_vars: dict[str, tk.BooleanVar] = {}
        self.display_widgets: dict[str, list[tk.Checkbutton]] = {}
        self._setup_style()
        self._build_ui()
        self.root.after(200, self._poll_events)
        self.root.after(300, self.refresh_devices)

    def _setup_style(self) -> None:
        self.root.configure(bg="#f5f5f7")
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"), foreground="#1d1d1f", background="#ffffff")
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 10), foreground="#6e6e73", background="#ffffff")
        style.configure("Version.TLabel", font=("Consolas", 10, "bold"), foreground="#087f6e", background="#e1f5f0")
        style.configure("Card.TLabelframe", background="#ffffff", borderwidth=1, relief="solid")
        style.configure("Card.TLabelframe.Label", font=("Microsoft YaHei UI", 11, "bold"), foreground="#1d1d1f", background="#ffffff")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabel", background="#ffffff", foreground="#3a3a3c", font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background="#ffffff", foreground="#1d1d1f", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=(18, 9), foreground="#ffffff")
        style.configure("Danger.TButton", foreground="#a33a36", padding=(12, 8))
        style.configure("Help.TLabel", background="#ffffff", foreground="#6e6e73", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background="#e1f5f0", foreground="#087f6e", padding=(10, 6), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TEntry", padding=7, fieldbackground="#ffffff")
        style.configure("TCombobox", padding=5)
        style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=(11, 6))

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg="#f5f5f7")
        outer.pack(fill="both", expand=True)
        shell = tk.Frame(outer, bg="#f5f5f7")
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, bg="#ededf0", width=222, highlightbackground="#dedee3", highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="Android", bg="#ededf0", fg="#1d1d1f",
                 font=("Microsoft YaHei UI", 17, "bold"), anchor="w").pack(fill="x", padx=24, pady=(28, 0))
        tk.Label(sidebar, text="应用性能采集", bg="#ededf0", fg="#6e6e73",
                 font=("Microsoft YaHei UI", 10), anchor="w").pack(fill="x", padx=25, pady=(0, 26))
        tk.Frame(sidebar, bg="#d8d8de", height=1).pack(fill="x", padx=22, pady=(0, 20))

        def sidebar_step(number: str, title: str, detail: str, active: bool = False) -> None:
            background = "#ffffff" if active else "#ededf0"
            row = tk.Frame(sidebar, bg=background, highlightbackground="#e1e1e6" if active else background,
                           highlightthickness=1 if active else 0)
            row.pack(fill="x", padx=12, pady=4)
            if active:
                tk.Frame(row, bg="#0a7b6c", width=3).pack(side="left", fill="y")
            body = tk.Frame(row, bg=background)
            body.pack(side="left", fill="x", expand=True, padx=(10, 12), pady=10)
            tk.Label(body, text=number, bg=background, fg="#0a7b6c" if active else "#8a8a90",
                     font=("Consolas", 10, "bold"), anchor="w").pack(fill="x")
            tk.Label(body, text=title, bg=background, fg="#1d1d1f" if active else "#4b4b50",
                     font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(fill="x", pady=(2, 0))
            tk.Label(body, text=detail, bg=background, fg="#7b7b81",
                     font=("Microsoft YaHei UI", 8), anchor="w").pack(fill="x", pady=(2, 0))

        sidebar_step("01", "任务设置", "设备 · 包名 · 时长", True)
        sidebar_step("02", "采集项目", "按需勾选性能指标")
        sidebar_step("03", "输出报告", "Excel · CSV · HTML")
        tk.Frame(sidebar, bg="#d8d8de", height=1).pack(fill="x", padx=22, pady=(24, 16))
        tk.Label(sidebar, text="小提示", bg="#ededf0", fg="#6e6e73",
                 font=("Microsoft YaHei UI", 9, "bold"), anchor="w").pack(fill="x", padx=25)
        tk.Label(sidebar, text="采集前请保持手机连接，\n窗口关闭不会自动停止设备端采集。", bg="#ededf0", fg="#8a8a90",
                 font=("Microsoft YaHei UI", 9), justify="left", anchor="w").pack(fill="x", padx=25, pady=(6, 0))
        tk.Label(sidebar, text=f"离线工具 · v{APP_VERSION}", bg="#ededf0", fg="#8a8a90",
                 font=("Consolas", 8), anchor="w").pack(side="bottom", fill="x", padx=25, pady=24)

        main = tk.Frame(shell, bg="#f5f5f7", padx=24, pady=24)
        main.pack(side="left", fill="both", expand=True)
        outer = main
        banner = tk.Frame(outer, bg="#ffffff", height=82, highlightbackground="#e5e5ea", highlightthickness=1)
        banner.pack(fill="x", pady=(0, 14))
        banner.pack_propagate(False)
        traffic = tk.Canvas(banner, width=48, height=18, bg="#ffffff", highlightthickness=0, bd=0)
        traffic.place(x=18, y=15)
        for x, color in ((7, "#ff5f57"), (23, "#febc2e"), (39, "#28c840")):
            traffic.create_oval(x - 5, 4, x + 5, 14, fill=color, outline=color)
        ttk.Label(banner, text="Android 应用性能采集", style="Title.TLabel").pack(anchor="w", padx=76, pady=(12, 0))
        ttk.Label(banner, text="选择设备与应用，勾选需要的项目，然后开始采集", style="Sub.TLabel").pack(anchor="w", padx=77, pady=(1, 0))
        tk.Label(banner, text=f"v{APP_VERSION}", bg="#e1f5f0", fg="#087f6e", font=("Consolas", 10, "bold"), padx=10, pady=4).place(relx=0.97, rely=0.28, anchor="e")

        basic = ttk.LabelFrame(outer, text="第一步：选择要检测的应用和时间", style="Card.TLabelframe", padding=16)
        basic.pack(fill="x", pady=(0, 12))
        for col in range(4):
            basic.columnconfigure(col, weight=1 if col in (1, 3) else 0)
        self.package_var = tk.StringVar(value="com.uusafe.portal")
        self.duration_var = tk.StringVar(value="10m")
        self.interval_var = tk.StringVar(value="5")
        self.launch_var = tk.StringVar(value="none")
        self.serial_var = tk.StringVar()
        self._field(basic, 0, 0, "应用包名", self.package_var)
        ttk.Label(basic, text="例如：com.uusafe.portal；多个应用用逗号分隔", style="Help.TLabel").grid(row=0, column=2, columnspan=2, sticky="w", padx=(10, 0))
        self._field(basic, 1, 0, "检测多长时间", self.duration_var)
        ttk.Label(basic, text="填写 30s、10m 或 2h", style="Help.TLabel").grid(row=1, column=2, columnspan=2, sticky="w", padx=(10, 0), pady=(12, 0))
        self._field(basic, 2, 0, "每隔多久记录一次", self.interval_var)
        ttk.Label(basic, text="一般填写 5 秒；数值越小数据越密集", style="Help.TLabel").grid(row=2, column=2, columnspan=2, sticky="w", padx=(10, 0), pady=(12, 0))
        ttk.Label(basic, text="启动方式", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.launch_display_var = tk.StringVar(value="不主动启动")
        ttk.Combobox(basic, textvariable=self.launch_display_var, values=["不主动启动", "先关闭再启动（冷启动）", "直接启动（热启动）"], state="readonly").grid(row=3, column=1, sticky="ew", padx=(10, 22), pady=(12, 0))
        ttk.Label(basic, text="通常保持“不主动启动”即可", style="Help.TLabel").grid(row=3, column=2, columnspan=2, sticky="w", padx=(10, 0), pady=(12, 0))
        ttk.Label(basic, text="连接的手机", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=(12, 0))
        serial_frame = ttk.Frame(basic, style="Card.TFrame")
        serial_frame.grid(row=4, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=(12, 0))
        serial_frame.columnconfigure(0, weight=1)
        self.serial_box = ttk.Combobox(serial_frame, textvariable=self.serial_var, state="normal")
        self.serial_box.grid(row=0, column=0, sticky="ew")
        ttk.Button(serial_frame, text="重新查找手机", command=self.refresh_devices).grid(row=0, column=1, padx=(8, 0))

        output = ttk.LabelFrame(outer, text="第二步：选择报告保存位置", style="Card.TLabelframe", padding=16)
        output.pack(fill="x", pady=(0, 12))
        output.columnconfigure(1, weight=1)
        self.output_var = tk.StringVar(value=str(self.default_output_dir()))
        self.adb_var = tk.StringVar(value=shutil.which("adb") or "adb")
        self.config_var = tk.StringVar(value=str(default_config_path()))
        self._path_field(output, 0, "报告保存到", self.output_var, directory=True)
        ttk.Label(output, text="默认保存到桌面附近的 Android性能报告 文件夹", style="Help.TLabel").grid(row=0, column=3, sticky="w", padx=(10, 0))
        ttk.Label(output, text="要生成哪些文件", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 0))
        report_frame = ttk.Frame(output, style="Card.TFrame")
        report_frame.grid(row=1, column=1, columnspan=3, sticky="w", padx=(10, 0), pady=(12, 0))
        self.report_vars = {key: tk.BooleanVar(value=key in {"csv", "excel"}) for key in ("csv", "excel", "html", "json")}
        for label, key in (("Excel 报告（推荐）", "excel"), ("CSV 原始数据", "csv"), ("网页报告", "html"), ("JSON 数据", "json")):
            ttk.Checkbutton(report_frame, text=label, variable=self.report_vars[key]).pack(side="left", padx=(0, 18))
        ttk.Label(output, text="运行日志", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 0))
        ttk.Label(output, text="自动保存到报告目录/logs，按日期切割，保留 7 天，最大 1GB", style="Help.TLabel").grid(row=2, column=1, columnspan=3, sticky="w", padx=(10, 0), pady=(12, 0))

        self.advanced_toggle = ttk.Button(outer, text="显示高级设置（ADB、配置文件路径）", command=self.toggle_advanced)
        self.advanced_toggle.pack(anchor="w", pady=(0, 8))
        self.advanced_frame = ttk.LabelFrame(outer, text="高级设置（一般不需要修改）", style="Card.TLabelframe", padding=12)
        self.advanced_frame.columnconfigure(1, weight=1)
        self._path_field(self.advanced_frame, 0, "ADB 程序路径", self.adb_var, filetypes=[("ADB 程序", "adb.exe"), ("所有文件", "*.*")])
        self._path_field(self.advanced_frame, 1, "采集配置文件", self.config_var, filetypes=[("配置文件", "*.conf"), ("所有文件", "*.*")])
        ttk.Label(self.advanced_frame, text="下方“采集项目”勾选后会自动保存到这个配置文件，并用于本次采集。", style="Help.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.advanced_frame.pack(fill="x", pady=(0, 10))
        self.advanced_frame.pack_forget()

        project_panel = ttk.LabelFrame(outer, text="第三步：勾选采集项目（全部项目可滚动查看）", style="Card.TLabelframe", padding=10)
        project_panel.pack(fill="both", expand=True, pady=(0, 10))
        project_toolbar = ttk.Frame(project_panel, style="Card.TFrame")
        project_toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(project_toolbar, text="全部勾选", command=self.select_all_projects).pack(side="left")
        ttk.Button(project_toolbar, text="清空选择", command=self.clear_all_projects).pack(side="left", padx=(8, 0))
        ttk.Button(project_toolbar, text="恢复默认", command=self.restore_default_projects).pack(side="left", padx=(8, 0))
        ttk.Button(project_toolbar, text="保存配置文件", command=self.save_config).pack(side="left", padx=(8, 0))
        self.project_summary_var = tk.StringVar()
        ttk.Label(project_toolbar, textvariable=self.project_summary_var, style="Help.TLabel").pack(side="right")
        self._build_project_picker(project_panel)

        action = ttk.Frame(outer)
        action.pack(fill="x", pady=(2, 10))
        self.start_button = tk.Button(action, text="开始采集并生成报告", command=self.start,
                                      bg="#0a7b6c", fg="#ffffff", activebackground="#086658", activeforeground="#ffffff",
                                      relief="flat", bd=0, padx=22, pady=10, font=("Microsoft YaHei UI", 11, "bold"), cursor="hand2")
        self.start_button.pack(side="left")
        self.stop_button = tk.Button(action, text="停止并保留已采集数据", command=self.stop,
                                     bg="#ffffff", fg="#a33a36", activebackground="#fff2f1", activeforeground="#8f2e2a",
                                     relief="flat", bd=0, padx=14, pady=10, font=("Microsoft YaHei UI", 9), cursor="hand2", state="disabled")
        self.stop_button.pack(side="left", padx=(12, 0))
        self.status_var = tk.StringVar(value="正在查找手机…")
        ttk.Label(action, textvariable=self.status_var, style="Status.TLabel").pack(side="right")

        log_frame = ttk.LabelFrame(outer, text="运行日志（出现问题时可复制给技术人员）", style="Card.TLabelframe", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=12, bg="#1d1d1f", fg="#e8e8ed", insertbackground="#ffffff", relief="flat", font=("Consolas", 9), wrap="word", padx=12, pady=8)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.update_project_summary()

    def toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.pack(fill="x", pady=(0, 10))
            self.advanced_toggle.configure(text="隐藏高级设置（ADB、配置文件路径）")
        else:
            self.advanced_frame.pack_forget()
            self.advanced_toggle.configure(text="显示高级设置（ADB、配置文件路径）")

    def _build_project_picker(self, parent: ttk.LabelFrame) -> None:
        """构建可滚动的采集项目面板。"""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        holder = tk.Frame(parent, bg="#f5f8fb")
        holder.pack(fill="both", expand=True)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        canvas = tk.Canvas(holder, bg="#f5f8fb", highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        cards = tk.Frame(canvas, bg="#f5f8fb")
        window_id = canvas.create_window((0, 0), window=cards, anchor="nw")
        cards.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        canvas.bind("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))

        for index, group in enumerate(PROJECT_GROUPS):
            row, column = divmod(index, 2)
            cards.columnconfigure(column, weight=1)
            card = tk.Frame(cards, bg="#ffffff", highlightbackground="#dce6ee", highlightthickness=1, bd=0)
            card.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 6 if column == 0 else 0), pady=(0, 8))
            card.columnconfigure(0, weight=1)

            header = tk.Frame(card, bg="#ffffff")
            header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
            header.columnconfigure(0, weight=1)
            tk.Label(header, text=group["title"], bg="#ffffff", fg="#123b5d",
                     font=("Microsoft YaHei", 11, "bold"), anchor="w").grid(row=0, column=0, sticky="w")
            if group["collect_key"]:
                collect_var = tk.BooleanVar(value=self.default_config_values.get(group["collect_key"], False))
                self.collect_vars[group["collect_key"]] = collect_var
                tk.Checkbutton(header, text="采集此类", variable=collect_var, command=self.update_project_summary,
                               bg="#ffffff", activebackground="#ffffff", fg="#167968", selectcolor="#d9f3ed",
                               font=("Microsoft YaHei", 9, "bold"), bd=0, highlightthickness=0).grid(row=0, column=1, sticky="e")
            else:
                tk.Label(header, text="状态列", bg="#ffffff", fg="#718394",
                         font=("Microsoft YaHei", 9), anchor="e").grid(row=0, column=1, sticky="e")
            tk.Label(card, text=group["description"], bg="#ffffff", fg="#728395",
                     font=("Microsoft YaHei", 9), anchor="w").grid(row=1, column=0, sticky="ew", padx=12)

            display_key = group["display_key"]
            initial_parent = self.default_config_values.get(display_key, False) if display_key else any(
                self.default_config_values.get(item_key, False) for item_key, _label in group["items"]
            )
            parent_var = tk.BooleanVar(value=initial_parent)
            self.display_parent_vars[group["key"]] = parent_var
            parent_row = tk.Frame(card, bg="#f1f7f6")
            parent_row.grid(row=2, column=0, sticky="ew", padx=10, pady=(8, 4))
            parent_row.columnconfigure(0, weight=1)
            display_label = "显示到 CSV" if display_key else "显示状态列"
            tk.Checkbutton(parent_row, text=display_label, variable=parent_var,
                           command=lambda current=group: self.toggle_display_parent(current),
                           bg="#f1f7f6", activebackground="#f1f7f6", fg="#126e62", selectcolor="#d4eee8",
                           font=("Microsoft YaHei", 9, "bold"), bd=0, highlightthickness=0).grid(row=0, column=0, sticky="w", padx=6, pady=3)

            item_frame = tk.Frame(card, bg="#ffffff")
            item_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
            item_frame.columnconfigure(0, weight=1)
            item_frame.columnconfigure(1, weight=1)
            widgets: list[tk.Checkbutton] = []
            for item_index, (item_key, item_label) in enumerate(group["items"]):
                item_var = tk.BooleanVar(value=self.default_config_values.get(item_key, False))
                self.display_vars[item_key] = item_var
                item_check = tk.Checkbutton(item_frame, text=item_label, variable=item_var,
                                            command=self.update_project_summary, bg="#ffffff",
                                            activebackground="#ffffff", fg="#34495a", selectcolor="#ffffff",
                                            font=("Microsoft YaHei", 9), anchor="w", justify="left",
                                            bd=0, highlightthickness=0)
                item_check.grid(row=item_index // 2, column=item_index % 2, sticky="w", padx=4, pady=2)
                widgets.append(item_check)
            self.display_widgets[group["key"]] = widgets
            self._sync_display_widgets(group)

        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

    def _sync_display_widgets(self, group: dict[str, Any]) -> None:
        state = "normal" if self.display_parent_vars[group["key"]].get() else "disabled"
        for widget in self.display_widgets.get(group["key"], []):
            widget.configure(state=state)

    def toggle_display_parent(self, group: dict[str, Any]) -> None:
        parent = self.display_parent_vars[group["key"]]
        if parent.get() and not any(self.display_vars[item_key].get() for item_key, _label in group["items"]):
            for item_key, _label in group["items"]:
                self.display_vars[item_key].set(True)
        self._sync_display_widgets(group)
        self.update_project_summary()

    def update_project_summary(self) -> None:
        collect_count = sum(1 for variable in self.collect_vars.values() if variable.get())
        display_count = 0
        for group in PROJECT_GROUPS:
            if self.display_parent_vars[group["key"]].get():
                display_count += sum(1 for item_key, _label in group["items"] if self.display_vars[item_key].get())
        self.project_summary_var.set(f"已选采集大类 {collect_count} 个 · CSV 显示列 {display_count} 个")

    def select_all_projects(self) -> None:
        for variable in self.collect_vars.values():
            variable.set(True)
        for group in PROJECT_GROUPS:
            self.display_parent_vars[group["key"]].set(True)
            for item_key, _label in group["items"]:
                self.display_vars[item_key].set(True)
            self._sync_display_widgets(group)
        self.update_project_summary()

    def clear_all_projects(self) -> None:
        for variable in self.collect_vars.values():
            variable.set(False)
        for group in PROJECT_GROUPS:
            self.display_parent_vars[group["key"]].set(False)
            for item_key, _label in group["items"]:
                self.display_vars[item_key].set(False)
            self._sync_display_widgets(group)
        self.update_project_summary()

    def restore_default_projects(self) -> None:
        for key, variable in self.collect_vars.items():
            variable.set(self.default_config_values.get(key, False))
        for group in PROJECT_GROUPS:
            display_key = group["display_key"]
            parent = self.display_parent_vars[group["key"]]
            if display_key:
                parent.set(self.default_config_values.get(display_key, False))
            else:
                parent.set(any(self.default_config_values.get(item_key, False) for item_key, _label in group["items"]))
            for item_key, _label in group["items"]:
                self.display_vars[item_key].set(self.default_config_values.get(item_key, False))
            self._sync_display_widgets(group)
        self.update_project_summary()

    def config_values_from_ui(self) -> dict[str, bool]:
        values = dict(self.default_config_values)
        for key, variable in self.collect_vars.items():
            values[key] = variable.get()
        for group in PROJECT_GROUPS:
            if group["display_key"]:
                values[group["display_key"]] = self.display_parent_vars[group["key"]].get()
            for item_key, _label in group["items"]:
                values[item_key] = self.display_vars[item_key].get()
        return values

    def selected_collection_names(self) -> list[str]:
        selected = []
        for group in PROJECT_GROUPS:
            key = group["collect_key"]
            if key and self.collect_vars.get(key) and self.collect_vars[key].get():
                selected.append(group["title"])
        return selected

    def selected_display_names(self) -> list[str]:
        selected = []
        for group in PROJECT_GROUPS:
            if not self.display_parent_vars[group["key"]].get():
                continue
            for item_key, item_label in group["items"]:
                if self.display_vars.get(item_key) and self.display_vars[item_key].get():
                    selected.append(item_label)
        return selected

    def save_config(self, show_message: bool = True) -> Path | None:
        try:
            target = Path(self.config_var.get().strip() or default_config_path()).expanduser()
            write_switch_config(target, self.config_values_from_ui())
            self.config_var.set(str(target))
            self.append_log(f"已保存采集配置：{target}")
            if show_message:
                messagebox.showinfo("配置已保存", f"采集项目配置已保存到：\n{target}")
            return target
        except Exception as exc:
            if show_message:
                messagebox.showerror("保存配置失败", str(exc))
            else:
                raise
            return None

    def _field(self, parent: ttk.Frame, row: int, label_col: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=label_col, sticky="w", pady=(0 if row == 0 else 12, 0))
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=label_col + 1, sticky="ew", padx=(10, 22), pady=(0 if row == 0 else 12, 0))

    def _path_field(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, directory: bool = False, filetypes: list[tuple[str, str]] | None = None) -> None:
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=(0 if row == 0 else 8, 0))
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(10, 8), pady=(0 if row == 0 else 8, 0))
        ttk.Button(parent, text="浏览…", command=lambda: self.browse(variable, directory, filetypes)).grid(row=row, column=2, pady=(0 if row == 0 else 8, 0))

    @staticmethod
    def default_output_dir() -> Path:
        home = Path.home()
        for folder in (home / "Desktop", home / "桌面", home / "Documents", home / "文档"):
            if folder.exists():
                return folder / "Android性能报告"
        return application_dir() / "reports"

    def browse(self, variable: tk.StringVar, directory: bool, filetypes: list[tuple[str, str]] | None) -> None:
        selected = filedialog.askdirectory() if directory else filedialog.askopenfilename(filetypes=filetypes or [("所有文件", "*.*")])
        if selected:
            variable.set(selected)

    def refresh_devices(self) -> None:
        adb = self.adb_var.get().strip() or "adb"
        code, output, error = run_capture([adb, "devices", "-l"])
        self.serial_values = parse_devices(output) if code == 0 else []
        # 下拉框的实际值必须是序列号；型号只放在状态提示里，避免用户选择后把“序列号（型号）”当成 serial。
        self.serial_box["values"] = [item["serial"] for item in self.serial_values]
        if len(self.serial_values) == 1:
            self.serial_var.set(self.serial_values[0]["serial"])
            self.status_var.set("已自动识别 1 台在线设备")
        elif len(self.serial_values) > 1:
            self.serial_var.set("")
            self.status_var.set(f"发现 {len(self.serial_values)} 台设备，请选择序列号")
        else:
            self.status_var.set("未发现在线设备，请检查 ADB 和 USB 调试")
            if error.strip():
                self.append_log("ADB：" + error.strip())

    def append_log(self, text: str) -> None:
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")

    def start(self) -> None:
        try:
            packages = safe_packages(self.package_var.get())
            duration = valid_time_value(self.duration_var.get(), "采集时长")
            interval = valid_time_value(self.interval_var.get(), "采样间隔")
            output_dir = Path(self.output_var.get().strip()).expanduser()
            if not output_dir:
                raise ValueError("请选择报告保存目录")
            selected_collect = self.selected_collection_names()
            if not selected_collect:
                raise ValueError("请至少勾选一个“采集此类”项目，例如 CPU 或 内存")
            report_types = [key for key, variable in self.report_vars.items() if variable.get()]
            if not report_types:
                raise ValueError("至少选择一种报告类型")
            adb = self.adb_var.get().strip() or "adb"
            if os.path.sep in adb and not Path(adb).exists():
                raise ValueError(f"找不到 ADB：{adb}")
            config_path = self.save_config(show_message=False)
            if config_path is None:
                raise ValueError("采集配置文件保存失败")
            launch_map = {"不主动启动": "none", "先关闭再启动（冷启动）": "cold", "直接启动（热启动）": "hot"}
            options = {"adb": adb, "serial": self.serial_var.get().strip(), "packages": packages,
                       "duration": duration, "interval": interval, "launch": self.launch_var.get(),
                       "output_dir": output_dir, "config": str(config_path),
                       "selected_collect": "、".join(selected_collect),
                       "selected_display": "、".join(self.selected_display_names()),
                       "report_types": report_types, "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            options["launch"] = launch_map.get(self.launch_display_var.get(), "none")
            log_dir = output_dir / "logs"
            self.logger = RotatingLogger(log_dir, callback=lambda line: self.events.put(("log", line)))
            self.logger.info("启动完整性能采集")
            self.logger.info("日志策略：按日期切割，保留 7 天，目录总量最大 1GB")
            self.logger.info("采集项目：" + (options["selected_collect"] or "无"))
            self.logger.info("CSV 显示项目：" + (options["selected_display"] or "无"))
            self.logger.info("配置文件：" + str(config_path))
            self.collector = PerformanceCollector(options, self.logger, self.events)
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.status_var.set("采集中，请保持设备连接…")
            threading.Thread(target=self._run_worker, daemon=True).start()
        except Exception as exc:
            messagebox.showerror("无法开始采集", str(exc))

    def _run_worker(self) -> None:
        try:
            if self.collector is None:
                return
            self.collector.run()
        except Exception as exc:
            if self.logger:
                self.logger.error(str(exc))
            self.events.put(("error", str(exc)))
        finally:
            if self.logger:
                self.logger.close()

    def stop(self) -> None:
        if self.collector:
            self.collector.stop()
            self.status_var.set("正在停止，保留已经采集的数据…")
            self.stop_button.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self.append_log(event[1])
                elif kind == "done":
                    paths, log_path, state, count = event[1:]
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set(f"完成：{count} 行，状态 {state}")
                    text = "已生成报告：\n" + "\n".join(paths) + f"\n\n日志：{log_path}"
                    messagebox.showinfo("采集完成", text)
                elif kind == "error":
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set("采集失败")
                    messagebox.showerror("采集失败", event[1])
        except queue.Empty:
            pass
        self.root.after(200, self._poll_events)


def main() -> None:
    root = tk.Tk()
    PerformanceApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

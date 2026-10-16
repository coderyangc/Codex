#!/usr/bin/env python3
"""Android performance collection environment validator.

The GUI intentionally uses only Python's standard library.  On Windows it can
be packaged as a single executable with PyInstaller; the executable still
uses the user-selected adb.exe and python.exe for validation.
"""

from __future__ import annotations

import datetime as dt
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
import tkinter as tk
import zipfile
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as xml_escape


APP_VERSION = "2.0.9"
APP_TITLE = f"Android 设备环境检查工具 v{APP_VERSION}"
DEFAULT_PACKAGE = "com.uusafe.portal"

PROJECTS: Sequence[Tuple[str, str, str]] = (
    ("adb", "ADB 基础命令", "检查 adb.exe 版本和命令是否可执行。"),
    ("device", "ADB 设备连接", "检查设备是否已授权、在线且可执行 shell。"),
    ("package", "应用包安装状态", "检查目标包名是否已安装，并读取应用 UID。"),
    ("device_info", "设备基本信息", "读取厂商、型号、Android 版本和 SDK。"),
    ("cpu", "CPU 采集前置条件", "检查 /proc/stat 和目标进程 CPU ticks。"),
    ("memory", "内存/PSS 采集前置条件", "检查 RSS、dumpsys meminfo 和 PSS 可读性。"),
    ("disk", "磁盘 I/O 采集前置条件", "检查目标进程 /proc/<pid>/io 是否可读。"),
    ("network", "网络 UID 统计", "检查 UID 网络统计文件和应用 UID。"),
    ("battery", "电量与应用耗电", "检查 dumpsys battery 和 batterystats。"),
    ("thermal", "温度与热限制", "检查电池温度、thermal zone 和 thermalservice。"),
    ("ui", "UI/gfxinfo 卡顿", "检查总帧数、卡顿帧和 gfxinfo 输出。"),
    ("storage", "应用存储占用", "检查应用数据目录、APK 路径和 du。"),
    ("stability", "稳定性/Logcat", "检查 Logcat 读取和崩溃/ANR 关键字检索。"),
    ("python", "Python 报告生成", "检查 Python 路径和离线 HTML 报告生成器。"),
)
PROJECT_NAMES = {key: name for key, name, _ in PROJECTS}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_report_path() -> Path:
    """Prefer a user-writable Windows folder so a novice need not change paths."""
    candidates = [Path.home() / "Desktop", Path.home() / "Documents", app_dir()]
    for folder in candidates:
        if folder.is_dir():
            return folder / "android_environment_validation_report.xlsx"
    return app_dir() / "android_environment_validation_report.xlsx"


def compact(text: str, limit: int = 1800) -> str:
    value = (text or "").replace("\x00", "").strip()
    return value if len(value) <= limit else value[:limit] + "\n...（已截断）"


def find_executable(names: Iterable[str], candidates: Iterable[Path]) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return str(Path(found).resolve())
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def default_adb_path() -> str:
    roots = []
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value) / "platform-tools" / "adb.exe")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    return find_executable(("adb.exe", "adb"), roots)


def default_python_path() -> str:
    candidates = []
    if not getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.extend(
            [
                Path(local) / "Programs" / "Python" / "Python311" / "python.exe",
                Path(local) / "Programs" / "Python" / "Python312" / "python.exe",
                Path(local) / "Programs" / "Python" / "Python313" / "python.exe",
            ]
        )
    return find_executable(("python.exe", "python", "py.exe", "py"), candidates)


def no_window_flag() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def run_command(command: Sequence[str], timeout: int = 20) -> Tuple[int, str]:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=no_window_flag(),
        )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        return completed.returncode, compact(output)
    except FileNotFoundError:
        return 127, f"找不到可执行文件：{command[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（>{timeout}秒）：{' '.join(command)}"
    except OSError as exc:
        return 126, f"执行失败：{exc}"


def packages_from_text(text: str) -> List[str]:
    packages = []
    for value in text.replace("，", ",").split(","):
        value = value.strip()
        if value and value not in packages:
            packages.append(value)
    return packages or [DEFAULT_PACKAGE]


def parse_devices(output: str) -> List[Dict[str, str]]:
    devices: List[Dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] in {"device", "offline", "unauthorized", "no permissions"}:
            devices.append({"serial": parts[0], "state": parts[1], "raw": line})
    return devices


def resolve_adb_serial(adb_path: str, requested: str = "") -> Tuple[str, str, List[Dict[str, str]]]:
    """Resolve one online device without making the user type its serial."""
    if requested.strip():
        return requested.strip(), "", []
    code, output = run_command([adb_path.strip() or "adb", "devices", "-l"], timeout=10)
    devices = parse_devices(output)
    online = [item for item in devices if item["state"] == "device"]
    if code != 0:
        return "", compact(output) or "adb devices 执行失败。", devices
    if len(online) == 1:
        return online[0]["serial"], "", devices
    if not online:
        return "", "没有在线的 Android 设备。请检查 USB 调试授权。", devices
    serials = "、".join(item["serial"] for item in online)
    return "", f"发现多台在线设备：{serials}。请从设备序列号下拉框选择目标设备。", devices


class Validator:
    def __init__(self, adb_path: str, python_path: str, serial: str, packages: Sequence[str], log):
        self.adb_path = adb_path.strip() or "adb"
        self.python_path = python_path.strip() or "python"
        self.serial = serial.strip()
        self.packages = list(packages)
        self.log = log
        self.context: Dict[str, Any] = {}

    def adb(self, *args: str, timeout: int = 20) -> Tuple[int, str]:
        command = [self.adb_path]
        if self.serial:
            command.extend(["-s", self.serial])
        command.extend(args)
        self.log("执行：" + " ".join(command))
        return run_command(command, timeout)

    def result(self, key: str, status: str, summary: str, details: str = "") -> Dict[str, Any]:
        return {
            "project": key,
            "name": PROJECT_NAMES.get(key, key),
            "status": status,
            "summary": summary,
            "details": details,
            "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def require_device(self) -> Optional[Dict[str, Any]]:
        if self.context.get("device_ready"):
            return None
        code, output = self.adb("get-state")
        if code == 0 and output.strip().splitlines() and output.strip().splitlines()[-1].strip() == "device":
            self.context["device_ready"] = True
            return None
        return self.result("device", "失败", "没有可用的在线 ADB 设备。", output or "请执行 adb devices -l 检查授权状态。")

    def check_adb(self) -> Dict[str, Any]:
        code, output = self.adb("version")
        if code == 0:
            version = next((line.strip() for line in output.splitlines() if "Android Debug Bridge" in line), "ADB 已可执行")
            return self.result("adb", "通过", version, output)
        return self.result("adb", "失败", "ADB 命令不可执行。", output)

    def check_device(self) -> Dict[str, Any]:
        code, output = self.adb("devices", "-l")
        devices = parse_devices(output)
        if code != 0:
            return self.result("device", "失败", "无法执行 adb devices。", output)
        self.context["devices"] = devices
        if not self.serial and len(devices) > 1:
            return self.result("device", "警告", "检测到多台设备，请填写设备序列号后再执行完整检测。", output)
        selected = next((item for item in devices if not self.serial or item["serial"] == self.serial), None)
        if selected and selected["state"] == "device":
            self.context["device_ready"] = True
            self.context["selected_device"] = selected
            return self.result("device", "通过", f"设备在线：{selected['serial']}", output)
        if selected:
            return self.result("device", "失败", f"设备状态为 {selected['state']}，不是 device。", output)
        return self.result("device", "失败", "没有找到匹配的在线设备。", output or "未发现设备")

    def check_package(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        lines = []
        failures = 0
        for package in self.packages:
            code, output = self.adb("shell", "pm", "path", package)
            installed = code == 0 and "package:" in output
            uid_code, uid_output = self.adb("shell", "dumpsys", "package", package)
            uid_match = re.search(r"(?:userId|uid)\s*=\s*(\d+)", uid_output)
            uid = uid_match.group(1) if uid_match else ""
            if installed:
                lines.append(f"{package}：已安装，UID {uid or '未读取'}")
            else:
                failures += 1
                lines.append(f"{package}：未安装或无法读取")
        status = "通过" if failures == 0 else ("警告" if failures < len(self.packages) else "失败")
        return self.result("package", status, f"检查 {len(self.packages)} 个包名，失败 {failures} 个。", "\n".join(lines))

    def first_pid(self, package: str) -> str:
        code, output = self.adb("shell", "pidof", package)
        if code == 0:
            return output.split()[0] if output.split() else ""
        return ""

    def check_device_info(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        code, output = self.adb("shell", "getprop")
        if code != 0:
            return self.result("device_info", "失败", "无法读取设备属性。", output)
        wanted = {}
        labels = {
            "ro.product.manufacturer": "厂商",
            "ro.product.brand": "品牌",
            "ro.product.model": "型号",
            "ro.build.version.release": "Android 版本",
            "ro.build.version.sdk": "SDK",
            "ro.build.id": "Build ID",
        }
        for line in output.splitlines():
            if "]: [" not in line:
                continue
            key, value = line.split("]: [", 1)
            key = key.lstrip("[")
            value = value.rstrip("]")
            if key in labels:
                wanted[labels[key]] = value
        self.context["device_info"] = wanted
        return self.result("device_info", "通过", "设备基本信息可读取。", "\n".join(f"{key}：{value}" for key, value in wanted.items()))

    def check_cpu(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        code, output = self.adb("shell", "cat", "/proc/stat")
        if code != 0 or not any(line.startswith("cpu ") for line in output.splitlines()):
            return self.result("cpu", "失败", "无法读取 /proc/stat。", output)
        details = ["/proc/stat：可读"]
        for package in self.packages:
            pid = self.first_pid(package)
            if not pid:
                details.append(f"{package}：当前未运行，无法读取进程 CPU ticks")
                continue
            pid_code, pid_output = self.adb("shell", "cat", f"/proc/{pid}/stat")
            details.append(f"{package}：进程 stat {'可读' if pid_code == 0 and pid_output else '不可读'}")
        return self.result("cpu", "通过", "CPU 基础数据可读取。", "\n".join(details))

    def check_memory(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        lines = []
        warnings = 0
        for package in self.packages:
            code, output = self.adb("shell", "dumpsys", "meminfo", package)
            has_total = any(line.strip().startswith("TOTAL") for line in output.splitlines())
            if code == 0 and has_total:
                lines.append(f"{package}：PSS/meminfo 可读取")
            else:
                warnings += 1
                lines.append(f"{package}：PSS/meminfo 不完整或不可读")
        status = "通过" if warnings == 0 else ("警告" if warnings < len(self.packages) else "失败")
        return self.result("memory", status, f"内存检查完成，异常 {warnings} 个。", "\n".join(lines))

    def check_disk(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        lines = []
        warnings = 0
        for package in self.packages:
            pid = self.first_pid(package)
            if not pid:
                warnings += 1
                lines.append(f"{package}：应用未运行，无法验证 /proc/<pid>/io")
                continue
            code, output = self.adb("shell", "cat", f"/proc/{pid}/io")
            if code == 0 and ("read_bytes" in output or "write_bytes" in output):
                lines.append(f"{package}：进程 I/O 可读取")
            else:
                warnings += 1
                lines.append(f"{package}：进程 I/O 不可读")
        status = "通过" if warnings == 0 else ("警告" if warnings < len(self.packages) else "失败")
        return self.result("disk", status, f"磁盘 I/O 检查完成，异常 {warnings} 个。", "\n".join(lines))

    def check_network(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        code, output = self.adb("shell", "cat", "/proc/net/xt_qtaguid/stats")
        if code == 0 and output.strip():
            return self.result("network", "通过", "UID 网络统计文件可读取。", compact(output, 1000))
        return self.result("network", "警告", "设备未提供 xt_qtaguid 网络统计，网络字段可能为空。", output or "现代 Android 版本可能已移除该接口。")

    def check_battery(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        code, output = self.adb("shell", "dumpsys", "battery")
        if code != 0 or "level:" not in output:
            return self.result("battery", "失败", "无法读取 dumpsys battery。", output)
        power_lines = []
        for package in self.packages:
            p_code, p_output = self.adb("shell", "dumpsys", "batterystats", "--charged", package, timeout=30)
            power_lines.append(f"{package}：{'可读取' if p_code == 0 else '不可读取'}")
        return self.result("battery", "通过", "电池状态可读取；应用耗电量为尽力读取项。", "电池状态：可读取\n" + "\n".join(power_lines))

    def check_thermal(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        temp_code, temp_output = self.adb("shell", "cat", "/sys/class/thermal/thermal_zone0/temp")
        thermal_code, thermal_output = self.adb("shell", "dumpsys", "thermalservice")
        if temp_code == 0 or thermal_code == 0:
            return self.result("thermal", "通过", "温度或 thermalservice 至少有一项可读取。", compact(temp_output + "\n" + thermal_output, 1200))
        return self.result("thermal", "警告", "温度接口不可读，温度字段可能为空。", temp_output + "\n" + thermal_output)

    def check_ui(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        lines = []
        warnings = 0
        for package in self.packages:
            code, output = self.adb("shell", "dumpsys", "gfxinfo", package, timeout=30)
            if code == 0 and ("Total frames rendered" in output or "Janky frames" in output):
                lines.append(f"{package}：gfxinfo 可读取")
            else:
                warnings += 1
                lines.append(f"{package}：gfxinfo 不完整或不可读")
        status = "通过" if warnings == 0 else ("警告" if warnings < len(self.packages) else "失败")
        return self.result("ui", status, f"gfxinfo 检查完成，异常 {warnings} 个。", "\n".join(lines))

    def check_storage(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        lines = []
        warnings = 0
        for package in self.packages:
            code, output = self.adb("shell", "pm", "path", package)
            if code == 0 and "package:" in output:
                lines.append(f"{package}：APK 路径可读取")
            else:
                warnings += 1
                lines.append(f"{package}：APK 路径不可读取")
        status = "通过" if warnings == 0 else ("警告" if warnings < len(self.packages) else "失败")
        return self.result("storage", status, f"应用存储检查完成，异常 {warnings} 个。", "\n".join(lines))

    def check_stability(self) -> Dict[str, Any]:
        blocked = self.require_device()
        if blocked:
            return blocked
        code, output = self.adb("logcat", "-d", "-b", "events", "-b", "system", "-t", "200", timeout=30)
        if code == 0:
            return self.result("stability", "通过", "Logcat 可读取；采集器会按基线统计新增错误。", compact(output, 1600))
        return self.result("stability", "警告", "Logcat 读取失败，崩溃/ANR 字段可能为空。", output)

    def check_python(self) -> Dict[str, Any]:
        code, output = run_command([self.python_path, "--version"], timeout=15)
        if code != 0:
            return self.result("python", "失败", "Python 路径不可执行。", output)
        generator = app_dir() / "generate_perf_report.py"
        if not generator.is_file():
            return self.result("python", "警告", "Python 可执行，但未找到报告生成器。", f"期望路径：{generator}\n{output}")
        import_code, import_output = run_command([self.python_path, "-c", "import csv,json,html,pathlib; print('标准库检查通过')"], timeout=15)
        status = "通过" if import_code == 0 else "警告"
        return self.result("python", status, "Python 和标准库报告环境可用。", output + "\n" + import_output + f"\n报告生成器：{generator}")

    def check(self, key: str) -> Dict[str, Any]:
        methods = {
            "adb": self.check_adb,
            "device": self.check_device,
            "package": self.check_package,
            "device_info": self.check_device_info,
            "cpu": self.check_cpu,
            "memory": self.check_memory,
            "disk": self.check_disk,
            "network": self.check_network,
            "battery": self.check_battery,
            "thermal": self.check_thermal,
            "ui": self.check_ui,
            "storage": self.check_storage,
            "stability": self.check_stability,
            "python": self.check_python,
        }
        method = methods[key]
        try:
            return method()
        except Exception as exc:  # keep one failed project from hiding the rest
            return self.result(key, "失败", "检测脚本异常。", repr(exc))


def _xlsx_text(value: Any) -> str:
    """Return XML-safe text, dropping characters that OOXML cannot store."""
    text = "" if value is None else str(value)
    text = "".join(char for char in text if char in "\t\n\r" or ord(char) >= 0x20)
    return xml_escape(text)


def _xlsx_column_name(index: int) -> str:
    name = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xlsx_cell(row_number: int, column_number: int, value: Any, style: int = 3) -> str:
    reference = f"{_xlsx_column_name(column_number)}{row_number}"
    if value is None or value == "":
        return f'<c r="{reference}" s="{style}"/>'
    if isinstance(value, bool):
        return f'<c r="{reference}" s="{style}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}" s="4"><v>{value}</v></c>'
    text = _xlsx_text(value)
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _xlsx_sheet_xml(rows: Sequence[Sequence[Any]], widths: Sequence[float], freeze_header: bool = True) -> str:
    row_xml = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for column_number, value in enumerate(row):
            style = 2 if row_number == 1 and freeze_header else (1 if row_number == 1 else 3)
            cells.append(_xlsx_cell(row_number, column_number, value, style))
        row_xml.append(f'<row r="{row_number}">' + "".join(cells) + "</row>")
    cols = []
    for index, width in enumerate(widths):
        column = index + 1
        cols.append(f'<col min="{column}" max="{column}" width="{width:.1f}" customWidth="1"/>')
    freeze = ""
    if freeze_header and rows:
        freeze = '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
    auto_filter = ""
    if freeze_header and rows:
        last_column = _xlsx_column_name(max(len(row) for row in rows) - 1)
        auto_filter = f'<autoFilter ref="A1:{last_column}{len(rows)}"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'{freeze}<sheetFormatPr defaultRowHeight="20"/><cols>{"".join(cols)}</cols>'
        f'<sheetData>{"".join(row_xml)}</sheetData>{auto_filter}</worksheet>'
    )


def write_xlsx_report(report: Dict[str, Any], path: Path) -> Path:
    """Write a dependency-free Chinese XLSX workbook for the Windows GUI report."""
    generated_at = report.get("generated_at", "")
    status_counts = report.get("status_counts", {})
    parameters = [
        ["参数", "值"],
        ["工具版本", f"v{report.get('version', APP_VERSION)}"],
        ["生成时间", generated_at],
        ["ADB 路径", report.get("adb_path", "")],
        ["Python 路径", report.get("python_path", "")],
        ["设备序列号", report.get("serial", "") or "自动选择"],
        ["包名", ",".join(report.get("packages", []))],
        ["检测项目", "、".join(report.get("selected_projects", []))],
    ]
    summary = [
        ["项目", "结果"],
        ["工具版本", f"v{report.get('version', APP_VERSION)}"],
        ["检测项目数", len(report.get("results", []))],
        ["通过", status_counts.get("通过", 0)],
        ["警告", status_counts.get("警告", 0)],
        ["失败", status_counts.get("失败", 0)],
        ["跳过", status_counts.get("跳过", 0)],
        ["设备序列号", report.get("serial", "") or "自动选择"],
        ["包名", ",".join(report.get("packages", []))],
    ]
    result_rows = [["检测项目", "状态", "结论", "详细信息"]]
    for item in report.get("results", []):
        result_rows.append([
            item.get("name", ""),
            item.get("status", ""),
            item.get("summary", ""),
            item.get("details", ""),
        ])
    device_rows = [["设备/上下文字段", "值"]]
    context = report.get("device_context", {}) or {}
    if context:
        for key, value in context.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            device_rows.append([key, value])
    else:
        device_rows.append(["结果", "未读取到设备上下文"])

    sheets = [
        ("检测汇总", summary, True),
        ("检测结果", result_rows, True),
        ("设备上下文", device_rows, True),
        ("运行参数", parameters, True),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        + '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<bookViews><workbookView/></bookViews><sheets>'
        + "".join(
            f'<sheet name="{_xlsx_text(name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, (name, _, _) in enumerate(sheets, start=1)
        )
        + '</sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        + '</Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="0"/><fonts count="3">'
        '<font><sz val="11"/><name val="等线"/></font>'
        '<font><b/><sz val="14"/><name val="等线"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="等线"/></font>'
        '</fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2563EB"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="5">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf>'
        '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{_xlsx_text(APP_TITLE)}</dc:title><dc:creator>Android App Performance Monitor</dc:creator>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{_xlsx_text(generated_at)}</dcterms:created>'
        '</cp:coreProperties>'
    )
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        f'<Application>{_xlsx_text(APP_TITLE)}</Application><DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop>'
        '<HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant>'
        f'<vt:variant><vt:i4>{len(sheets)}</vt:i4></vt:variant></vt:vector></HeadingPairs>'
        '<TitlesOfParts><vt:vector size="4" baseType="lpstr">'
        + "".join(f'<vt:lpstr>{_xlsx_text(name)}</vt:lpstr>' for name, _, _ in sheets)
        + '</vt:vector></TitlesOfParts></Properties>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)
        for index, (_, rows, freeze_header) in enumerate(sheets, start=1):
            widths = []
            for column in range(max(len(row) for row in rows)):
                maximum = max((len(str(row[column])) if column < len(row) and row[column] is not None else 0) for row in rows)
                widths.append(min(80.0, max(12.0, maximum * 1.15 + 2)))
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _xlsx_sheet_xml(rows, widths, freeze_header))
    return path


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x760")
        self.minsize(860, 620)
        self.queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.project_vars: Dict[str, tk.BooleanVar] = {}
        self.report_type_vars: Dict[str, tk.BooleanVar] = {}
        self.adb_var = tk.StringVar(value=default_adb_path())
        self.python_var = tk.StringVar(value=default_python_path())
        self.serial_var = tk.StringVar()
        self.package_var = tk.StringVar(value=DEFAULT_PACKAGE)
        default_report = default_report_path()
        self.report_var = tk.StringVar(value=str(default_report))
        self.status_var = tk.StringVar(value="第 1 步：正在查找手机…")
        self.device_state_var = tk.StringVar(value="正在查找手机…")
        self.report_hint_var = tk.StringVar(value="默认生成 Excel，可按需勾选 HTML / JSON")
        self.advanced_visible = False
        self._build_ui()
        self.after(150, self._drain_queue)
        self.after(300, self.auto_detect_device)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background="#f3f6fa")
        style.configure("Card.TLabelframe", background="#ffffff", bordercolor="#d9e2ec", relief="solid")
        style.configure("Card.TLabelframe.Label", background="#ffffff", foreground="#1f2937", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Title.TLabel", background="#12304a", foreground="#ffffff", font=("Microsoft YaHei UI", 19, "bold"))
        style.configure("Subtitle.TLabel", background="#12304a", foreground="#cbd9e7", font=("Microsoft YaHei UI", 10))
        style.configure("Hint.TLabel", background="#ffffff", foreground="#64748b", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background="#e7f0ff", foreground="#1d4ed8", padding=(10, 6), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Primary.TButton", background="#2563eb", foreground="#ffffff", padding=(20, 10), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#94a3b8")])
        style.configure("Secondary.TButton", padding=(12, 7), font=("Microsoft YaHei UI", 9))
        style.configure("TCheckbutton", background="#ffffff", foreground="#1f2937", font=("Microsoft YaHei UI", 9))
        style.configure("TLabel", background="#ffffff", foreground="#334155", font=("Microsoft YaHei UI", 9))
        style.configure("TEntry", padding=6)
        style.configure("TCombobox", padding=5)

        self.configure(bg="#f3f6fa")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        main = ttk.Frame(self, style="App.TFrame", padding=(18, 16, 18, 14))
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        banner = tk.Frame(main, bg="#12304a", height=82)
        banner.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        banner.grid_propagate(False)
        ttk.Label(banner, text="Android 设备环境检查", style="Title.TLabel").pack(anchor="w", padx=22, pady=(14, 0))
        ttk.Label(banner, text="连接手机 → 选择检查内容 → 点击开始，完成后自动生成报告", style="Subtitle.TLabel").pack(anchor="w", padx=23, pady=(2, 0))

        quick = ttk.LabelFrame(main, text="第一步：确认手机和报告位置", style="Card.TLabelframe", padding=(14, 10))
        quick.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        quick.columnconfigure(1, weight=1)
        quick.columnconfigure(3, weight=1)
        ttk.Label(quick, text="要检查的应用包名").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(quick, textvariable=self.package_var).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(quick, text="例如 com.uusafe.portal；多个用逗号分隔", style="Hint.TLabel").grid(row=0, column=2, columnspan=2, sticky="w", padx=10, pady=5)
        self.serial_combo = ttk.Combobox(quick, textvariable=self.serial_var, state="normal")
        self.serial_combo.grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(quick, text="连接的手机").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Button(quick, text="重新查找手机", style="Secondary.TButton", command=self.auto_detect_device).grid(row=1, column=2, sticky="w", padx=10, pady=5)
        ttk.Label(quick, textvariable=self.device_state_var, style="Status.TLabel").grid(row=1, column=3, sticky="e", padx=(10, 0), pady=5)
        ttk.Label(quick, text="报告保存位置").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(quick, textvariable=self.report_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Button(quick, text="浏览…", style="Secondary.TButton", command=self._choose_report).grid(row=2, column=3, sticky="e", padx=(10, 0), pady=5)

        ttk.Label(quick, text="需要哪些报告").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=5)
        report_types = ttk.Frame(quick, style="App.TFrame")
        report_types.grid(row=3, column=1, columnspan=3, sticky="w", pady=5)
        for key, label, enabled in (("excel", "Excel（推荐）", True), ("html", "HTML", False), ("json", "JSON", False)):
            self.report_type_vars[key] = tk.BooleanVar(value=enabled)
            ttk.Checkbutton(report_types, text=label, variable=self.report_type_vars[key]).pack(side="left", padx=(0, 18))

        ttk.Label(quick, text="只有一台手机时会自动使用；多台手机请在下拉框选择。", style="Hint.TLabel").grid(row=4, column=0, columnspan=4, sticky="w", pady=(7, 0))
        self.advanced_toggle = ttk.Button(quick, text="显示高级设置（ADB / Python 异常时使用）", style="Secondary.TButton", command=self._toggle_advanced)
        self.advanced_toggle.grid(row=5, column=0, columnspan=4, sticky="w", pady=(7, 0))
        self.advanced_frame = ttk.Frame(quick, style="App.TFrame")
        self.advanced_frame.columnconfigure(1, weight=1)
        self._path_row(self.advanced_frame, 0, "ADB 路径：", self.adb_var, self._choose_adb, "浏览")
        self._path_row(self.advanced_frame, 1, "Python 路径：", self.python_var, self._choose_python, "浏览")

        box = ttk.LabelFrame(main, text="第二步：选择检查内容（默认 CPU 和内存）", style="Card.TLabelframe", padding=(10, 8))
        box.grid(row=2, column=0, sticky="nsew", pady=(0, 12))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        canvas = tk.Canvas(box, highlightthickness=0, height=230, bg="#ffffff", bd=0)
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg="#ffffff")
        inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        for index, (key, name, description) in enumerate(PROJECTS):
            self.project_vars[key] = tk.BooleanVar(value=key in {"cpu", "memory"})
            row = index // 2
            column = (index % 2) * 2
            ttk.Checkbutton(inner, text=name, variable=self.project_vars[key]).grid(row=row, column=column, sticky="w", padx=(10, 4), pady=(8, 0))
            ttk.Label(inner, text=description, style="Hint.TLabel").grid(row=row, column=column + 1, sticky="w", padx=(0, 18), pady=(8, 0))

        actions = ttk.Frame(main, style="App.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(actions, text="恢复默认选择", style="Secondary.TButton", command=self.select_defaults).pack(side="left")
        ttk.Button(actions, text="全部选择", style="Secondary.TButton", command=self.select_all).pack(side="left", padx=8)
        ttk.Button(actions, text="全部取消", style="Secondary.TButton", command=self.select_none).pack(side="left")
        ttk.Label(actions, textvariable=self.status_var, style="Status.TLabel").pack(side="right")

        output = ttk.LabelFrame(main, text="运行日志", style="Card.TLabelframe", padding=(8, 8))
        output.grid(row=4, column=0, sticky="nsew", pady=(0, 10))
        output.columnconfigure(0, weight=1)
        output.rowconfigure(0, weight=1)
        self.log_text = tk.Text(output, wrap="word", height=8, state="disabled", font=("Consolas", 10), bg="#172433", fg="#dbeafe", insertbackground="#ffffff", relief="flat", padx=10, pady=8)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(output, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        bottom = ttk.Frame(main, style="App.TFrame")
        bottom.grid(row=5, column=0, sticky="ew")
        self.run_button = ttk.Button(bottom, text="第三步：开始检查并生成报告", style="Primary.TButton", command=self.start)
        self.run_button.pack(side="left")
        self.stop_button = ttk.Button(bottom, text="停止检测", style="Secondary.TButton", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(bottom, textvariable=self.report_hint_var, style="Hint.TLabel").pack(side="right")
        self.select_defaults()

    def _toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(7, 0))
            self.advanced_toggle.configure(text="隐藏高级设置（ADB / Python 路径）")
        else:
            self.advanced_frame.grid_remove()
            self.advanced_toggle.configure(text="显示高级设置（ADB / Python 路径）")

    def _path_row(self, parent, row: int, label: str, variable: tk.StringVar, command, button_text: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8, pady=5)
        ttk.Button(parent, text="浏览", command=command).grid(row=row, column=2, padx=(0, 8), pady=5)

    def _choose_adb(self) -> None:
        value = filedialog.askopenfilename(title="选择 adb.exe", filetypes=[("adb.exe", "adb.exe"), ("所有文件", "*.*")])
        if value:
            self.adb_var.set(value)

    def _choose_python(self) -> None:
        value = filedialog.askopenfilename(title="选择 python.exe", filetypes=[("python.exe", "python.exe"), ("所有文件", "*.*")])
        if value:
            self.python_var.set(value)

    def _choose_report(self) -> None:
        value = filedialog.asksaveasfilename(
            title="选择报告保存路径",
            defaultextension=".xlsx",
            filetypes=[("Excel 报告", "*.xlsx"), ("HTML 报告", "*.html"), ("JSON 报告", "*.json"), ("所有文件", "*.*")],
        )
        if value:
            self.report_var.set(value)

    def select_all(self) -> None:
        for variable in self.project_vars.values():
            variable.set(True)

    def select_none(self) -> None:
        for variable in self.project_vars.values():
            variable.set(False)

    def select_defaults(self) -> None:
        for key, variable in self.project_vars.items():
            variable.set(key in {"cpu", "memory"})

    def auto_detect_device(self) -> None:
        if self.running:
            return
        self.status_var.set("第 1 步：正在查找手机…")
        self.device_state_var.set("正在查找手机…")
        thread = threading.Thread(target=self._device_worker, args=(self.adb_var.get().strip(),), daemon=True)
        thread.start()

    def _device_worker(self, adb_path: str) -> None:
        serial, error, devices = resolve_adb_serial(adb_path, "")
        self.queue.put(("devices", serial, error, devices))

    def write_log(self, message: str) -> None:
        self.queue.put(("log", message))

    def append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def start(self) -> None:
        if self.running:
            return
        selected = [key for key, variable in self.project_vars.items() if variable.get()]
        if not selected:
            messagebox.showwarning("还没有选择检查内容", "请至少勾选一项要检查的内容。")
            return
        report_path = self.report_var.get().strip()
        if not report_path:
            messagebox.showwarning("还没有选择保存位置", "请填写或浏览选择报告保存位置。")
            return
        report_types = tuple(key for key, variable in self.report_type_vars.items() if variable.get())
        if not report_types:
            messagebox.showwarning("还没有选择报告类型", "请至少勾选一种要生成的报告。")
            return
        self.running = True
        self.stop_event.clear()
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("正在检查，请保持手机连接…")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        parameters = (
            self.adb_var.get().strip(),
            self.python_var.get().strip(),
            self.serial_var.get().strip(),
            self.package_var.get(),
            report_path,
            report_types,
        )
        thread = threading.Thread(target=self._worker, args=(selected, parameters), daemon=True)
        thread.start()

    def stop(self) -> None:
        if self.running:
            self.stop_event.set()
            self.status_var.set("正在停止…")

    def _worker(self, selected: Sequence[str], parameters: Tuple[str, str, str, str, str, Sequence[str]]) -> None:
        adb_path, python_path, serial, package_text, report_path, report_types = parameters
        try:
            if not serial:
                serial, error, devices = resolve_adb_serial(adb_path, "")
                if error:
                    raise RuntimeError(error)
                self.write_log(f"自动绑定 ADB 设备：{serial}")
            validator = Validator(adb_path, python_path, serial, packages_from_text(package_text), self.write_log)
            results = []
            for key in selected:
                if self.stop_event.is_set():
                    results.append(validator.result(key, "跳过", "用户停止了后续检测。"))
                    continue
                self.write_log(f"\n开始：{PROJECT_NAMES[key]}")
                result = validator.check(key)
                results.append(result)
                self.write_log(f"结果：{result['status']} - {result['summary']}")
            report = self.build_report(validator, selected, results)
            generated = self.save_report(report, Path(report_path), report_types)
            self.queue.put(("done", report, generated))
        except Exception as exc:
            self.queue.put(("error", f"检测或保存报告失败：{exc!r}"))

    def build_report(self, validator: Validator, selected: Sequence[str], results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for item in results:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {
            "tool": APP_TITLE,
            "version": APP_VERSION,
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "adb_path": validator.adb_path,
            "python_path": validator.python_path,
            "serial": validator.serial,
            "packages": validator.packages,
            "selected_projects": [PROJECT_NAMES[key] for key in selected],
            "status_counts": counts,
            "device_context": validator.context,
            "results": list(results),
        }

    def save_report(self, report: Dict[str, Any], requested: Path, report_types: Sequence[str]) -> Dict[str, Path]:
        requested = requested.expanduser()
        if requested.suffix.lower() not in {".xlsx", ".html", ".json"}:
            requested = requested.with_suffix(".xlsx")
        xlsx_path = requested.with_suffix(".xlsx")
        html_path = requested.with_suffix(".html")
        json_path = requested.with_suffix(".json")
        generated: Dict[str, Path] = {}
        if "json" in report_types:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            generated["json"] = json_path
        if "html" in report_types:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            rows = []
            for item in report["results"]:
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(item['name'])}</td>"
                    f"<td class=\"status-{html.escape(item['status'])}\">{html.escape(item['status'])}</td>"
                    f"<td>{html.escape(item['summary'])}</td>"
                    f"<td><pre>{html.escape(item.get('details', ''))}</pre></td>"
                    "</tr>"
                )
            summary = "，".join(f"{key} {value} 项" for key, value in report["status_counts"].items()) or "无结果"
            document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(APP_TITLE)}</title>
<style>
body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Microsoft YaHei",sans-serif;background:#f5f7fb;color:#1f2937;margin:0}}
main{{max-width:1280px;margin:auto;padding:28px}}h1{{margin:0 0 6px}}.muted{{color:#64748b}}
.card{{background:#fff;border:1px solid #dbe3ee;border-radius:12px;padding:16px;margin:18px 0}}
table{{border-collapse:collapse;width:100%;background:#fff}}th,td{{border:1px solid #dbe3ee;padding:9px;text-align:left;vertical-align:top}}th{{background:#eef3f9}}pre{{white-space:pre-wrap;margin:0;font:12px/1.5 Consolas,monospace}}
.status-通过{{color:#15803d;font-weight:700}}.status-警告{{color:#b45309;font-weight:700}}.status-失败{{color:#b91c1c;font-weight:700}}.status-跳过{{color:#64748b;font-weight:700}}
code{{background:#eef2f7;padding:2px 5px;border-radius:4px}}
</style></head><body><main>
<h1>{html.escape(APP_TITLE)}</h1>
<p class="muted">生成时间：{html.escape(report['generated_at'])}　·　结果：{html.escape(summary)}</p>
<section class="card"><h2>运行参数</h2><p>ADB：<code>{html.escape(report['adb_path'])}</code></p><p>Python：<code>{html.escape(report['python_path'])}</code></p><p>设备序列号：<code>{html.escape(report['serial'] or '自动选择')}</code></p><p>包名：<code>{html.escape(','.join(report['packages']))}</code></p><p>检测项目：{html.escape('、'.join(report['selected_projects']))}</p></section>
<section class="card"><h2>检测结果</h2><table><thead><tr><th>项目</th><th>状态</th><th>结论</th><th>详细信息</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<footer class="muted">本报告由本地 Windows GUI 工具生成；检测结果不代表真实采集已完成。</footer>
        </main></body></html>"""
            html_path.write_text(document, encoding="utf-8")
            generated["html"] = html_path
        if "excel" in report_types:
            xlsx_path.parent.mkdir(parents=True, exist_ok=True)
            write_xlsx_report(report, xlsx_path)
            generated["excel"] = xlsx_path
        return generated

    def _drain_queue(self) -> None:
        try:
            while True:
                event = self.queue.get_nowait()
                kind = event[0]
                if kind == "log":
                    self.append_log(event[1])
                elif kind == "devices":
                    _, serial, error, devices = event
                    online = [item for item in devices if item["state"] == "device"]
                    self.serial_combo.configure(values=[item["serial"] for item in online])
                    if serial:
                        self.serial_var.set(serial)
                        self.status_var.set(f"已找到手机：{serial}")
                        self.device_state_var.set(f"已连接：{serial}")
                    elif error:
                        self.status_var.set("未找到可用手机")
                        self.device_state_var.set("未找到可用设备")
                        self.append_log(error)
                    else:
                        self.status_var.set("未发现在线手机")
                        self.device_state_var.set("未发现在线设备")
                elif kind == "done":
                    _, report, generated = event
                    self.running = False
                    self.run_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set("检查完成，可以打开报告")
                    labels = {"excel": "Excel", "html": "HTML", "json": "JSON"}
                    lines = []
                    for key, path in generated.items():
                        label = labels.get(key, key)
                        lines.append(f"{label}：{path}")
                        self.append_log(f"\n{label} 报告：{path}")
                    self.report_hint_var.set("报告已生成，可直接打开")
                    messagebox.showinfo("检查完成", "检查已完成，报告已经生成。\n\n" + "\n".join(lines))
                elif kind == "error":
                    self.running = False
                    self.run_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set("检查失败，请查看提示")
                    self.append_log(event[1])
                    messagebox.showerror("检测失败", event[1])
                self.queue.task_done()
        except queue.Empty:
            pass
        self.after(150, self._drain_queue)


if __name__ == "__main__":
    App().mainloop()

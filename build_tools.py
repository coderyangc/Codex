"""Windows build helper for the two GUI executables.

The BAT files intentionally only locate Python and call this file. Keeping
path handling and PyInstaller arguments in Python avoids CMD code-page,
parenthesis and line-continuation differences on Windows installations.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
import venv


VERSION = "2.0.9"
BASE = Path(__file__).resolve().parent
DIST = BASE / "dist"
BUILD = BASE / "build"
VENV = BASE / ".build_venv"


def log_path(mode: str) -> Path:
    return BASE / ("build_performance_monitor.log" if mode == "performance" else "build_environment_validator.log")


def say(message: str) -> None:
    print(message, flush=True)


def run(command: list[str], log_file: Path) -> None:
    display = " ".join(f'"{item}"' if " " in item else item for item in command)
    say("RUN: " + display)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write("\n$ " + display + "\n")
        result = subprocess.run(command, cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        output = result.stdout or ""
        handle.write(output)
    if output:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {display}")


def required_files(mode: str) -> list[Path]:
    common = [BASE / "build_tools.py", BASE / "collector_config.conf"]
    if mode == "performance":
        return common + [BASE / "performance_monitor_gui.py", BASE / "xlsx_writer.py", BASE / "android_app_perf_monitor.sh"]
    return common + [BASE / "environment_validator_gui.py"]


def build(mode: str) -> Path:
    if mode not in {"performance", "environment"}:
        raise ValueError("mode must be performance or environment")
    log_file = log_path(mode)
    log_file.write_text(f"Android performance tools build log; version={VERSION}\n", encoding="utf-8")
    say(f"Android tools build v{VERSION}")
    say(f"Working folder: {BASE}")
    say(f"Build log: {log_file}")
    missing = [str(path) for path in required_files(mode) if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing files: " + ", ".join(missing))

    venv_python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not venv_python.exists():
        say("Creating isolated build environment...")
        venv.EnvBuilder(with_pip=True, clear=False).create(str(VENV))
    if not venv_python.exists():
        raise RuntimeError(f"Python executable was not created: {venv_python}")

    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pyinstaller"], log_file)
    if mode == "performance":
        source = BASE / "performance_monitor_gui.py"
        name = f"AndroidAppPerformanceMonitor_v{VERSION}"
        data_files = [BASE / "xlsx_writer.py", BASE / "android_app_perf_monitor.sh", BASE / "collector_config.conf"]
    else:
        source = BASE / "environment_validator_gui.py"
        name = f"AndroidDeviceEnvironmentCheck_v{VERSION}"
        data_files = []

    command = [str(venv_python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", name, "--distpath", str(DIST), "--workpath", str(BUILD), "--specpath", str(BUILD)]
    separator = os.pathsep
    for data_file in data_files:
        command.extend(["--add-data", f"{data_file}{separator}."])
    command.append(str(source))
    run(command, log_file)

    executable = DIST / (name + (".exe" if os.name == "nt" else ""))
    if not executable.exists():
        raise FileNotFoundError(f"EXE was not created: {executable}")
    if mode == "environment":
        generator = BASE / "generate_perf_report.py"
        if generator.exists():
            shutil.copy2(generator, DIST / generator.name)
        shutil.copy2(BASE / "collector_config.conf", DIST / "collector_config.conf")
    say(f"DONE: {executable}")
    return executable


def main() -> int:
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""
    try:
        build(mode)
        return 0
    except Exception as exc:
        say("ERROR: " + str(exc))
        try:
            log_file = log_path(mode if mode in {"performance", "environment"} else "performance")
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write("\nERROR: " + repr(exc) + "\n")
                traceback.print_exc(file=handle)
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

rem ASCII-only Windows command-line collector.
rem Use the Chinese GUI EXE for the novice-friendly workflow.

set "DURATION=10m"
set "INTERVAL=5"
set "PACKAGES=com.uusafe.portal"
set "LAUNCH_MODE=none"
set "SERIAL="
set "OUTPUT=android_app_perf.csv"
set "CONFIG=%~dp0collector_config.conf"
set "SCRIPT=%~dp0android_app_perf_monitor.sh"
set "REMOTE_CONFIG=/data/local/tmp/android_app_perf_monitor_config_%RANDOM%.conf"
set "ERROR_OUTPUT=%OUTPUT%.adb_error.log"

if "%~1"=="" goto run
goto parse

:parse
if "%~1"=="" goto run
if /I "%~1"=="--duration" goto get_duration
if /I "%~1"=="--interval" goto get_interval
if /I "%~1"=="--packages" goto get_packages
if /I "%~1"=="--package" goto get_packages
if /I "%~1"=="--launch" goto get_launch
if /I "%~1"=="--serial" goto get_serial
if /I "%~1"=="--output" goto get_output
if /I "%~1"=="--config" goto get_config
echo ERROR: unknown argument %~1
echo Usage: run_android_app_perf_monitor.bat [--packages package1,package2] [--duration 10m] [--interval 5] [--serial SERIAL] [--output report.csv] [--config collector_config.conf]
exit /b 2

:get_duration
if "%~2"=="" goto missing_value
set "DURATION=%~2"
shift
shift
goto parse

:get_interval
if "%~2"=="" goto missing_value
set "INTERVAL=%~2"
shift
shift
goto parse

:get_packages
if "%~2"=="" goto missing_value
set "PACKAGES=%~2"
shift
shift
goto parse

:get_launch
if "%~2"=="" goto missing_value
set "LAUNCH_MODE=%~2"
shift
shift
goto parse

:get_serial
if "%~2"=="" goto missing_value
set "SERIAL=%~2"
shift
shift
goto parse

:get_output
if "%~2"=="" goto missing_value
set "OUTPUT=%~2"
set "ERROR_OUTPUT=%OUTPUT%.adb_error.log"
shift
shift
goto parse

:get_config
if "%~2"=="" goto missing_value
set "CONFIG=%~2"
shift
shift
goto parse

:missing_value
echo ERROR: argument %~1 needs a value.
exit /b 2

:run
if not exist "%SCRIPT%" (
  echo ERROR: missing %SCRIPT%
  exit /b 2
)
if not exist "%CONFIG%" (
  echo ERROR: missing %CONFIG%
  exit /b 2
)

set "ADB_ARGS="
if defined SERIAL set "ADB_ARGS=-s "%SERIAL%""

if not defined SERIAL (
  set "AUTO_SERIAL="
  set /a AUTO_COUNT=0
  for /f "skip=1 tokens=1,2" %%A in ('adb devices 2^>nul') do if "%%B"=="device" (
    set /a AUTO_COUNT+=1
    set "AUTO_SERIAL=%%A"
  )
  if !AUTO_COUNT! EQU 1 set "SERIAL=!AUTO_SERIAL!"
  if !AUTO_COUNT! GTR 1 (
    echo ERROR: multiple online devices. Use --serial SERIAL.
    adb devices -l
    exit /b 1
  )
  if !AUTO_COUNT! EQU 0 (
    echo ERROR: no online ADB device. Check USB debugging and run adb devices -l.
    exit /b 1
  )
  set "ADB_ARGS=-s "!SERIAL!""
)

adb %ADB_ARGS% get-state >nul 2>&1
if errorlevel 1 (
  echo ERROR: ADB device is not ready.
  exit /b 1
)

echo Device: %SERIAL%
echo Packages: %PACKAGES%
echo Duration: %DURATION%
echo Interval: %INTERVAL%
echo Output: %OUTPUT%
echo Config: %CONFIG%

adb %ADB_ARGS% push "%CONFIG%" "%REMOTE_CONFIG%" >nul 2>"%ERROR_OUTPUT%"
if errorlevel 1 (
  echo ERROR: failed to push config. Details: %ERROR_OUTPUT%
  if exist "%ERROR_OUTPUT%" type "%ERROR_OUTPUT%"
  exit /b 1
)

if exist "%ERROR_OUTPUT%" del /q "%ERROR_OUTPUT%" >nul 2>&1
adb %ADB_ARGS% shell sh -s %DURATION% %INTERVAL% %PACKAGES% %LAUNCH_MODE% %REMOTE_CONFIG% < "%SCRIPT%" > "%OUTPUT%" 2> "%ERROR_OUTPUT%"
set "COLLECT_EXIT=%ERRORLEVEL%"
adb %ADB_ARGS% shell rm -f "%REMOTE_CONFIG%" >nul 2>&1

if not "%COLLECT_EXIT%"=="0" (
  echo ERROR: ADB collection failed with exit code %COLLECT_EXIT%.
  echo Details: %ERROR_OUTPUT%
  if exist "%ERROR_OUTPUT%" type "%ERROR_OUTPUT%"
  exit /b %COLLECT_EXIT%
)

if exist "%ERROR_OUTPUT%" del /q "%ERROR_OUTPUT%" >nul 2>&1
echo DONE: CSV saved to %CD%\%OUTPUT%
exit /b 0


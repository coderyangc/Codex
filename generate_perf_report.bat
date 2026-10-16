@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Generate an offline HTML report from collector CSV output.
rem 工具版本：v2.0.9
rem Example:
rem   generate_perf_report.bat --input portal_30m.csv --output portal_30m_report

if "%~1"=="" goto menu
set "REPORT_ARGS=%*"
goto execute

:menu
cls
echo ==================================================
echo        安卓应用性能报告生成工具
echo ==================================================
echo [1] 生成中文 HTML 性能报告
echo [0] 退出
echo.
set "MENU_CHOICE="
set /p "MENU_CHOICE=请输入菜单编号："
if "%MENU_CHOICE%"=="0" exit /b 0
if not "%MENU_CHOICE%"=="1" (
  echo 请输入 1 或 0。
  pause
  goto menu
)

set "REPORT_INPUT="
set /p "REPORT_INPUT=请输入采集 CSV 文件路径："
if not defined REPORT_INPUT (
  echo CSV 文件路径不能为空。
  pause
  goto menu
)
set "REPORT_OUTPUT="
set /p "REPORT_OUTPUT=请输入报告目录（回车则自动生成）："
set "REPORT_ARGS=--input "%REPORT_INPUT%""
if defined REPORT_OUTPUT set "REPORT_ARGS=%REPORT_ARGS% --output "%REPORT_OUTPUT%""

:execute
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%~dp0generate_perf_report.py" %REPORT_ARGS%
  exit /b %errorlevel%
)

where python >nul 2>&1
if not errorlevel 1 (
  python "%~dp0generate_perf_report.py" %REPORT_ARGS%
  exit /b %errorlevel%
)

echo 找不到 Python 3。请安装 Python 3，或只使用 CSV 采集功能。
exit /b 1

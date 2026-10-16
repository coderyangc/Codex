@echo off
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 goto use_py
where python >nul 2>&1
if not errorlevel 1 goto use_python
echo ERROR: Python 3 was not found.
echo Install Python 3 and enable Add Python to PATH.
pause
exit /b 1

:use_py
py -3 "%~dp0build_tools.py" environment
set "RC=%ERRORLEVEL%"
goto finish

:use_python
python "%~dp0build_tools.py" environment
set "RC=%ERRORLEVEL%"

:finish
echo.
if "%RC%"=="0" (
  echo Build completed. Check the dist folder.
) else (
  echo Build failed. Check build_environment_validator.log.
)
pause
exit /b %RC%


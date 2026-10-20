@echo off
setlocal
cd /d "%~dp0"
title PRC Result Alarm
py -3 -c "import sys, tkinter; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 goto run_py
python -c "import sys, tkinter; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 goto run_python
echo Python 3.10 or newer with tkinter is needed to run this checker.
echo Get a standard Windows Python installation from:
echo https://www.python.org/downloads/windows/
echo Then close this window and double-click START_ALARM.cmd again.
echo If you use the classic installer, keep Tcl/Tk selected.
pause
exit /b 1

:run_py
py -3 "%~dp0prc_result_alarm.py"
goto finished

:run_python
python "%~dp0prc_result_alarm.py"

:finished
if not errorlevel 1 exit /b 0
echo.
echo The checker stopped with an error. The message above may help diagnose it.
pause

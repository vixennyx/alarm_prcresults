@echo off
setlocal
cd /d "%~dp0"
if exist ".venv-build\Scripts\python.exe" goto build
py -3 -m venv .venv-build
if errorlevel 1 goto failed

:build
".venv-build\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto failed
".venv-build\Scripts\python.exe" -m unittest -q
if errorlevel 1 goto failed
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --noupx --name PRCResultAlarm --distpath dist --workpath build --specpath build prc_result_alarm.py
if errorlevel 1 goto failed
echo.
echo Ready: dist\PRCResultAlarm.exe
exit /b 0

:failed
echo Build failed. Check the error above.
exit /b 1

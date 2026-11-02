@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher not found. Install Python 3.11 or 3.12 from python.org and tick "Add Python to PATH".
  pause
  exit /b 1
)
py -3.12 -m venv .venv
if errorlevel 1 py -3.11 -m venv .venv
if errorlevel 1 py -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation failed. Copy the error and send it to ChatGPT.
  pause
  exit /b 1
)
echo.
echo Setup complete. Double-click run.bat to launch the scanner.
pause

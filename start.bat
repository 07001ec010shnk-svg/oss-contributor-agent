@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First-time setup. This takes a few minutes, please wait...
  py -m venv .venv
  if errorlevel 1 (
    echo Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/ and tick "Add to PATH".
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)

if not exist "api_keys.env" (
  copy "api_keys.env.example" "api_keys.env" >nul
  echo A settings file will open. Paste your GITHUB_TOKEN and your AI key, save it, then close it.
  notepad "api_keys.env"
)

".venv\Scripts\python.exe" main.py go
echo.
pause

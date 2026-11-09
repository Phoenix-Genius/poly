@echo off
rem One-click launcher for Windows: sets up Python the first time, then opens the bot's web page.
rem Double-click this file, or run "start.bat" in Command Prompt / PowerShell.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

rem Supported: 64-bit Python 3.11-3.14. Polymarket's SDK needs 3.11+, and one of its dependencies (ckzg) ships
rem prebuilt packages only up to 3.14; on newer or 32-bit Python pip tries to compile it and fails.
set "OKPY=import sys, struct; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) and struct.calcsize('P') == 8 else 1)"

if not exist ".venv\Scripts\python.exe" goto findpy
".venv\Scripts\python.exe" -c "%OKPY%" >nul 2>&1 && goto install
echo The Python environment was made with an unsupported Python; rebuilding it...

:findpy
set "PY="
for %%V in (3.13 3.12 3.14 3.11 3.10) do (
  if not defined PY py -%%V -c "%OKPY%" >nul 2>&1 && set "PY=py -%%V"
)
if not defined PY python -c "%OKPY%" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "%OKPY%" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  echo No supported Python found ^(need 64-bit Python 3.11 to 3.14^).
  echo Install Python 3.13 from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo then run start.bat again.
  pause
  exit /b 1
)

echo First run: setting up Python environment with %PY% ...
if exist ".venv" rmdir /s /q ".venv"
%PY% -m venv .venv || (echo Could not create the Python environment. & pause & exit /b 1)

:install
if not exist ".venv\.installed" (
  ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || (echo Installing requirements failed. & pause & exit /b 1)
  type nul > ".venv\.installed"
)

".venv\Scripts\python.exe" -m poly_bot web %*
pause

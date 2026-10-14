@echo off
rem print3d wrapper for cmd/PowerShell: runs print3d.py with the print3d venv's Python.
setlocal
set "PY=%LOCALAPPDATA%\print3d\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo {"ok": false, "error": {"stage": "usage", "type": "SetupError", "message": "print3d is not installed: run skills/print3d/scripts/setup.ps1 first", "line": null, "traceback": []}}
  exit /b 2
)
"%PY%" "%~dp0print3d.py" %*
exit /b %ERRORLEVEL%

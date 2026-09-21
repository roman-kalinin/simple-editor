@echo off
REM Start the simple video editor. Tries python, then py launcher.
cd /d "%~dp0"
where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python server.py
  goto :eof
)
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 server.py
  goto :eof
)
echo Python 3 was not found. Install from https://www.python.org/downloads/
pause

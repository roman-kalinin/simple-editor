@echo off
REM Build the standalone Simple Video Editor app AND its Windows installer.
REM Double-click this file. It will:
REM   1. Check for Python.
REM   2. Download ffmpeg + ffprobe if not already next to this script.
REM   3. Install PyInstaller + pywebview.
REM   4. Build dist\SimpleEditor.exe (standalone app window, self-contained).
REM   5. Compile dist\SimpleEditor-Setup.exe (the installer) with Inno Setup.
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ==============================================================
echo  Building Simple Video Editor (app + installer)
echo ==============================================================

REM ---- 1. Locate Python. ----
where python >nul 2>nul
if not errorlevel 1 (
  set "PY=python"
  goto :pyok
)
where py >nul 2>nul
if not errorlevel 1 (
  set "PY=py -3"
  goto :pyok
)
echo.
echo ERROR: Python 3 is not installed (or not on PATH^).
echo Install it from https://www.python.org/downloads/ and re-run.
echo During install, tick "Add python.exe to PATH".
echo.
pause
exit /b 1
:pyok
echo [1/5] Using Python: %PY%

REM ---- 2. Fetch ffmpeg if missing. ----
if exist "ffmpeg.exe" if exist "ffprobe.exe" (
  echo [2/5] ffmpeg.exe + ffprobe.exe already present, skipping download.
  goto :ffmpegdone
)
echo [2/5] Downloading ffmpeg essentials from gyan.dev...
set "ZIP=%TEMP%\ffmpeg-essentials.zip"
set "EX=%TEMP%\ffmpeg-essentials"
if exist "%ZIP%" del "%ZIP%"
if exist "%EX%" rmdir /s /q "%EX%"
curl -L --fail --retry 3 -o "%ZIP%" ^
  "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
if errorlevel 1 (
  echo ERROR: ffmpeg download failed. Grab the zip manually from
  echo https://www.gyan.dev/ffmpeg/builds/ and extract ffmpeg.exe +
  echo ffprobe.exe next to this script, then re-run.
  pause
  exit /b 1
)
powershell -NoProfile -Command ^
  "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%EX%' -Force" || goto :unzipfail
set "FOUND=0"
for /d %%D in ("%EX%\*") do (
  if exist "%%D\bin\ffmpeg.exe" (
    copy /y "%%D\bin\ffmpeg.exe"  "%~dp0ffmpeg.exe"  >nul
    copy /y "%%D\bin\ffprobe.exe" "%~dp0ffprobe.exe" >nul
    set "FOUND=1"
  )
)
if "!FOUND!"=="0" (
  echo ERROR: Couldn't find ffmpeg.exe inside the downloaded zip.
  pause
  exit /b 1
)
del "%ZIP%" >nul 2>nul
rmdir /s /q "%EX%" >nul 2>nul
echo      ffmpeg.exe + ffprobe.exe ready.
goto :ffmpegdone
:unzipfail
echo ERROR: Failed to extract ffmpeg zip.
pause
exit /b 1
:ffmpegdone

REM ---- 3. Install build deps. ----
echo [3/5] Installing PyInstaller + pywebview...
%PY% -m pip install --upgrade --quiet pip pyinstaller pyinstaller-hooks-contrib pywebview pillow || (
  echo ERROR: pip install failed.
  pause
  exit /b 1
)

REM Regenerate the app icon if missing.
if not exist "app.ico" (
  echo      Generating app icon...
  %PY% make_icon.py
)

REM ---- 4. Build the app. ----
echo [4/5] Building standalone app with PyInstaller...
%PY% -m PyInstaller --noconfirm SimpleEditor.spec || (
  echo ERROR: PyInstaller build failed. See output above.
  pause
  exit /b 1
)

REM ---- 5. Compile the installer. ----
echo [5/5] Compiling installer with Inno Setup...
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if "!ISCC!"=="" (
  echo.
  echo Inno Setup not found. Install it with:
  echo     winget install JRSoftware.InnoSetup
  echo ...then re-run this script. The standalone app is already built at
  echo     dist\SimpleEditor.exe
  pause
  exit /b 1
)
"!ISCC!" installer.iss || (
  echo ERROR: Inno Setup compile failed.
  pause
  exit /b 1
)

echo.
echo ==============================================================
echo  Build complete.
echo  Standalone app:  %~dp0dist\SimpleEditor.exe
echo  Installer:       %~dp0dist\SimpleEditor-Setup.exe
echo ==============================================================
echo.
pause

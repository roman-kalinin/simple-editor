@echo off
REM Build SimpleEditor.exe for Windows.
REM Double-click this file. It will:
REM   1. Check for Python (guide install if missing).
REM   2. Download ffmpeg + ffprobe if not already next to this script.
REM   3. Install PyInstaller.
REM   4. Build dist\SimpleEditor.exe (~150 MB, self-contained).
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ==============================================================
echo  Building SimpleEditor.exe
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
echo Install it from https://www.python.org/downloads/ and re-run this script.
echo During install, tick "Add python.exe to PATH".
echo.
pause
exit /b 1
:pyok
echo [1/4] Using Python: %PY%

REM ---- 2. Fetch ffmpeg if missing. ----
if exist "ffmpeg.exe" if exist "ffprobe.exe" (
  echo [2/4] ffmpeg.exe + ffprobe.exe already present, skipping download.
  goto :ffmpegdone
)
echo [2/4] Downloading ffmpeg essentials from gyan.dev...
set "ZIP=%TEMP%\ffmpeg-essentials.zip"
set "EX=%TEMP%\ffmpeg-essentials"
if exist "%ZIP%" del "%ZIP%"
if exist "%EX%" rmdir /s /q "%EX%"

curl -L --fail --retry 3 -o "%ZIP%" ^
  "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
if errorlevel 1 (
  echo ERROR: Download failed. Check your network, or grab the zip manually
  echo from https://www.gyan.dev/ffmpeg/builds/ and extract ffmpeg.exe +
  echo ffprobe.exe next to this script, then re-run.
  pause
  exit /b 1
)

echo      Extracting...
powershell -NoProfile -Command ^
  "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%EX%' -Force" || goto :unzipfail

REM The zip has a versioned top folder like ffmpeg-7.1-essentials_build\bin\*.
REM Find it and copy the two binaries out.
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

REM ---- 3. Install PyInstaller. ----
echo [3/4] Installing PyInstaller...
%PY% -m pip install --upgrade --quiet pip pyinstaller || (
  echo ERROR: pip install failed.
  pause
  exit /b 1
)

REM ---- 4. Build. ----
echo [4/4] Running PyInstaller...
%PY% -m PyInstaller --noconfirm SimpleEditor.spec || (
  echo ERROR: PyInstaller build failed. See output above.
  pause
  exit /b 1
)

echo.
echo ==============================================================
echo  Build complete.
echo  Output: %~dp0dist\SimpleEditor.exe
echo ==============================================================
echo.
pause

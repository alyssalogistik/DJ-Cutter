@echo off
setlocal enabledelayedexpansion
title DJ-Cutter - Ffonkot 003

rem ---- edit these two lines if the folders ever move --------------------
set "SRC=C:\Users\User\Downloads\DJ-Cutter\Ffonkot 003"
set "DST=C:\Users\User\Downloads\DJ-Cutter\Ffonkot 003 FINAL"
rem ----------------------------------------------------------------------

echo.
echo   SOURCE : %SRC%
echo   OUTPUT : %DST%
echo.

if not exist "%SRC%" (
  echo ERROR: source folder not found:
  echo   %SRC%
  echo Fix the SRC line in this file, or move the folder there.
  pause & exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python is not on PATH.
  echo Install it from https://www.python.org/downloads/windows/
  echo and tick "Add python.exe to PATH" during setup.
  pause & exit /b 1
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo ERROR: FFmpeg is not on PATH.
  echo Install it with:  winget install Gyan.FFmpeg
  echo then close and reopen this window.
  pause & exit /b 1
)

echo Checking Python packages ^(first run downloads them, a few minutes^)...
python -m pip install --quiet --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo ERROR: could not install the Python packages.
  pause & exit /b 1
)

echo.
python "%~dp0dj_cutter.py" "%SRC%" "%DST%"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo Processing failed with exit code %RC%. Nothing in the source folder was changed.
  pause & exit /b %RC%
)

echo.
echo Opening the result folder...
start "" explorer "%DST%"
pause

@echo off
setlocal enabledelayedexpansion
title DJ-Cutter FINAL V1

rem ---- ubah dua baris ini kalau foldernya pindah -----------------------
set "SRC=%USERPROFILE%\Downloads\DJ CUTTER"
set "REF=%USERPROFILE%\Downloads\DJ CUTTER\CLASSIC_4_FINAL_V4.mp3"
set "DST=%USERPROFILE%\Downloads\DJ_CUTTER_FINAL_V1"
rem ----------------------------------------------------------------------

echo.
echo   SUMBER : %SRC%
echo   ACUAN  : %REF%
echo   HASIL  : %DST%
echo.

if not exist "%SRC%" (
  echo ERROR: folder sumber tidak ditemukan:
  echo   %SRC%
  echo Perbaiki baris SRC di berkas .bat ini.
  pause & exit /b 1
)

set CNT=0
for %%f in ("%SRC%\*.mp3") do set /a CNT+=1
echo   Ditemukan !CNT! berkas MP3 di folder sumber.
if !CNT!==0 ( echo ERROR: tidak ada MP3 di folder sumber. & pause & exit /b 1 )

if not exist "%REF%" (
  echo.
  echo PERINGATAN: berkas acuan tidak ditemukan:
  echo   %REF%
  echo Proses tetap bisa jalan tanpa acuan, tapi karakter tonal
  echo tidak akan dicocokkan ke CLASSIC_4_FINAL_V4.
  echo.
  choice /C YN /M "Lanjut tanpa acuan"
  if errorlevel 2 ( exit /b 1 )
  set "REFARG="
) else (
  set "REFARG=--ref "%REF%""
)

where python >nul 2>&1 || (
  echo ERROR: Python tidak ada di PATH.
  echo Pasang dari https://www.python.org/downloads/windows/
  echo dan centang "Add python.exe to PATH".
  pause & exit /b 1
)
where ffmpeg >nul 2>&1 || (
  echo ERROR: FFmpeg tidak ada di PATH.
  echo Pasang dengan:  winget install Gyan.FFmpeg
  echo lalu tutup dan buka ulang jendela ini.
  pause & exit /b 1
)

echo.
echo Memeriksa paket Python ^(unduhan pertama butuh beberapa menit^)...
python -m pip install --quiet --disable-pip-version-check -r "%~dp0requirements.txt" || (
  echo ERROR: paket Python gagal dipasang.
  pause & exit /b 1
)

echo.
python "%~dp0dj_final.py" !REFARG! "%SRC%" "%DST%"
set "RC=!ERRORLEVEL!"

if not "!RC!"=="0" (
  echo.
  echo Proses gagal, kode !RC!. Folder sumber tidak diubah sama sekali.
  pause & exit /b !RC!
)

set OUT=0
for %%f in ("%DST%\*.mp3") do set /a OUT+=1
echo.
echo   Sumber : !CNT! berkas
echo   Hasil  : !OUT! berkas
if not "!CNT!"=="!OUT!" ( echo   PERINGATAN: jumlahnya tidak sama, periksa _QC_REPORT.txt )
echo.
echo Membuka folder hasil...
start "" explorer "%DST%"
pause

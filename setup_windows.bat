@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title OCR2Word - Cai dat
echo ============================================================
echo  OCR2Word - Cai dat moi truong (Windows 10/11)
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [LOI] Chua cai Python. Tai Python 3.10+ tai https://www.python.org/downloads/windows/
    echo       Khi cai nho tick "Add python.exe to PATH".
    pause & exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" || (
    echo [LOI] Can Python 3.9 tro len. & pause & exit /b 1
)

echo [1/3] Cai thu vien Python...
python -m pip install --upgrade pip >nul
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 ( echo [LOI] Cai thu vien that bai. & pause & exit /b 1 )

echo.
echo [2/3] Kiem tra Tesseract-OCR...
set "TESS="
if exist "%~dp0tesseract\tesseract.exe" set "TESS=%~dp0tesseract\tesseract.exe"
if not defined TESS if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESS=%ProgramFiles%\Tesseract-OCR\tesseract.exe"
if not defined TESS if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" set "TESS=%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"
if not defined TESS if exist "%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe" set "TESS=%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"
if not defined TESS (
    for /f "delims=" %%p in ('where tesseract 2^>nul') do if not defined TESS set "TESS=%%p"
)
if not defined TESS (
    echo [!] Chua cai Tesseract-OCR.
    echo     Tai ban cai Windows tai: https://github.com/UB-Mannheim/tesseract/wiki
    echo     Khi cai, o muc "Additional language data" tick: Vietnamese, Russian, English
    echo     Sau do chay lai file nay.
    set /p OPEN="Mo trang tai ngay? (y/n): "
    if /i "!OPEN!"=="y" start https://github.com/UB-Mannheim/tesseract/wiki
    pause & exit /b 1
)
echo     Tim thay: !TESS!

echo.
echo [3/3] Kiem tra du lieu ngon ngu (vie, eng, rus)...
for %%d in ("!TESS!") do set "TESSDIR=%%~dpd"
set "TESSDATA=!TESSDIR!tessdata"
if defined TESSDATA_PREFIX if exist "%TESSDATA_PREFIX%" set "TESSDATA=%TESSDATA_PREFIX%"
for %%L in (vie eng rus) do (
    if not exist "!TESSDATA!\%%L.traineddata" (
        echo     Dang tai %%L.traineddata ...
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
          "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri 'https://github.com/tesseract-ocr/tessdata_best/raw/main/%%L.traineddata' -OutFile '!TESSDATA!\%%L.traineddata'"
        if errorlevel 1 (
            echo     [!] Khong tai duoc %%L.traineddata. Neu bi loi quyen ghi, chay file nay bang "Run as administrator".
        )
    ) else (
        echo     %%L: OK
    )
)

echo.
python -m ocr_tool --tesseract "!TESS!" --check
echo.
echo Cai dat xong. Chay run_gui.bat de mo chuong trinh.
pause

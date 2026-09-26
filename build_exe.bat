@echo off
chcp 65001 >nul
cd /d "%~dp0"
title OCR2Word - Dong goi .exe
echo [1/3] Cai PyInstaller...
python -m pip install -r requirements.txt pyinstaller >nul || (echo [LOI] Cai thu vien that bai & pause & exit /b 1)

echo [2/3] Dong goi bang PyInstaller (dist\OCR2Word\)...
python -m PyInstaller --noconfirm --clean OCR2Word.spec || (echo [LOI] PyInstaller that bai & pause & exit /b 1)

echo [3/3] Kem Tesseract vao ban dong goi (neu co)...
set "SRC="
if exist "%~dp0tesseract\tesseract.exe" set "SRC=%~dp0tesseract"
if not defined SRC if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "SRC=%ProgramFiles%\Tesseract-OCR"
if not defined SRC if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" set "SRC=%LOCALAPPDATA%\Programs\Tesseract-OCR"
if defined SRC (
    xcopy /E /I /Y /Q "%SRC%" "dist\OCR2Word\tesseract" >nul
    echo     Da chep Tesseract tu: %SRC%
) else (
    echo     [!] Khong tim thay Tesseract-OCR - ban dong goi se can may dich tu cai Tesseract.
)

echo.
echo Ket qua: dist\OCR2Word\OCR2Word.exe  (giao dien)  va  dist\OCR2Word\ocr2word-cli.exe  (dong lenh)
echo.
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
    echo Tao bo cai bang Inno Setup...
    for /f "delims=" %%v in ('python -c "import ocr_tool; print(ocr_tool.__version__)"') do set "VER=%%v"
    "%ISCC%" /DMyAppVersion=%VER% installer\OCR2Word.iss && echo Bo cai: dist\installer\
) else (
    echo De tao bo cai .exe, cai Inno Setup 6 (https://jrsoftware.org/isdl.php) roi chay lai file nay.
)
pause

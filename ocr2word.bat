@echo off
chcp 65001 >nul
setlocal
if exist "%~dp0tesseract\tesseract.exe" set "TESSERACT_CMD=%~dp0tesseract\tesseract.exe"
rem chay duoc tu thu muc bat ky (duong dan anh tuong doi van tinh theo thu muc hien tai)
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m ocr_tool %*

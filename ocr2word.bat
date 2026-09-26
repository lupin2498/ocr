@echo off
chcp 65001 >nul
if exist "%~dp0tesseract\tesseract.exe" set "TESSERACT_CMD=%~dp0tesseract\tesseract.exe"
python -m ocr_tool %*

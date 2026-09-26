@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0tesseract\tesseract.exe" set "TESSERACT_CMD=%~dp0tesseract\tesseract.exe"
start "" pythonw -m ocr_tool
if errorlevel 1 python -m ocr_tool

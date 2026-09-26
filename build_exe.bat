@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Dong goi OCR2Word.exe bang PyInstaller...
python -m pip install pyinstaller >nul
python -m PyInstaller --noconfirm --clean --onefile --windowed --name OCR2Word ^
  --collect-all pytesseract --collect-all docx --collect-all pymupdf ^
  --hidden-import ocr_tool.gui --hidden-import ocr_tool.cli ^
  run_ocr2word.py
echo.
echo File ket qua: dist\OCR2Word.exe
echo Luu y: may chay van can cai Tesseract-OCR, hoac copy thu muc "tesseract" (chua tesseract.exe va tessdata)
echo        dat canh OCR2Word.exe de chay dang portable.
pause

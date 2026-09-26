# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller: tạo thư mục dist/OCR2Word chứa OCR2Word.exe (giao diện) và ocr2word-cli.exe (dòng lệnh).
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("docx") + [("assets/icon.ico", "assets")]
hiddenimports = (
    ["ocr_tool", "ocr_tool.gui", "ocr_tool.cli", "pymupdf", "fitz"]
    + collect_submodules("docx")
    + collect_submodules("ocr_tool")
)

a = Analysis(
    ["run_ocr2word.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pandas", "IPython", "PyQt5", "PyQt6", "PySide2", "PySide6", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OCR2Word",
    icon="assets/icon.ico",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ocr2word-cli",
    icon="assets/icon.ico",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OCR2Word",
)

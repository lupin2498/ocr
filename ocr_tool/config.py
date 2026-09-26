"""Tìm Tesseract trên Windows / Linux, kiểm tra dữ liệu ngôn ngữ."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_best/raw/main/{lang}.traineddata"


def app_dir() -> Path:
    """Thư mục chứa chương trình (kể cả khi đóng gói PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def candidate_tesseract_paths() -> List[Path]:
    cands: List[Path] = []
    env = os.environ.get("TESSERACT_CMD")
    if env:
        cands.append(Path(env))
    # Bản portable đặt cạnh chương trình
    cands.append(app_dir() / "tesseract" / "tesseract.exe")
    cands.append(app_dir() / "tesseract" / "tesseract")
    if os.name == "nt":
        for base in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            if base:
                cands.append(Path(base) / "Tesseract-OCR" / "tesseract.exe")
                cands.append(Path(base) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    which = shutil.which("tesseract")
    if which:
        cands.append(Path(which))
    return cands


def find_tesseract() -> Optional[str]:
    for p in candidate_tesseract_paths():
        if p.is_file():
            return str(p)
    return None


def tessdata_dir_for(tesseract_cmd: str) -> Optional[Path]:
    env = os.environ.get("TESSDATA_PREFIX")
    if env and Path(env).is_dir():
        return Path(env)
    base = Path(tesseract_cmd).resolve().parent
    for cand in (base / "tessdata", base.parent / "share" / "tessdata",
                 base.parent / "share" / "tesseract-ocr" / "5" / "tessdata",
                 base.parent / "share" / "tesseract-ocr" / "4.00" / "tessdata"):
        if cand.is_dir():
            return cand
    return None


def list_languages(tesseract_cmd: str, tessdata_dir: Optional[str] = None) -> List[str]:
    cmd = [tesseract_cmd, "--list-langs"]
    if tessdata_dir:
        cmd += ["--tessdata-dir", tessdata_dir]
    try:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return []
    langs = []
    for line in (out.stdout + out.stderr).splitlines():
        line = line.strip()
        if line and " " not in line and not line.startswith("List"):
            langs.append(line)
    return langs


def download_traineddata(lang: str, dest_dir: Path, log=print) -> Path:
    """Tải file traineddata (bản tessdata_best) nếu chưa có."""
    import urllib.request

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{lang}.traineddata"
    if dest.exists():
        return dest
    url = TESSDATA_URL.format(lang=lang)
    log(f"Đang tải {url} ...")
    tmp = dest.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    log(f"Đã lưu {dest}")
    return dest

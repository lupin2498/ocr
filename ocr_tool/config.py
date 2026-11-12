"""Tìm Tesseract trên Windows / Linux, kiểm tra dữ liệu ngôn ngữ."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

log = logging.getLogger(__name__)

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


def _tessdata_candidates(tesseract_cmd: Optional[str], explicit: Optional[str] = None) -> List[Path]:
    cands: List[Path] = []
    if explicit:
        cands.append(Path(explicit))
    if tesseract_cmd:
        try:
            base = Path(tesseract_cmd).resolve().parent
        except OSError:
            base = Path(tesseract_cmd).parent
        # dữ liệu đi kèm chính tesseract.exe này (bản đóng gói) đứng trước biến môi trường TESSDATA_PREFIX:
        # máy từng cài Tesseract khác thường còn TESSDATA_PREFIX trỏ sang tessdata cũ / thiếu tiếng Việt
        cands.append(base / "tessdata")
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        cands += [Path(env), Path(env) / "tessdata"]
    if tesseract_cmd:
        cands += [base.parent / "share" / "tessdata",
                  base.parent / "share" / "tesseract-ocr" / "5" / "tessdata",
                  base.parent / "share" / "tesseract-ocr" / "4.00" / "tessdata"]
    cands += [Path("/usr/share/tesseract-ocr/5/tessdata"), Path("/usr/share/tesseract-ocr/4.00/tessdata"),
              Path("/usr/share/tessdata"), Path("/usr/local/share/tessdata"), Path("/opt/homebrew/share/tessdata")]
    return cands


def resolve_tessdata(tesseract_cmd: Optional[str], explicit: Optional[str] = None,
                     langs: Sequence[str] = ()) -> Optional[Path]:
    """Thư mục tessdata dùng cho OCR: thư mục đầu tiên có đủ dữ liệu các ngôn ngữ cần."""
    existing: List[Path] = []
    for c in _tessdata_candidates(tesseract_cmd, explicit):
        try:
            if c.is_dir() and c not in existing:
                existing.append(c)
        except OSError:
            continue
    for c in existing:
        if all((c / f"{lang}.traineddata").is_file() for lang in langs):
            return c
    return existing[0] if existing else None


def tessdata_dir_for(tesseract_cmd: str) -> Optional[Path]:
    return resolve_tessdata(tesseract_cmd, langs=("vie", "eng", "rus"))


def ascii_path(path: str) -> str:
    """Windows: Tesseract nhận tham số dòng lệnh / đường dẫn theo bảng mã ANSI, nên đường dẫn có dấu
    (C:\\Users\\Nguyễn\\...) bị hỏng. Dùng tên ngắn 8.3 (toàn ASCII) nếu có."""
    if os.name != "nt" or not path or path.isascii():
        return path
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 32768)  # type: ignore[attr-defined]
        if 0 < n < 32768 and buf.value.isascii():
            return buf.value
    except Exception:  # noqa: BLE001
        pass
    return path


def ensure_ascii_tempdir() -> None:
    """Windows: pytesseract ghi ảnh tạm vào %TEMP% rồi đưa đường dẫn cho tesseract.exe – nếu tên người dùng có
    dấu (C:\\Users\\Nguyễn\\AppData\\Local\\Temp) Tesseract không mở được file. Chuyển sang thư mục tạm ASCII."""
    if os.name != "nt":
        return
    import tempfile

    cur = tempfile.gettempdir()
    if cur.isascii():
        return
    cands = [ascii_path(cur)]
    for base in (os.environ.get("ProgramData"), os.environ.get("PUBLIC"), os.environ.get("SystemDrive", "C:") + "\\"):
        if base:
            cands.append(os.path.join(base, "OCR2Word", "tmp") if not base.endswith("\\") else base + "OCR2WordTemp")
    for c in cands:
        if not c or not c.isascii():
            continue
        try:
            os.makedirs(c, exist_ok=True)
            test = os.path.join(c, f"ocr2word_{os.getpid()}.tmp")
            with open(test, "wb") as f:
                f.write(b"ok")
            os.remove(test)
        except OSError:
            continue
        tempfile.tempdir = c
        os.environ["TMP"] = os.environ["TEMP"] = c  # tiến trình con (OCR song song) dùng cùng thư mục
        return


def _copy_if_changed(src: Path, dst: Path) -> None:
    st = src.stat()
    try:
        dt = dst.stat()
        if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
            return
    except FileNotFoundError:
        pass
    tmp = dst.with_name(f"{dst.name}.{os.getpid()}.part")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)  # thay nguyên khối: hai chương trình chạy cùng lúc không đọc phải file chép dở


def _ascii_tessdata_copy(td: Path, langs: Sequence[str]) -> Optional[str]:
    """Chép dữ liệu các ngôn ngữ cần dùng (và thư mục configs: Tesseract cần configs/tsv) sang thư mục không dấu."""
    files = [td / f"{lang}.traineddata" for lang in langs]
    if not files or not all(f.is_file() for f in files):
        return None
    system = (os.environ.get("SystemDrive") or "C:") + "\\"
    dests = [Path(b) / "OCR2Word" / "tessdata" for b in (os.environ.get("ProgramData"), os.environ.get("PUBLIC")) if b]
    dests.append(Path(system) / "OCR2WordData" / "tessdata")
    for dest in dests:
        if not str(dest).isascii():
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
            for f in files:
                _copy_if_changed(f, dest / f.name)
            if (td / "configs").is_dir():
                (dest / "configs").mkdir(exist_ok=True)
                for f in (td / "configs").iterdir():
                    if f.is_file():
                        _copy_if_changed(f, dest / "configs" / f.name)
        except OSError as e:
            log.warning("không chép được tessdata sang %s: %s", dest, e)
            continue
        log.info("tessdata nằm ở đường dẫn có dấu – dùng bản chép tại %s", dest)
        return str(dest)
    return None


def usable_tessdata(td: Path, langs: Sequence[str] = ()) -> str:
    """Đường dẫn tessdata mà tesseract.exe mở được. Windows: Tesseract đọc đường dẫn theo bảng mã ANSI nên thư
    mục có dấu (cài cho riêng người dùng: C:\\Users\\Nguyễn Văn A\\AppData\\Local\\Programs\\...) bị hỏng. Dùng
    tên ngắn 8.3; ổ đĩa tắt tên 8.3 thì chép dữ liệu ngôn ngữ cần dùng sang thư mục không dấu (một lần)."""
    p = ascii_path(str(td))
    if os.name != "nt" or p.isascii():
        return p
    return _ascii_tessdata_copy(td, langs) or p


def configure_tesseract(tesseract_cmd: Optional[str], tessdata: Optional[str] = None,
                        langs: Sequence[str] = ()) -> Optional[str]:
    """Đặt TESSDATA_PREFIX (thay cho --tessdata-dir: pytesseract trên Windows giữ nguyên dấu ngoặc kép trong
    tham số nên đường dẫn có dấu cách hỏng) và thư mục tạm an toàn. Trả về thư mục tessdata đã chọn."""
    td = resolve_tessdata(tesseract_cmd, tessdata, langs)
    if td is not None:
        os.environ["TESSDATA_PREFIX"] = usable_tessdata(td, langs)
    ensure_ascii_tempdir()
    return str(td) if td is not None else None


def _run_kwargs() -> dict:
    kwargs: dict = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: không nháy cửa sổ đen
    return kwargs


def list_languages(tesseract_cmd: str, tessdata_dir: Optional[str] = None) -> List[str]:
    cmd = [tesseract_cmd, "--list-langs"]
    if tessdata_dir:
        td = ascii_path(str(tessdata_dir))
        if os.name == "nt" and not td.isascii():
            # Tesseract không mở được đường dẫn có dấu: đọc danh sách file dữ liệu (khi chạy OCR sẽ dùng bản chép)
            try:
                return sorted(f.stem for f in Path(tessdata_dir).glob("*.traineddata"))
            except OSError:
                return []
        cmd += ["--tessdata-dir", td]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30, check=False, **_run_kwargs())
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    text = (out.stdout + out.stderr).decode("utf-8", errors="replace")
    langs = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and " " not in line and not line.startswith("List") and ":" not in line:
            langs.append(line)
    return langs


def tesseract_version(tesseract_cmd: str) -> str:
    try:
        out = subprocess.run([tesseract_cmd, "--version"], capture_output=True, timeout=30, check=False,
                             **_run_kwargs())
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    first = (out.stdout + out.stderr).decode("utf-8", errors="replace").strip().splitlines()
    return first[0] if first else ""


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

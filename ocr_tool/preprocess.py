"""Tiền xử lý ảnh: đọc file, xoay theo EXIF, phóng to, khử nghiêng, khử nhiễu."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import logging

import cv2
import numpy as np
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
PDF_EXTS = {".pdf"}
# Ảnh scan khổ lớn (A3 600 dpi ~ 70 MP) vượt ngưỡng chống "bom giải nén" mặc định của Pillow (89 MP cảnh báo)
Image.MAX_IMAGE_PIXELS = 400_000_000
MAX_WORK_PIXELS = 60_000_000  # ảnh lớn hơn được thu nhỏ trước khi OCR (đủ cho A3 ở 400 dpi), tránh hết bộ nhớ

log = logging.getLogger(__name__)


class InputError(Exception):
    """File đầu vào không đọc được – thông báo đã viết sẵn cho người dùng."""


@dataclass
class LoadedImage:
    image: np.ndarray  # BGR hoặc gray
    dpi: Optional[float]
    source: str
    page_index: int = 0
    pdf_text: Optional[object] = None  # pdftext.PdfPageText nếu PDF có lớp chữ


def _check_file(p: Path) -> None:
    if not p.is_file():
        raise InputError(f"Không tìm thấy file: {p}")
    if p.stat().st_size == 0:
        raise InputError(f"File rỗng (0 byte): {p.name}")


def is_pdf(p: Path) -> bool:
    """Nhận PDF theo nội dung (đầu file %PDF) chứ không chỉ theo đuôi."""
    try:
        with open(p, "rb") as f:
            head = f.read(1024)
    except OSError:
        return p.suffix.lower() in PDF_EXTS
    return b"%PDF" in head


def _open_image(p: Path) -> Image.Image:
    _check_file(p)
    try:
        im = Image.open(p)
        im.load()
        return im
    except UnidentifiedImageError:
        raise InputError(f"Không đọc được ảnh (file hỏng hoặc không phải ảnh/PDF): {p.name}") from None
    except Image.DecompressionBombError:
        raise InputError(f"Ảnh quá lớn (trên {Image.MAX_IMAGE_PIXELS // 1_000_000} triệu điểm ảnh): {p.name}") from None
    except (OSError, SyntaxError, ValueError) as e:
        # file bị cắt cụt (tải dở, chép lỗi): vẫn cố đọc phần còn lại thay vì bỏ cả trang
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        try:
            im = Image.open(p)
            im.load()
            log.warning("%s bị hỏng/cắt cụt (%s) – chỉ đọc được một phần ảnh", p.name, e)
            return im
        except Exception:  # noqa: BLE001
            raise InputError(f"Ảnh bị hỏng, không đọc được: {p.name} ({e})") from None
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = False


def load_inputs(path: str, pdf_dpi: int = 300, use_pdf_text: bool = True) -> List[LoadedImage]:
    """Đọc ảnh hoặc PDF (mỗi trang PDF -> 1 ảnh). Dùng PIL để hỗ trợ đường dẫn Unicode."""
    return list(iter_inputs(path, pdf_dpi, use_pdf_text))


def _iter_image_frames(p: Path):
    im = _open_image(p)
    try:
        n = getattr(im, "n_frames", 1) if p.suffix.lower() in (".tif", ".tiff", ".gif") else 1
        for i in range(n):
            if n > 1:
                im.seek(i)
                frame = im.copy()
            else:
                frame = im
            frame = ImageOps.exif_transpose(frame)
            dpi = _pil_dpi(frame)
            arr, dpi = _pil_to_bgr(frame, dpi)
            yield LoadedImage(arr, dpi, str(p), i)
    finally:
        im.close()


def _fitz():
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore
    try:
        fitz.TOOLS.mupdf_display_errors(False)  # lỗi PDF hỏng đã báo bằng thông báo dễ hiểu bên dưới
    except Exception:  # noqa: BLE001
        pass
    return fitz


_REPAIRED_WARNED: set = set()


def open_pdf(p: Path):
    """Mở PDF; lỗi thường gặp -> InputError có thông báo dễ hiểu."""
    fitz = _fitz()
    _check_file(p)
    try:
        doc = fitz.open(str(p))
    except Exception as e:  # noqa: BLE001 - EmptyFileError, FileDataError, RuntimeError tuỳ phiên bản
        raise InputError(f"Không mở được PDF (file hỏng hoặc không đúng định dạng): {p.name} ({e})") from None
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise InputError(f"PDF có mật khẩu, không đọc được: {p.name}. Hãy mở bằng trình đọc PDF, "
                         f"in/lưu thành bản không mật khẩu rồi thử lại.")
    if doc.page_count == 0:
        doc.close()
        raise InputError(f"PDF không có trang nào (có thể đã hỏng): {p.name}")
    if getattr(doc, "is_repaired", False) and str(p) not in _REPAIRED_WARNED:
        _REPAIRED_WARNED.add(str(p))
        log.warning("%s bị hỏng (tải dở / chép lỗi) – đã cố sửa, nội dung có thể bị thiếu", p.name)
    return doc


def iter_pdf(p: Path, dpi: int, use_pdf_text: bool = True):
    """Đọc PDF lần lượt từng trang (không giữ ảnh cả cuốn trong bộ nhớ – sách 230 trang ~6 GB)."""
    doc = open_pdf(p)
    try:
        for i in range(doc.page_count):
            yield _render_page(doc, i, dpi, use_pdf_text, str(p))
    finally:
        doc.close()


def _page_dpi(page, dpi: int) -> float:
    """Trang PDF khổ rất lớn (A0, bản vẽ) dựng ở dpi thấp hơn để ảnh không vượt MAX_WORK_PIXELS."""
    w_in, h_in = page.rect.width / 72.0, page.rect.height / 72.0
    px = w_in * h_in * dpi * dpi
    if px > MAX_WORK_PIXELS and w_in > 0 and h_in > 0:
        return max(72.0, float(dpi) * (MAX_WORK_PIXELS / px) ** 0.5)
    return float(dpi)


def _render_page(doc, index: int, dpi: int, use_pdf_text: bool, source: str) -> LoadedImage:
    fitz = _fitz()
    page = doc[index]
    d = _page_dpi(page, dpi)
    zoom = d / 72.0
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        elif pix.n == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        arr = np.ascontiguousarray(arr)
        del pix
    except Exception as e:  # noqa: BLE001 - trang hỏng: trang trắng thay vì bỏ cả tài liệu
        log.warning("%s trang %d: không dựng được ảnh trang (%s) – để trang trắng", source, index + 1, e)
        w = max(1, int(page.rect.width * zoom)) if page.rect.width else int(8.27 * d)
        h = max(1, int(page.rect.height * zoom)) if page.rect.height else int(11.69 * d)
        arr = np.full((h, w, 3), 255, np.uint8)
    li = LoadedImage(arr, d, source, index)
    if use_pdf_text:
        try:
            from .pdftext import extract_page
            li.pdf_text = extract_page(page, zoom)
        except Exception:  # noqa: BLE001 - lớp chữ hỏng thì OCR như bình thường
            li.pdf_text = None
    return li


def render_pdf_page(path: str, index: int, dpi: int = 300, use_pdf_text: bool = True) -> LoadedImage:
    """Dựng một trang PDF (dùng cho OCR song song)."""
    doc = open_pdf(Path(path))
    try:
        return _render_page(doc, index, dpi, use_pdf_text, path)
    finally:
        doc.close()


def iter_inputs(path: str, pdf_dpi: int = 300, use_pdf_text: bool = True):
    p = Path(path)
    _check_file(p)
    if p.suffix.lower() in PDF_EXTS or is_pdf(p):
        yield from iter_pdf(p, pdf_dpi, use_pdf_text)
    else:
        yield from _iter_image_frames(p)


def count_pages(path: str) -> int:
    p = Path(path)
    _check_file(p)
    if p.suffix.lower() in PDF_EXTS or is_pdf(p):
        doc = open_pdf(p)
        try:
            return doc.page_count
        finally:
            doc.close()
    if p.suffix.lower() in (".tif", ".tiff", ".gif"):
        im = _open_image(p)
        try:
            return getattr(im, "n_frames", 1)
        finally:
            im.close()
    return 1


def _pil_dpi(im: Image.Image) -> Optional[float]:
    dpi = im.info.get("dpi")
    if dpi:
        try:
            v = float(dpi[0])
            if 40 <= v <= 2400:
                return v
        except (TypeError, ValueError, IndexError):
            pass
    return None


def _pil_to_bgr(im: Image.Image, dpi: Optional[float] = None) -> Tuple[np.ndarray, Optional[float]]:
    """Ảnh PIL (mọi chế độ màu) -> mảng BGR 8 bit; ảnh quá lớn được thu nhỏ (dpi điều chỉnh theo)."""
    w, h = im.size
    if w * h > MAX_WORK_PIXELS:
        f = math.ceil(math.sqrt(w * h / MAX_WORK_PIXELS))
        log.info("ảnh %dx%d quá lớn – thu nhỏ %d lần trước khi OCR", w, h, f)
        im = im.reduce(f) if hasattr(im, "reduce") else im.resize((w // f, h // f))
        dpi = dpi / f if dpi else None
    if im.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        # ảnh 16/32 bit (máy scan chế độ "48-bit"): co giãn về 8 bit theo dải giá trị thật
        a = np.asarray(im, dtype=np.float32)
        lo, hi = float(np.percentile(a, 0.5)), float(np.percentile(a, 99.5))
        a = np.clip((a - lo) * (255.0 / max(1e-6, hi - lo)), 0, 255).astype(np.uint8)
        return cv2.cvtColor(a, cv2.COLOR_GRAY2BGR), dpi
    if im.mode in ("RGBA", "LA", "P", "PA", "RGBa", "La") or (im.mode == "L" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR), dpi


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def estimate_scale(gray: np.ndarray, target_min_side: int = 2000, max_scale: float = 3.0) -> float:
    """Phóng to ảnh nhỏ để Tesseract nhận dạng tốt hơn (~300 dpi cho khổ A4)."""
    h, w = gray.shape[:2]
    short = min(h, w)
    if short >= target_min_side:
        return 1.0
    return min(max_scale, target_min_side / short)


def deskew(gray: np.ndarray, max_angle: float = 10.0) -> Tuple[np.ndarray, float]:
    """Khử nghiêng dựa trên hướng các dòng chữ (Hough trên ảnh nhị phân đã giãn ngang)."""
    h, w = gray.shape[:2]
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    # nối các chữ trong dòng thành vệt dài
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // 60), 1))
    bands = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel)
    edges = cv2.Canny(bands, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=100,
                            minLineLength=w // 6, maxLineGap=20)
    if lines is None or len(lines) < 3:
        return gray, 0.0
    angles = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        if x2 == x1:
            continue
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if abs(a) <= max_angle:
            angles.append(a)
    if len(angles) < 3:
        return gray, 0.0
    angle = float(np.median(angles))
    if abs(angle) < 0.15:
        return gray, 0.0
    center = (w / 2, h / 2)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return rotated, angle


def flatten_background(gray: np.ndarray) -> np.ndarray:
    """Ảnh scan/photocopy nền xám hoặc sáng không đều: chia cho nền ước lượng -> nền trắng, chữ giữ độ đậm.
    Ảnh nền đã trắng (PDF dựng hình, scan sạch) thì giữ nguyên."""
    small = cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    bright = np.percentile(small, 90)
    spread = np.percentile(small, 90) - np.percentile(small, 60)
    if bright >= 240 and spread < 12:
        return gray
    k = max(15, int(min(small.shape[:2]) * 0.05) | 1)
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 3)
    bg = cv2.resize(bg, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    out = cv2.divide(gray, np.maximum(bg, 1), scale=255)
    return out


def denoise(gray: np.ndarray) -> np.ndarray:
    return cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)


def binarize(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, 31, 15)


@dataclass
class PreprocessResult:
    gray: np.ndarray       # ảnh dùng để OCR
    scale: float           # tỉ lệ phóng so với ảnh gốc
    angle: float           # góc đã xoay
    color: np.ndarray      # ảnh gốc (đã phóng/xoay) để trích hình nếu cần


def preprocess(img: np.ndarray, do_deskew: bool = True, do_denoise: bool = False,
               do_binarize: bool = False, upscale: bool = True) -> PreprocessResult:
    gray = to_gray(img)
    scale = estimate_scale(gray) if upscale else 1.0
    color = img
    if scale != 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        color = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    angle = 0.0
    if do_deskew:
        gray, angle = deskew(gray)
        if angle:
            h, w = gray.shape[:2]
            m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            color = cv2.warpAffine(color, m, (w, h), flags=cv2.INTER_CUBIC,
                                   borderMode=cv2.BORDER_CONSTANT,
                                   borderValue=(255, 255, 255))
    gray = flatten_background(gray)
    if do_denoise:
        gray = denoise(gray)
    if do_binarize:
        gray = binarize(gray)
    return PreprocessResult(gray=gray, scale=scale, angle=angle, color=color)

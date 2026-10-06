"""Tiền xử lý ảnh: đọc file, xoay theo EXIF, phóng to, khử nghiêng, khử nhiễu."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
PDF_EXTS = {".pdf"}


@dataclass
class LoadedImage:
    image: np.ndarray  # BGR hoặc gray
    dpi: Optional[float]
    source: str
    page_index: int = 0
    pdf_text: Optional[object] = None  # pdftext.PdfPageText nếu PDF có lớp chữ


def load_inputs(path: str, pdf_dpi: int = 300, use_pdf_text: bool = True) -> List[LoadedImage]:
    """Đọc ảnh hoặc PDF (mỗi trang PDF -> 1 ảnh). Dùng PIL để hỗ trợ đường dẫn Unicode."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in PDF_EXTS:
        return _load_pdf(p, pdf_dpi, use_pdf_text)
    if ext == ".tif" or ext == ".tiff" or ext == ".gif":
        return _load_multiframe(p)
    with Image.open(p) as im:
        im = ImageOps.exif_transpose(im)
        dpi = _pil_dpi(im)
        arr = _pil_to_bgr(im)
    return [LoadedImage(arr, dpi, str(p), 0)]


def _load_multiframe(p: Path) -> List[LoadedImage]:
    out: List[LoadedImage] = []
    with Image.open(p) as im:
        n = getattr(im, "n_frames", 1)
        for i in range(n):
            im.seek(i)
            frame = ImageOps.exif_transpose(im.copy())
            out.append(LoadedImage(_pil_to_bgr(frame), _pil_dpi(frame), str(p), i))
    return out


def _load_pdf(p: Path, dpi: int, use_pdf_text: bool = True) -> List[LoadedImage]:
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore
    out: List[LoadedImage] = []
    doc = fitz.open(str(p))
    zoom = dpi / 72.0
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        elif pix.n == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        li = LoadedImage(np.ascontiguousarray(arr), float(dpi), str(p), i)
        if use_pdf_text:
            try:
                from .pdftext import extract_page
                li.pdf_text = extract_page(page, zoom)
            except Exception:  # noqa: BLE001 - lớp chữ hỏng thì OCR như bình thường
                li.pdf_text = None
        out.append(li)
    doc.close()
    return out


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


def _pil_to_bgr(im: Image.Image) -> np.ndarray:
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


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
    if do_denoise:
        gray = denoise(gray)
    if do_binarize:
        gray = binarize(gray)
    return PreprocessResult(gray=gray, scale=scale, angle=angle, color=color)

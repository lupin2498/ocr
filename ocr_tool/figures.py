"""Nhận diện vùng hình vẽ / ảnh trên trang để giữ nguyên dạng ảnh (không OCR).

Ý tưởng:
* Hình vẽ kỹ thuật, sơ đồ, ảnh chụp tạo ra các thành phần liên thông (connected component)
  lớn hơn hẳn một ký tự (nét vẽ dài, khối đen, vùng bán sắc). Gom các thành phần lớn gần nhau
  thành vùng ứng viên.
* Chú thích nhỏ nằm trong / sát hình (nhãn chi tiết) được gộp vào hình; dòng tiêu đề hình
  ("Hình 2.1. ...") và đoạn văn chính thì không.
* Khung bao quanh một đoạn văn (text box) không bị coi là hình.
* Bảng phát hiện được nhưng chứa nhiều nét vẽ ngoài chữ / ô toàn ký tự rác -> thực chất là hình.
* Đoạn "văn bản" toàn ký tự rác, độ tin cậy thấp -> vùng hình (giữ ảnh để không mất nội dung).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .document import BBox, Line, union_bbox
from .vnsyllable import is_valid_syllable

log = logging.getLogger(__name__)

CAPTION_RE = re.compile(r"^\s*(hình|hinh|bảng|bang|sơ đồ|рис\.?|рисунок|таблица|fig\.?|figure|table)\s*[\dIVX]",
                        re.IGNORECASE)
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def text_quality(text: str, conf: float = 100.0) -> float:
    """Tỉ lệ token "giống chữ thật" (âm tiết tiếng Việt hợp lệ, số, từ Latin/Cyrillic đủ dài)."""
    toks = _TOKEN_RE.findall(text)
    if not toks:
        return 0.0
    good = 0
    for t in toks:
        low = t.lower()
        if t.isdigit() or is_valid_syllable(low):
            good += 1
        elif len(t) >= 3 and (t.isalpha() and (t.isupper() or t.islower() or t.istitle())) and conf >= 80:
            good += 1
        elif re.fullmatch(r"[A-ZА-Я]{1,6}\d*[A-ZА-Я]*", t):
            good += 0.5  # ký hiệu kỹ thuật
    return good / len(toks)


def is_good_text_line(line: Line) -> bool:
    return line.conf >= 70 and text_quality(line.text, line.conf) >= 0.6


@dataclass
class FigureRegion:
    bbox: BBox
    reason: str = ""


def _inter_area(a: BBox, b: BBox) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)


def _area(b: BBox) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _expand(b: BBox, d: float, w: int, h: int) -> BBox:
    d = int(d)
    return (max(0, b[0] - d), max(0, b[1] - d), min(w, b[2] + d), min(h, b[3] + d))


def _gap(a: BBox, b: BBox) -> float:
    dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
    return max(dx, dy)


def binarize_ink(gray: np.ndarray) -> np.ndarray:
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]


def merge_regions(regions: List[BBox], gap: float) -> List[BBox]:
    regions = list(regions)
    changed = True
    while changed:
        changed = False
        out: List[BBox] = []
        for r in regions:
            for i, o in enumerate(out):
                if _gap(r, o) <= gap:
                    out[i] = union_bbox([r, o])
                    changed = True
                    break
            else:
                out.append(r)
        regions = out
    return regions


def _is_outline_frame(labels: np.ndarray, idx: int, x: int, y: int, cw: int, ch: int, lh: float) -> bool:
    """Thành phần liên thông chủ yếu nằm trên 4 cạnh khung bao của nó (khung chữ nhật rỗng)."""
    comp = labels[y:y + ch, x:x + cw] == idx
    band = max(2, int(0.5 * lh))
    total = int(comp.sum())
    if total == 0:
        return False
    border = int(comp[:band].sum() + comp[-band:].sum() + comp[band:-band, :band].sum() + comp[band:-band, -band:].sum())
    return border >= 0.85 * total


def detect_figures(gray: np.ndarray, lines: Sequence[Line], lh: float,
                   exclude: Sequence[BBox] = (), content_width: Optional[int] = None) -> List[FigureRegion]:
    """Trả về các vùng hình. `lines`: các dòng OCR trên trang; `exclude`: vùng bảng thật."""
    h, w = gray.shape[:2]
    lh = max(8.0, lh)
    bw = binarize_ink(gray)
    pad = int(0.4 * lh) + 4
    for b in exclude:
        bw[max(0, b[1] - pad):b[3] + pad, max(0, b[0] - pad):b[2] + pad] = 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    seeds: List[BBox] = []
    edge = max(3, int(0.012 * min(w, h)))
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if area < 4:
            continue
        # viền / bóng đen ở mép trang scan (gáy sách, mép giấy) – không phải hình.
        # Ảnh tràn lề thật (bìa sách, ảnh chụp) thì lớn theo cả hai chiều và đặc -> vẫn là hình.
        if x <= edge or y <= edge or x + cw >= w - edge or y + ch >= h - edge:
            fill = area / max(1, cw * ch)
            thin = min(cw, ch) < 0.15 * min(w, h)
            if thin or fill < 0.3:
                continue
        # khung chữ nhật chỉ có đường viền (khung bao đoạn văn/hình): không làm "hạt giống" hình
        if cw > 6 * lh and ch > 4 * lh and area < 0.08 * cw * ch and _is_outline_frame(labels, i, x, y, cw, ch, lh):
            continue
        thin_h = ch < 0.35 * lh and cw > 4 * lh   # đường kẻ ngang mảnh
        thin_v = cw < 0.35 * lh and ch > 4 * lh   # đường kẻ dọc mảnh
        big = cw > 3.5 * lh or ch > 2.6 * lh
        if big or thin_h or thin_v:
            seeds.append((int(x), int(y), int(x + cw), int(y + ch)))
    if not seeds:
        return []
    groups = merge_regions(seeds, gap=1.2 * lh)
    content_width = content_width or w

    regions: List[FigureRegion] = []
    for g in groups:
        gw, gh = g[2] - g[0], g[3] - g[1]
        if gw < 3 * lh or gh < 2.5 * lh:
            continue  # đường kẻ phân cách, gạch chân...
        regions.append(FigureRegion(g, "graphics"))
    if not regions:
        return []

    # Gộp nhãn / chữ nằm trong hoặc sát hình
    out: List[FigureRegion] = []
    for reg in regions:
        box = reg.bbox
        for _ in range(3):
            grown = box
            bw_ = box[2] - box[0]
            span = (box[0] - 0.1 * bw_, box[2] + 0.1 * bw_)
            for ln in lines:
                if not ln.words:
                    continue
                lb = ln.bbox
                if CAPTION_RE.match(ln.text):
                    continue
                inside = _inter_area(lb, box) >= 0.5 * max(1, _area(lb))
                near = _gap(lb, box) <= 1.2 * lh
                if inside:
                    grown = union_bbox([grown, lb])
                    continue
                if not near:
                    continue
                width = lb[2] - lb[0]
                within_span = lb[0] >= span[0] and lb[2] <= span[1]
                gaps = [b.left - a.right for a, b in zip(ln.words, ln.words[1:])]
                sparse = bool(gaps) and max(gaps) > 2.5 * lh  # hàng nhãn rời rạc
                side = lb[1] < box[3] and lb[3] > box[1] and width < 0.25 * content_width
                if side or (within_span and (sparse or width < 0.45 * content_width
                                             or not is_good_text_line(ln))):
                    grown = union_bbox([grown, lb])
            if grown == box:
                break
            box = grown
        # Khung chữ (text box): bên trong chủ yếu là dòng văn bản tốt -> không phải hình
        inner = [ln for ln in lines if ln.words and _inter_area(ln.bbox, box) >= 0.5 * max(1, _area(ln.bbox))]
        good = [ln for ln in inner if is_good_text_line(ln) and (ln.bbox[2] - ln.bbox[0]) > 0.3 * (box[2] - box[0])]
        good_area = sum(_area(ln.bbox) for ln in good)
        if len(good) >= 2 and good_area > 0.30 * _area(box):
            log.debug("bỏ qua khung chữ %s", box)
            continue
        out.append(FigureRegion(box, reg.reason))
    merged = merge_regions([r.bbox for r in out], gap=1.5 * lh)
    return [FigureRegion(b, "graphics") for b in merged]


def detect_frames(gray: np.ndarray, lh: float) -> List[BBox]:
    """Khung chữ nhật chỉ có đường viền (hộp chữ / hộp minh hoạ)."""
    h, w = gray.shape[:2]
    lh = max(8.0, lh)
    bw = binarize_ink(gray)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    out: List[BBox] = []
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if cw > 6 * lh and ch > 4 * lh and area < 0.08 * cw * ch and cw < 0.98 * w and ch < 0.98 * h \
                and _is_outline_frame(labels, i, x, y, cw, ch, lh):
            out.append((int(x), int(y), int(x + cw), int(y + ch)))
    return out


_PANEL_DELTA = 10
_PANEL_SIDE = 0.6


def detect_shaded_panels(color: np.ndarray, lh: float, min_frac: float = 0.03) -> List[BBox]:
    """Khung tô nền / viền xám dày (hộp minh hoạ của sách). Bước làm phẳng nền trước OCR xoá mất
    các khung này nên dò trên ảnh gốc: đóng hình thái học (xoá nét chữ) rồi lấy vùng tối hơn giấy.
    Trả về khung đặc (nền xám) và khung viền dày (hình chữ nhật rỗng)."""
    g = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY) if color.ndim == 3 else color
    h, w = g.shape[:2]
    f = 4
    small = cv2.resize(g, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    k = 3  # ~12px ở 300dpi: rộng hơn nét chữ thường, hẹp hơn viền khung
    filled = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    paper = float(np.percentile(small, 90))
    mask = (filled < paper - _PANEL_DELTA).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * k + 1, 2 * k + 1)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out: List[BBox] = []
    sw, sh = small.shape[1], small.shape[0]
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if cw * ch < min_frac * sw * sh or cw < 0.25 * sw or ch < 0.1 * sh:
            continue
        touches = x <= 1 or y <= 1 or x + cw >= sw - 1 or y + ch >= sh - 1
        if touches and min(cw, ch) < 0.15 * min(sw, sh):
            continue  # bóng mép trang scan
        if touches and cw * ch > 0.8 * sw * sh:
            continue
        solid = area >= 0.6 * cw * ch
        ring = False
        if not solid and cw > 6 * k and ch > 6 * k:
            comp = labels[y:y + ch, x:x + cw] == i
            band = 2 * k
            # cạnh khung: tỉ lệ hàng/cột trong dải mép có điểm thuộc khung
            sides = [comp[:band].any(axis=0).mean(), comp[-band:].any(axis=0).mean(),
                     comp[:, :band].any(axis=1).mean(), comp[:, -band:].any(axis=1).mean()]
            ring = sum(v > _PANEL_SIDE for v in sides) >= 3 and comp[band:-band, band:-band].mean() < 0.3
        if solid or ring:
            out.append((int(x) * f, int(y) * f, min(w, (int(x) + int(cw)) * f), min(h, (int(y) + int(ch)) * f)))
    return out


_DIAGRAM_GLYPHS = set("¬~`\\@*_<>®©¥‹›§¤•=—→←↑↓")


def table_is_figure(gray: np.ndarray, table_bbox: BBox, line_mask: Optional[np.ndarray],
                    word_boxes: Iterable[BBox], cell_texts: Sequence[str], lh: float) -> bool:
    """Bảng phát hiện được có thực là hình vẽ không? (nhiều nét vẽ ngoài chữ, hoặc ô toàn ký tự rác)."""
    l, t, r, b = table_bbox
    if r - l < 4 or b - t < 4:
        return False
    bw = binarize_ink(gray[t:b, l:r]).copy()
    if line_mask is not None:
        lm = line_mask[t:b, l:r]
        lm = cv2.dilate(lm, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
        bw[lm > 0] = 0
    pad = int(0.25 * lh)
    for wb in word_boxes:
        x0, y0 = max(0, wb[0] - l - pad), max(0, wb[1] - t - pad)
        x1, y1 = min(r - l, wb[2] - l + pad), min(b - t, wb[3] - t + pad)
        bw[y0:y1, x0:x1] = 0
    residual = float((bw > 0).mean())
    texts = [c for c in cell_texts if c.strip()]
    empty_ratio = 1 - len(texts) / max(1, len(cell_texts))
    quality = np.mean([text_quality(c) for c in texts]) if texts else 0.0
    log.debug("bảng %s: mực thừa %.3f, ô trống %.2f, chất lượng chữ %.2f", table_bbox, residual, empty_ratio, quality)
    # mũi tên, khung, biểu tượng của sơ đồ bị OCR thành ký hiệu lạ trong "ô"
    joined = "".join(texts)
    glyphs = sum(c in _DIAGRAM_GLYPHS for c in joined)
    alnum = sum(c.isalnum() for c in joined)
    glyph_ratio = glyphs / max(1, alnum)
    log.debug("bảng %s: ký hiệu sơ đồ %d (%.2f)", table_bbox, glyphs, glyph_ratio)
    if residual > 0.03:
        return True
    if glyphs >= 5 and (glyph_ratio >= 0.08 or (residual > 0.012 and glyph_ratio >= 0.04)):
        return True
    if texts and quality < 0.35:
        return True
    if empty_ratio > 0.85 and len(cell_texts) >= 4:
        return True
    return False


def crop_figure(color: np.ndarray, bbox: BBox, pad: int = 4) -> np.ndarray:
    h, w = color.shape[:2]
    l, t, r, b = bbox
    return color[max(0, t - pad):min(h, b + pad), max(0, l - pad):min(w, r + pad)].copy()

"""Suy ra bố cục (khổ giấy, lề, canh lề, cỡ chữ, thụt đầu dòng, giãn cách) từ toạ độ OCR."""
from __future__ import annotations

import logging
import re
from statistics import median
from typing import List, Optional, Tuple

from .document import BBox, Element, Figure, Page, Paragraph, Table

log = logging.getLogger(__name__)

A4 = (8.27, 11.69)
LETTER = (8.5, 11.0)


def choose_paper(width_px: int, height_px: int, dpi: Optional[float]) -> Tuple[float, float]:
    """Chọn khổ giấy (inch). Nếu ảnh có DPI đáng tin thì dùng kích thước thật,
    ngược lại quy về A4 theo hướng ảnh."""
    landscape = width_px > height_px
    if dpi:
        w_in, h_in = width_px / dpi, height_px / dpi
        if 3.5 <= min(w_in, h_in) <= 20 and 4 <= max(w_in, h_in) <= 30:
            return (w_in, h_in)
    paper = A4
    return (paper[1], paper[0]) if landscape else paper


def build_page(width_px: int, height_px: int, dpi: Optional[float], elements: List[Element],
               source: str = "", page_index: int = 0) -> Page:
    pw, ph = choose_paper(width_px, height_px, dpi)
    eff_dpi = width_px / pw
    page = Page(width_px=width_px, height_px=height_px, dpi=eff_dpi,
                page_width_in=pw, page_height_in=ph, elements=elements,
                source=source, page_index=page_index)
    _extract_page_number(page)
    _compute_margins(page)
    _style_elements(page)
    return page


_PAGE_NO = re.compile(r"^\s*[-–]?\s*\d{1,4}\s*[-–]?\s*$")


def _extract_page_number(page: Page) -> None:
    """Số trang ở mép trên/dưới trang -> header/footer của Word (không chiếm chỗ trong thân trang)."""
    keep = []
    for el in page.elements:
        if isinstance(el, Paragraph) and not el.is_empty() and _PAGE_NO.match(el.text) and len(el.lines) == 1:
            b = el.bbox
            if b[3] < 0.1 * page.height_px and not page.header_text:
                page.header_text = el.text.strip()
                continue
            if b[1] > 0.9 * page.height_px and not page.footer_text:
                page.footer_text = el.text.strip()
                continue
        keep.append(el)
    page.elements = keep


def _content_bbox(page: Page) -> Optional[BBox]:
    boxes = []
    for el in page.elements:
        if isinstance(el, Paragraph):
            if not el.is_empty():
                boxes.append(el.bbox)
        else:
            boxes.append(el.bbox)
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _compute_margins(page: Page) -> None:
    cb = _content_bbox(page)
    if cb is None:
        return
    l = page.px_to_in(cb[0])
    r = page.page_width_in - page.px_to_in(cb[2])
    t = page.px_to_in(cb[1])
    b = page.page_height_in - page.px_to_in(cb[3])
    clamp = lambda v, lo, hi: max(lo, min(hi, v))
    page.margin_left_in = clamp(l, 0.4, 1.6)
    # chừa thêm ~0.15" bề ngang: phông thay thế rộng hơn chút sẽ không làm xuống dòng thừa
    page.margin_right_in = clamp(r - 0.15, 0.3, 1.6)
    page.margin_top_in = clamp(t, 0.4, 1.6)
    page.margin_bottom_in = clamp(b, 0.4, 1.6)


COMMON_SIZES = [7, 8, 9, 10, 10.5, 11, 12, 13, 14, 16, 18, 20, 22, 24, 26, 28, 32, 36, 40, 48]
_ASC = set("bdfhklt0123456789đĐбй")
_DESC = set("gjpqyдуфрщцДЩЦ")
_ABOVE = set("ăâêôơưĂÂÊÔƠƯàảãáằẳẵắầẩẫấèẻẽéềểễếìỉĩíòỏõóồổỗốờởỡớùủũúừửữứỳỷỹý"
             "ÀẢÃÁẰẲẴẮẦẨẪẤÈẺẼÉỀỂỄẾÌỈĨÍÒỎÕÓỒỔỖỐỜỞỠỚÙỦŨÚỪỬỮỨỲỶỸÝЁёЙ")
_BELOW = set("ạặậẹệịọộợụựỵẠẶẬẸỆỊỌỘỢỤỰỴ")


def _snap_size(pt: float) -> float:
    return min(COMMON_SIZES, key=lambda s: abs(s - pt))


def estimate_font_px(text: str, line_height_px: float) -> float:
    """Ước lượng cỡ chữ (px, tương ứng em) từ chiều cao hộp dòng và các loại ký tự có mặt.
    Chiều cao hộp = tổng các phần: chữ hoa/ascender ~0.72em, x-height ~0.5em,
    descender ~0.22em, dấu phía trên ~0.15em, dấu phía dưới ~0.2em."""
    chars = set(text)
    has_asc = any(c.isupper() or c in _ASC for c in chars)
    has_desc = any(c in _DESC for c in chars)
    has_above = any(c in _ABOVE for c in chars)
    has_below = any(c in _BELOW for c in chars)
    ratio = 0.72 if has_asc else 0.5
    if has_above:
        ratio += 0.15 if has_asc else 0.32
    if has_desc:
        ratio += 0.22
    elif has_below:
        ratio += 0.2
    return line_height_px / ratio


def _font_size_pt(page: Page, p: Paragraph) -> float:
    ems = [estimate_font_px(ln.text, ln.height) for ln in p.lines if ln.words and ln.height > 0]
    if not ems:
        return 12.0
    pt = page.px_to_pt(median(ems))
    return _snap_size(max(7.0, min(48.0, pt)))


def _style_elements(page: Page) -> None:
    paras = [e for e in page.elements if isinstance(e, Paragraph) and not e.is_empty()]
    heights = [ln.height for p in paras for ln in p.lines if ln.words]
    if not heights:
        for el in page.elements:
            if isinstance(el, Table):
                el.font_size_pt = 11.0
        heights = [max(12.0, page.dpi / 6)]
        paras = []
    if not paras:
        return
    base_h = float(median(heights))
    base_pt = float(median([p.fixed_size_pt or _font_size_pt(page, p) for p in paras]))

    pdf_pitch_ratio = _page_pdf_pitch_ratio(page, paras)
    content = _content_bbox(page)
    cL, cR = content[0], content[2]
    content_w = max(1, cR - cL)
    page_cx = page.width_px / 2

    for idx, el in enumerate(page.elements):
        nxt = page.elements[idx + 1] if idx + 1 < len(page.elements) else None
        if isinstance(el, Figure):
            fcx = (el.bbox[0] + el.bbox[2]) / 2
            if abs(fcx - (cL + cR) / 2) <= 0.08 * content_w or abs(fcx - page_cx) <= 0.06 * page.width_px:
                el.alignment = "center"
                el.left_indent_in = 0.0
            elif el.bbox[2] >= cR - 0.05 * content_w and el.bbox[0] > cL + 0.3 * content_w:
                el.alignment = "right"
                el.left_indent_in = 0.0
            else:
                el.alignment = "left"
                el.left_indent_in = max(0.0, page.px_to_in(el.bbox[0] - cL))
            el.space_after_pt = _space_after(page, el.bottom, nxt, base_h)
            continue
        if isinstance(el, Table):
            el.font_size_pt = max(7.0, min(base_pt, 12.0))
            el.space_after_pt = _space_after(page, el.bottom, nxt, base_h)
            continue
        if el.is_empty():
            continue
        p = el
        lh = p.median_line_height or base_h
        p.font_size_pt = p.fixed_size_pt if p.fixed_size_pt else _font_size_pt(page, p)
        # tiêu đề: chữ lớn hơn hẳn hoặc ngắn và toàn chữ in
        text = p.text
        letters = [c for c in text if c.isalpha()]
        upper_ratio = (sum(c.isupper() for c in letters) / len(letters)) if letters else 0
        p.bold = (p.font_size_pt >= base_pt * 1.25 and len(p.lines) <= 3) or \
                 (upper_ratio > 0.9 and len(letters) >= 4 and len(p.lines) <= 2)
        if p.fixed_bold is not None:
            p.bold = p.fixed_bold

        ref = p.block_bbox or p.bbox
        bL, bR = ref[0], ref[2]
        # với khối rộng gần bằng trang, dùng lề nội dung làm mốc
        if (bR - bL) > 0.7 * content_w:
            bL, bR = cL, cR
        p.alignment = _alignment(p, bL, bR, cL, cR, page_cx, content_w, lh)
        pb = p.bbox
        if p.alignment in ("left", "justify"):
            lefts = [ln.bbox[0] for ln in p.lines if ln.words]
            body_left = min(lefts[1:]) if len(lefts) > 1 else lefts[0]
            indent = page.px_to_in(body_left - cL)
            p.left_indent_in = indent if indent >= 0.08 else 0.0
            first = page.px_to_in(lefts[0] - body_left)
            p.first_line_indent_in = first if abs(first) >= 0.08 and len(lefts) > 1 else 0.0
        else:
            p.left_indent_in = 0.0
            p.first_line_indent_in = 0.0
        if p.fixed_size_pt:
            p.line_spacing = _pdf_line_spacing(page, p, pdf_pitch_ratio)
            p.line_pitch_pt = round(max(p.fixed_size_pt, p.line_spacing * 1.15 * p.fixed_size_pt), 1)
        else:
            p.line_spacing = _line_spacing(p, lh)
        p.space_after_pt = _space_after(page, pb[3], nxt, lh)


def _alignment(p: Paragraph, bL: int, bR: int, cL: int, cR: int, page_cx: float,
               content_w: int, lh: float) -> str:
    lines = [ln for ln in p.lines if ln.words]
    lefts = [ln.bbox[0] for ln in lines]
    rights = [ln.bbox[2] for ln in lines]
    tol = max(lh * 0.8, 0.025 * content_w)
    pb = p.bbox
    width = pb[2] - pb[0]
    centers = [(l + r) / 2 for l, r in zip(lefts, rights)]
    # Canh giữa: mọi dòng có tâm gần tâm trang (hoặc tâm vùng nội dung) và không chiếm hết bề ngang
    if width < 0.8 * content_w and min(lefts) - cL > tol and cR - max(rights) > tol:
        if len(lines) == 1:
            tol = min(tol, max(lh * 0.6, 0.012 * content_w))  # dòng đơn: chỉ coi là giữa khi thật sự cân
        cands = [page_cx, (cL + cR) / 2]
        if (bR - bL) - width > 2 * tol:  # khối rộng hơn hẳn đoạn (vd. cột)
            cands.append((bL + bR) / 2)
        for cx in cands:
            if all(abs(c - cx) <= tol for c in centers):
                return "center"
    # Canh phải: lề phải thẳng với mép phải khối / nội dung, lề trái cách xa mép trái
    for L, R in ((bL, bR), (cL, cR)):
        if all(abs(r - R) <= tol for r in rights) and min(lefts) - L > 3 * tol:
            if len(lines) == 1 or (max(lefts) - min(lefts)) > tol:
                return "right"
    # Dòng đơn nằm hẳn về nửa phải trang và kết thúc gần mép phải
    if len(lines) == 1 and lefts[0] - cL > 0.5 * content_w and cR - rights[0] <= 0.08 * content_w:
        return "right"
    # Căn đều hai bên: >= 3 dòng, trái thẳng, phải thẳng (trừ dòng cuối)
    if len(lines) >= 3:
        body_rights = rights[:-1]
        if all(abs(l - bL) <= tol for l in lefts[1:]) and \
                all(abs(r - bR) <= tol for r in body_rights) and (bR - bL) > 0.5 * content_w:
            return "justify"
    return "left"


def _pitch_ratio(page: Page, p: Paragraph) -> Optional[float]:
    lines = [ln for ln in p.lines if ln.words]
    tops = [ln.bbox[1] for ln in lines]
    pitches = [b - a for a, b in zip(tops, tops[1:]) if b > a]
    if not pitches or not p.fixed_size_pt:
        return None
    # Word: giãn dòng "single" của Times New Roman ~ 1.15 x cỡ chữ
    return page.px_to_pt(median(pitches)) / (1.15 * p.fixed_size_pt)


def _page_pdf_pitch_ratio(page: Page, paras: List[Paragraph]) -> float:
    rs = [r for r in (_pitch_ratio(page, p) for p in paras) if r]
    return float(median(rs)) if rs else 1.0


def _pdf_line_spacing(page: Page, p: Paragraph, default: float) -> float:
    r = _pitch_ratio(page, p) or default
    return round(max(0.8, min(3.0, r)), 2)


def _line_spacing(p: Paragraph, lh: float) -> float:
    lines = [ln for ln in p.lines if ln.words]
    if len(lines) < 2 or lh <= 0:
        return 1.0
    tops = [ln.bbox[1] for ln in lines]
    pitches = [b - a for a, b in zip(tops, tops[1:]) if b > a]
    if not pitches:
        return 1.0
    ratio = median(pitches) / (lh * 1.2)
    for cand in (1.0, 1.15, 1.5, 2.0):
        if ratio <= cand + 0.12:
            return cand
    return 2.0


def _space_after(page: Page, bottom: int, nxt: Optional[Element], lh: float) -> float:
    if nxt is None:
        return 6.0
    gap = nxt.top - bottom
    if gap <= 0:
        return 0.0
    # trừ khoảng trống tự nhiên giữa hai dòng
    extra = gap - lh * 0.35
    pt = page.px_to_pt(max(0.0, extra))
    return float(max(0.0, min(30.0, round(pt))))

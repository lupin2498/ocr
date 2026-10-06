"""Đọc lớp chữ có sẵn trong PDF (thay vì OCR) – chính xác 100% khi PDF được tạo từ Word.

* Hỗ trợ chữ Unicode và chữ mã TCVN3/ABC (phông .VnTime/.VnTimeH), kể cả chữ "ư" bị xuất thành "-".
* Trả về các khối/đoạn/dòng/từ với toạ độ theo pixel của ảnh trang (để dùng chung bố cục, bảng, hình).
* Lấy vị trí các ảnh nhúng trong PDF -> vùng hình (giữ nguyên ảnh).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .document import BBox, Line, Paragraph, Word, union_bbox
from .legacy_encoding import (_TCVN3_SPECIFIC, decode_tcvn3, is_tcvn3_font, is_upper_font, looks_tcvn3)

log = logging.getLogger(__name__)

_VIET_UNICODE = re.compile(r"[Ạ-ỹơưăđƠƯĂĐ]")
_LIST_START = re.compile(r"^\s*([-+*•–]\s|\d+(\.\d+)*[.)]\s|[a-zđ]\)\s|\d+\)\s)", re.IGNORECASE)


@dataclass
class PdfPageText:
    paragraphs: List[Paragraph] = field(default_factory=list)
    image_boxes: List[BBox] = field(default_factory=list)
    n_chars: int = 0
    rotated_chars: int = 0
    rotated_boxes: List[BBox] = field(default_factory=list)
    rules: List[BBox] = field(default_factory=list)  # đường kẻ vector (kẻ bảng) theo pixel


def _page_is_tcvn3(page) -> Tuple[bool, bool]:
    """(có phông .Vn, nội dung trông như mã TCVN3 dù tên phông khác)."""
    fonts = [f[3] for f in page.get_fonts()]
    by_font = any(is_tcvn3_font(f) for f in fonts)
    txt = page.get_text()
    by_content = looks_tcvn3(txt) and not _VIET_UNICODE.search(txt)
    return by_font, by_content


def extract_page(page, scale: float) -> PdfPageText:
    """`scale` = pixel ảnh / point PDF (dpi/72)."""
    from .spellfix import restore_u_hyphen

    out = PdfPageText()
    tcvn_font, tcvn_content = _page_is_tcvn3(page)
    tcvn = tcvn_font or tcvn_content
    raw = page.get_text("rawdict")
    lines: List[Tuple[Line, float, bool, bool]] = []  # (line, size_pt, bold, blank_before)
    blank = True
    for blk in raw.get("blocks", []):
        if blk.get("type", 0) != 0:
            continue
        for ln in blk.get("lines", []):
            if abs(ln.get("dir", (1, 0))[1]) > 0.2:
                out.rotated_chars += sum(len(sp.get("chars", [])) for sp in ln.get("spans", []))
                bb = ln["bbox"]
                out.rotated_boxes.append((int(bb[0] * scale), int(bb[1] * scale), int(bb[2] * scale), int(bb[3] * scale)))
                continue  # chữ xoay dọc (trang hình xoay ngang) – nằm trong ảnh trang
            words: List[Word] = []
            cur_txt, cur_box = [], []
            sizes, bolds, italics = [], [], []
            prev_upper = False

            def flush():
                if cur_txt:
                    txt = "".join(cur_txt)
                    txt = restore_u_hyphen(txt)
                    b = union_bbox(cur_box)
                    words.append(Word(txt, int(b[0] * scale), int(b[1] * scale),
                                      max(1, int((b[2] - b[0]) * scale)), max(1, int((b[3] - b[1]) * scale)), 99.0))
                cur_txt.clear()
                cur_box.clear()

            for sp in ln.get("spans", []):
                font = sp.get("font", "")
                vn = is_tcvn3_font(font) or (tcvn_content and "symbol" not in font.lower())
                upper = is_upper_font(font) if vn else prev_upper
                chars = sp.get("chars", [])
                s = "".join(c["c"] for c in chars)
                # phông thay thế (Calibri...) chứa mã TCVN3 lẻ, ví dụ "C¥" trong tiêu đề
                # chỉ khi dính liền chữ trước đó (C¥), tránh đổi ký hiệu toán học (÷, ±) của phông Symbol
                fallback = (not vn and tcvn and bool(s.strip()) and " " not in s.strip() and bool(cur_txt)
                            and cur_txt[-1].isalpha() and not _VIET_UNICODE.search(s)
                            and all(ch in _TCVN3_SPECIFIC for ch in s.strip()))
                for c in chars:
                    ch = c["c"]
                    if ch.isspace():
                        flush()
                        continue
                    if vn or fallback:
                        ch = decode_tcvn3(ch, upper)
                    cur_txt.append(ch)
                    cur_box.append(tuple(c["bbox"]))
                if s.strip():
                    sizes.append(sp.get("size", 12.0))
                    bolds.append(bool(sp.get("flags", 0) & 16) or "bold" in font.lower())
                    italics.append(bool(sp.get("flags", 0) & 2) or "italic" in font.lower())
                    if vn:
                        prev_upper = upper
            flush()
            if not words:
                blank = True
                continue
            size = max(set(sizes), key=sizes.count) if sizes else 12.0
            bold = sum(bolds) > len(bolds) / 2 if bolds else False
            italic = sum(italics) > len(italics) / 2 if italics else False
            lines.append((Line(words), size, (bold, italic), blank))
            blank = False
            out.n_chars += sum(len(w.text) for w in words)

    out.paragraphs = _group_paragraphs(_merge_same_row(lines), scale)
    # đường kẻ vector (bảng): đoạn thẳng và hình chữ nhật mảnh
    try:
        for dr in page.get_drawings():
            for it in dr.get("items", []):
                if it[0] == "l":
                    p1, p2 = it[1], it[2]
                    x0, x1 = sorted((p1.x, p2.x))
                    y0, y1 = sorted((p1.y, p2.y))
                elif it[0] == "re":
                    r = it[1]
                    x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
                    if min(x1 - x0, y1 - y0) > 3:
                        # khung chữ nhật đủ lớn: thêm 4 cạnh
                        for e in ((x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)):
                            out.rules.append(tuple(int(v * scale) for v in e))
                        continue
                else:
                    continue
                if max(x1 - x0, y1 - y0) < 4:
                    continue
                out.rules.append((int(x0 * scale), int(y0 * scale), int(x1 * scale), int(y1 * scale)))
    except Exception:  # noqa: BLE001
        out.rules = []
    # ảnh nhúng
    W, H = page.rect.width, page.rect.height
    for img in page.get_images(full=True):
        try:
            rects = page.get_image_rects(img[0])
        except Exception:  # noqa: BLE001
            rects = []
        for r in rects:
            if r.width * r.height > 0.7 * W * H or r.width < 8 or r.height < 8:
                continue  # ảnh nền cả trang (PDF scan có lớp chữ) hoặc chấm nhỏ
            out.image_boxes.append((int(r.x0 * scale), int(r.y0 * scale), int(r.x1 * scale), int(r.y1 * scale)))
    # Chữ xoay dọc (chú thích của hình đặt ngang trang): gộp vào vùng ảnh gần nhất để giữ nguyên dạng ảnh
    if out.image_boxes and out.rotated_boxes:
        lim = 0.15 * page.rect.width * scale
        boxes = list(out.image_boxes)
        for rb in out.rotated_boxes:
            def gap(a):
                return max(0, max(a[0], rb[0]) - min(a[2], rb[2]), max(a[1], rb[1]) - min(a[3], rb[3]))
            k = min(range(len(boxes)), key=lambda i: gap(boxes[i]))
            if gap(boxes[k]) <= lim:
                boxes[k] = union_bbox([boxes[k], rb])
        out.image_boxes = boxes
    return out


def _merge_same_row(lines):
    """Gộp các "dòng" PDF nằm cùng hàng (bị tách bởi tab): '- Ký hiệu động cơ:' + 'TB2-117A'."""
    out = []
    for item in lines:
        ln, size, bold, blank = item
        if out:
            pl, psize, pbold, pblank = out[-1]
            a, b = pl.bbox, ln.bbox
            ov = min(a[3], b[3]) - max(a[1], b[1])
            if ov > 0.6 * min(a[3] - a[1], b[3] - b[1]) and b[0] >= a[2] - 2:
                words = list(ln.words)
                if b[0] - a[2] > 1.5 * (a[3] - a[1]):  # khoảng trống lớn = tab
                    w0 = words[0]
                    words[0] = Word("\t" + w0.text, w0.left, w0.top, w0.width, w0.height, w0.conf)
                out[-1] = (Line(pl.words + words), psize, pbold, pblank)
                continue
        out.append(item)
    return out


def _group_paragraphs(lines, scale: float) -> List[Paragraph]:
    """Tách đoạn: dòng trống, thụt đầu dòng, gạch đầu dòng, đổi cỡ/đậm, dòng trước ngắn."""
    if not lines:
        return []
    lefts = sorted(l.bbox[0] for l, *_ in lines)
    body_left = lefts[len(lefts) // 5] if len(lefts) >= 5 else lefts[0]
    rights = sorted(l.bbox[2] for l, *_ in lines)
    body_right = rights[int(len(rights) * 0.8)]
    indent_tol = 8 * scale  # ~8pt
    paras: List[Paragraph] = []
    cur: List[Line] = []
    cur_style = None
    prev_line: Optional[Line] = None
    for ln, size, bold, blank_before in lines:
        style = (round(size), bold)
        b = ln.bbox
        new = not cur
        if cur and prev_line is not None:
            pb = prev_line.bbox
            lh = max(1, pb[3] - pb[1])
            gap = b[1] - pb[3]
            if blank_before or gap > 0.8 * lh or b[1] < pb[1] - 2:
                new = True
            elif style != cur_style:
                new = True
            elif b[0] > body_left + indent_tol and b[0] > pb[0] + indent_tol:
                new = True  # thụt đầu dòng mới
            elif _LIST_START.match(ln.text):
                new = True
            elif pb[2] < body_right - 0.25 * (body_right - body_left) \
                    and not prev_line.text.rstrip().endswith((",", "-")):
                new = True  # dòng trước kết thúc sớm
        if new and cur:
            paras.append(_mk_para(cur, cur_style))
            cur = []
        cur.append(ln)
        cur_style = style
        prev_line = ln
    if cur:
        paras.append(_mk_para(cur, cur_style))
    return paras


def _mk_para(lines: List[Line], style) -> Paragraph:
    p = Paragraph(lines=list(lines))
    p.fixed_size_pt = float(style[0]) if style else None
    p.fixed_bold = bool(style[1][0]) if style else None
    p.italic = bool(style[1][1]) if style else False
    p.lang = "pdf"
    p.keep_breaks = True
    return p

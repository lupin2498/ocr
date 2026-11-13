"""Xuất Page -> file Word (.docx) bằng python-docx."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import List, Optional, Set

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from .document import Figure, Page, Paragraph, Table

_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


# Ký tự không được phép trong XML 1.0 (lớp chữ PDF hỏng hay chứa \x00, \x02, \x1f...) -> python-docx từ chối ghi
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


_TXT_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")


def xml_safe(text: str) -> str:
    return _XML_BAD.sub("", text) if text else text


def _clamp(v: float, lo: float, hi: float) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    if v != v:  # NaN
        return lo
    return max(lo, min(hi, v))


def _set_run_font(run, name: str, size_pt: float, bold: bool = False, italic: bool = False) -> None:
    run.font.name = name
    run.font.size = Pt(_clamp(size_pt, 1.0, 400.0))
    run.font.bold = bold
    if italic:
        run.font.italic = True
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), name)


def _set_char_spacing(run, pt: float) -> None:
    val = int(round(pt * 20))
    if not val:
        return
    rpr = run._element.get_or_add_rPr()
    sp = OxmlElement("w:spacing")
    sp.set(qn("w:val"), str(val))
    rpr.insert_element_before(sp, "w:w", "w:kern", "w:position", "w:sz", "w:szCs", "w:highlight", "w:u",
                              "w:effect", "w:bdr", "w:shd", "w:fitText", "w:vertAlign", "w:rtl", "w:cs",
                              "w:em", "w:lang", "w:eastAsianLayout", "w:specVanish", "w:oMath")


def _join_lines(lines: List[str]) -> str:
    """Nối các dòng OCR thành một đoạn; xử lý từ bị ngắt bằng gạch nối."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
        elif out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln
        else:
            out += " " + ln
    return out


class DocxExporter:
    def __init__(self, font_name: str = "Times New Roman", keep_line_breaks: bool = False,
                 page_break_between_pages: bool = True):
        self.font_name = font_name
        self.keep_line_breaks = keep_line_breaks
        self.page_break = page_break_between_pages

    def export(self, pages: List[Page], out_path: str) -> str:
        self.build(pages).save(out_path)
        return out_path

    def build(self, pages: List[Page]):
        doc = Document()
        # font mặc định
        style = doc.styles["Normal"]
        style.font.name = self.font_name
        style.font.size = Pt(12)
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(attr), self.font_name)
        style.paragraph_format.space_after = Pt(0)

        for i, page in enumerate(pages):
            section = doc.sections[0] if i == 0 else doc.add_section()
            self._setup_section(section, page)
            self._write_header_footer(section, page)
            self._write_page(doc, page)
        return doc

    def _setup_section(self, section, page: Page) -> None:
        pw = _clamp(page.page_width_in, 1.0, 22.0)  # Word: khổ giấy tối đa 22 inch
        ph = _clamp(page.page_height_in, 1.0, 22.0)
        section.orientation = WD_ORIENT.LANDSCAPE if pw > ph else WD_ORIENT.PORTRAIT
        section.page_width = Inches(pw)
        section.page_height = Inches(ph)

        def fit(a: float, b: float, total: float):
            a, b = _clamp(a, 0.0, total), _clamp(b, 0.0, total)
            room = total - 0.5  # chừa ít nhất 0,5 inch cho nội dung
            if a + b > room:
                k = max(0.0, room) / (a + b)
                a, b = a * k, b * k
            return a, b

        ml, mr = fit(page.margin_left_in, page.margin_right_in, pw)
        mt, mb = fit(page.margin_top_in, page.margin_bottom_in, ph)
        section.left_margin = Inches(ml)
        section.right_margin = Inches(mr)
        section.top_margin = Inches(mt)
        section.bottom_margin = Inches(mb)
        section.header_distance = Inches(max(0.2, min(0.5, mt - 0.35)))
        section.footer_distance = Inches(max(0.2, min(0.5, mb - 0.35)))

    def _write_header_footer(self, section, page: Page) -> None:
        for part, text in ((section.header, page.header_text), (section.footer, page.footer_text)):
            part.is_linked_to_previous = False
            para = part.paragraphs[0] if part.paragraphs else part.add_paragraph()
            for r in list(para.runs):
                r._element.getparent().remove(r._element)
            text = xml_safe(text or "").strip()
            if text:
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _set_run_font(para.add_run(text), self.font_name, 12)

    def _write_page(self, doc, page: Page) -> None:
        if not page.elements:
            doc.add_paragraph()
        for el in page.elements:
            if isinstance(el, Paragraph):
                if not el.is_empty():
                    self._write_paragraph(doc, page, el)
            elif isinstance(el, Table):
                self._write_table(doc, page, el)
            elif isinstance(el, Figure):
                self._write_figure(doc, page, el)

    def _write_paragraph(self, doc, page: Page, p: Paragraph) -> None:
        para = doc.add_paragraph()
        pf = para.paragraph_format
        para.alignment = _ALIGN.get(p.alignment, WD_ALIGN_PARAGRAPH.LEFT)
        if p.left_indent_in:
            pf.left_indent = Inches(min(p.left_indent_in, page.page_width_in / 2))
        if p.first_line_indent_in:
            pf.first_line_indent = Inches(max(-1.0, min(1.5, p.first_line_indent_in)))
        pf.space_after = Pt(_clamp(p.space_after_pt, 0.0, 300.0))
        pf.space_before = Pt(0)
        pf.widow_control = False  # không đẩy 2 dòng sang trang sau như bản gốc không có
        if p.line_pitch_pt and p.line_pitch_pt > 0:
            pf.line_spacing = Pt(_clamp(p.line_pitch_pt, 1.0, 600.0))  # "Exactly" như bản gốc -> trang không tràn
        else:
            pf.line_spacing = _clamp(p.line_spacing or 1.0, 0.5, 5.0)
        lines = [xml_safe(ln.text) for ln in p.lines if not ln.is_empty()]
        # tab stop tại vị trí cột thứ hai (dòng "nhãn: <tab> giá trị" trong PDF)
        for ln in p.lines:
            for wd in ln.words:
                if wd.text.startswith("\t"):
                    x_in = page.px_to_in(wd.left) - page.margin_left_in - (pf.left_indent.inches if pf.left_indent else 0)
                    if x_in > 0.1:
                        try:
                            pf.tab_stops.add_tab_stop(Inches(round(x_in, 2)))
                        except ValueError:
                            pass
        if self.keep_line_breaks:
            for k, text in enumerate(lines):
                run = para.add_run(text)
                _set_run_font(run, self.font_name, p.font_size_pt, p.bold, p.italic)
                if k < len(lines) - 1:
                    run.add_break(WD_BREAK.LINE)
        else:
            run = para.add_run(_join_lines(lines).replace(" \t", "\t"))
            _set_run_font(run, self.font_name, p.font_size_pt, p.bold, p.italic)
            _set_char_spacing(run, p.char_spacing_pt)

    def _write_figure(self, doc, page: Page, f: Figure) -> None:
        import io

        import cv2
        import numpy as np

        img = f.image
        if img is None or getattr(img, "size", 0) == 0:
            return
        # giới hạn độ phân giải ảnh nhúng ~300 dpi để file Word không quá nặng
        scale = min(1.0, 300.0 / max(1.0, page.dpi))
        if scale < 0.98:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        photo = len(np.unique(gray[::4, ::4])) > 64
        ok, buf = cv2.imencode(".jpg" if photo else ".png", img,
                               [cv2.IMWRITE_JPEG_QUALITY, 90] if photo else [cv2.IMWRITE_PNG_COMPRESSION, 6])
        if not ok:
            return
        width_in = page.px_to_in(f.bbox[2] - f.bbox[0])
        max_w = page.page_width_in - page.margin_left_in - page.margin_right_in
        width_in = max(0.3, min(width_in, max_w - f.left_indent_in))
        # không vượt chiều cao vùng in (tránh tràn sang trang sau)
        max_h = page.page_height_in - page.margin_top_in - page.margin_bottom_in - 0.35
        h_px, w_px = img.shape[:2]
        if w_px and width_in * h_px / w_px > max_h > 0:
            width_in = max_h * w_px / h_px
        para = doc.add_paragraph()
        para.alignment = _ALIGN.get(f.alignment, WD_ALIGN_PARAGRAPH.CENTER)
        pf = para.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(_clamp(f.space_after_pt, 0.0, 300.0))
        if f.left_indent_in:
            pf.left_indent = Inches(f.left_indent_in)
        para.add_run().add_picture(io.BytesIO(buf.tobytes()), width=Inches(width_in))

    def _write_table(self, doc, page: Page, t: Table) -> None:
        if t.n_rows < 1 or t.n_cols < 1:
            return
        table = doc.add_table(rows=t.n_rows, cols=t.n_cols)
        table.style = "Table Grid"
        table.autofit = False  # python-docx ghi <w:tblLayout w:type="fixed"/> đúng vị trí
        table.alignment = WD_TABLE_ALIGNMENT.LEFT
        tbl_pr = table._tbl.tblPr
        # thụt bảng theo vị trí thật trên trang (w:tblInd phải đứng trước tblBorders/tblLayout/tblLook)
        indent_in = page.px_to_in(t.bbox[0]) - page.margin_left_in
        if indent_in > 0.05:
            ind = OxmlElement("w:tblInd")
            ind.set(qn("w:w"), str(int(min(indent_in, 10.0) * 1440)))
            ind.set(qn("w:type"), "dxa")
            tbl_pr.insert_element_before(ind, "w:tblBorders", "w:shd", "w:tblLayout", "w:tblCellMar",
                                         "w:tblLook", "w:tblCaption", "w:tblDescription", "w:tblPrChange")
        col_widths = [max(0.05, page.px_to_in(t.col_edges[c + 1] - t.col_edges[c])) for c in range(t.n_cols)]
        # lưới cột (w:tblGrid) – Word/LibreOffice dùng giá trị này để dàn cột
        grid = table._tbl.tblGrid
        for gc, w_in in zip(grid.findall(qn("w:gridCol")), col_widths):
            gc.set(qn("w:w"), str(int(w_in * 1440)))
        for c, w_in in enumerate(col_widths):
            for r in range(t.n_rows):
                table.cell(r, c).width = Inches(w_in)
        for r in range(t.n_rows):
            row_h = page.px_to_in(t.row_edges[r + 1] - t.row_edges[r])
            table.rows[r].height = Inches(_clamp(row_h, 0.15, 20.0))

        for cell_info in t.cells:
            r, c = cell_info.row, cell_info.col
            if not (0 <= r < t.n_rows and 0 <= c < t.n_cols):
                continue  # ô nằm ngoài lưới (phát hiện bảng lỗi): bỏ qua thay vì làm hỏng cả file
            cell = table.cell(r, c)
            if cell_info.row_span > 1 or cell_info.col_span > 1:
                r2 = min(t.n_rows, r + max(1, cell_info.row_span)) - 1
                c2 = min(t.n_cols, c + max(1, cell_info.col_span)) - 1
                try:
                    cell = cell.merge(table.cell(r2, c2))
                except Exception:  # noqa: BLE001 - ô gộp chồng lấn: giữ ô đơn
                    pass
            para = cell.paragraphs[0]
            para.alignment = _ALIGN.get(cell_info.alignment, WD_ALIGN_PARAGRAPH.LEFT)
            para.paragraph_format.space_after = Pt(0)
            lines = [xml_safe(ln) for ln in cell_info.lines if ln.strip()]
            for k, text in enumerate(lines):
                run = para.add_run(text)
                _set_run_font(run, self.font_name, t.font_size_pt, cell_info.bold)
                if k < len(lines) - 1:
                    run.add_break(WD_BREAK.LINE)
        # khoảng cách sau bảng
        after = doc.add_paragraph()
        after.paragraph_format.space_after = Pt(_clamp(t.space_after_pt, 0.0, 300.0))
        _set_run_font(after.add_run(""), self.font_name, 2)


def available_path(target: Path, attempt: int) -> Path:
    return target if attempt == 0 else target.with_name(f"{target.stem} ({attempt}){target.suffix}")


def _path_key(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def unique_output(target: Path, used: Set[str]) -> Path:
    """Tên file kết quả chưa dùng trong lượt chạy này: hai đầu vào trùng tên (a\\scan.png + b\\scan.png với
    cùng thư mục kết quả, hoặc scan.png + scan.pdf) không được ghi đè lên nhau -> scan.docx, scan (2).docx..."""
    cand, n = target, 1
    while _path_key(cand) in used:
        n += 1
        cand = target.with_name(f"{target.stem} ({n}){target.suffix}")
    used.add(_path_key(cand))
    return cand


def remember_output(path, used: Set[str]) -> None:
    used.add(_path_key(path))


def check_writable_dir(d: Path) -> Optional[str]:
    """None nếu tạo/ghi được file trong thư mục `d`; nếu không: thông báo lỗi dễ hiểu (kiểm tra trước khi OCR,
    tránh OCR xong cả lô mới biết không lưu được kết quả)."""
    try:
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(d), prefix=".ocr2word_", suffix=".tmp"):
            pass
    except OSError as e:
        return f"Không ghi được vào thư mục {d} ({e.strerror or e}). Hãy chọn thư mục kết quả khác."
    return None


def export_docx(pages: List[Page], out_path: str, font_name: str = "Times New Roman",
                keep_line_breaks: bool = False) -> str:
    """Ghi file Word; nếu file đích đang mở trong Word (bị khoá) thì ghi sang "tên (1).docx", "tên (2).docx"...
    Trả về đường dẫn thực sự đã ghi."""
    doc = DocxExporter(font_name=font_name, keep_line_breaks=keep_line_breaks).build(pages)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception = PermissionError(str(target))
    for attempt in range(20):
        cand = available_path(target, attempt)
        try:
            doc.save(str(cand))
            return str(cand)
        except PermissionError as e:
            last_err = e
    raise last_err


def write_text(pages: List[Page], out_path: str) -> str:
    """Xuất .txt (UTF-8 có BOM để Notepad cũ nhận đúng tiếng Việt); file đang khoá thì đổi tên như .docx."""
    target = Path(out_path)
    text = _TXT_BAD.sub("", "\n\n".join(p.text for p in pages))
    last_err: Exception = PermissionError(str(target))
    for attempt in range(20):
        cand = available_path(target, attempt)
        try:
            cand.write_text(text, encoding="utf-8-sig", errors="replace")
            return str(cand)
        except PermissionError as e:
            last_err = e
    raise last_err

"""Xuất Page -> file Word (.docx) bằng python-docx."""
from __future__ import annotations

from typing import List, Optional

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


def _set_run_font(run, name: str, size_pt: float, bold: bool = False, italic: bool = False) -> None:
    run.font.name = name
    run.font.size = Pt(size_pt)
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
        doc.save(out_path)
        return out_path

    def _setup_section(self, section, page: Page) -> None:
        section.orientation = WD_ORIENT.LANDSCAPE if page.landscape else WD_ORIENT.PORTRAIT
        section.page_width = Inches(page.page_width_in)
        section.page_height = Inches(page.page_height_in)
        section.left_margin = Inches(page.margin_left_in)
        section.right_margin = Inches(page.margin_right_in)
        section.top_margin = Inches(page.margin_top_in)
        section.bottom_margin = Inches(page.margin_bottom_in)
        section.header_distance = Inches(max(0.2, min(0.5, page.margin_top_in - 0.35)))
        section.footer_distance = Inches(max(0.2, min(0.5, page.margin_bottom_in - 0.35)))

    def _write_header_footer(self, section, page: Page) -> None:
        for part, text in ((section.header, page.header_text), (section.footer, page.footer_text)):
            part.is_linked_to_previous = False
            para = part.paragraphs[0] if part.paragraphs else part.add_paragraph()
            for r in list(para.runs):
                r._element.getparent().remove(r._element)
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
        pf.space_after = Pt(p.space_after_pt)
        pf.space_before = Pt(0)
        pf.widow_control = False  # không đẩy 2 dòng sang trang sau như bản gốc không có
        if p.line_pitch_pt:
            pf.line_spacing = Pt(p.line_pitch_pt)  # "Exactly" như bản gốc -> trang không bị tràn
        else:
            pf.line_spacing = p.line_spacing
        lines = [ln.text for ln in p.lines if not ln.is_empty()]
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
        pf.space_after = Pt(f.space_after_pt)
        if f.left_indent_in:
            pf.left_indent = Inches(f.left_indent_in)
        para.add_run().add_picture(io.BytesIO(buf.tobytes()), width=Inches(width_in))

    def _write_table(self, doc, page: Page, t: Table) -> None:
        if t.n_rows < 1 or t.n_cols < 1:
            return
        table = doc.add_table(rows=t.n_rows, cols=t.n_cols)
        table.style = "Table Grid"
        table.autofit = False
        table.alignment = WD_TABLE_ALIGNMENT.LEFT
        # thụt bảng theo vị trí thật trên trang
        indent_in = page.px_to_in(t.bbox[0]) - page.margin_left_in
        if indent_in > 0.05:
            tbl_pr = table._tbl.tblPr
            ind = OxmlElement("w:tblInd")
            ind.set(qn("w:w"), str(int(indent_in * 1440)))
            ind.set(qn("w:type"), "dxa")
            tbl_pr.append(ind)
        col_widths = [page.px_to_in(t.col_edges[c + 1] - t.col_edges[c]) for c in range(t.n_cols)]
        # lưới cột (w:tblGrid) – Word/LibreOffice dùng giá trị này để dàn cột
        grid = table._tbl.tblGrid
        for gc, w_in in zip(grid.findall(qn("w:gridCol")), col_widths):
            gc.set(qn("w:w"), str(int(w_in * 1440)))
        tbl_pr = table._tbl.tblPr
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        tbl_pr.append(layout)
        for c, w_in in enumerate(col_widths):
            for r in range(t.n_rows):
                table.cell(r, c).width = Inches(w_in)
        for r in range(t.n_rows):
            row_h = page.px_to_in(t.row_edges[r + 1] - t.row_edges[r])
            table.rows[r].height = Inches(max(0.15, row_h))

        for cell_info in t.cells:
            r, c = cell_info.row, cell_info.col
            cell = table.cell(r, c)
            if cell_info.row_span > 1 or cell_info.col_span > 1:
                other = table.cell(r + cell_info.row_span - 1, c + cell_info.col_span - 1)
                cell = cell.merge(other)
            para = cell.paragraphs[0]
            para.alignment = _ALIGN.get(cell_info.alignment, WD_ALIGN_PARAGRAPH.LEFT)
            para.paragraph_format.space_after = Pt(0)
            lines = [ln for ln in cell_info.lines if ln.strip()]
            for k, text in enumerate(lines):
                run = para.add_run(text)
                _set_run_font(run, self.font_name, t.font_size_pt, cell_info.bold)
                if k < len(lines) - 1:
                    run.add_break(WD_BREAK.LINE)
        # khoảng cách sau bảng
        after = doc.add_paragraph()
        after.paragraph_format.space_after = Pt(t.space_after_pt)
        _set_run_font(after.add_run(""), self.font_name, 2)


def export_docx(pages: List[Page], out_path: str, font_name: str = "Times New Roman",
                keep_line_breaks: bool = False) -> str:
    return DocxExporter(font_name=font_name, keep_line_breaks=keep_line_breaks).export(pages, out_path)

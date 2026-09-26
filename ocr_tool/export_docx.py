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

from .document import Page, Paragraph, Table

_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


def _set_run_font(run, name: str, size_pt: float, bold: bool = False) -> None:
    run.font.name = name
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), name)


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

    def _write_page(self, doc, page: Page) -> None:
        if not page.elements:
            doc.add_paragraph()
        for el in page.elements:
            if isinstance(el, Paragraph):
                self._write_paragraph(doc, page, el)
            elif isinstance(el, Table):
                self._write_table(doc, page, el)

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
        pf.line_spacing = p.line_spacing
        lines = [ln.text for ln in p.lines if not ln.is_empty()]
        if self.keep_line_breaks:
            for k, text in enumerate(lines):
                run = para.add_run(text)
                _set_run_font(run, self.font_name, p.font_size_pt, p.bold)
                if k < len(lines) - 1:
                    run.add_break(WD_BREAK.LINE)
        else:
            run = para.add_run(_join_lines(lines))
            _set_run_font(run, self.font_name, p.font_size_pt, p.bold)

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

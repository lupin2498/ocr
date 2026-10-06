"""Kiểm thử đọc lớp chữ PDF (Unicode và TCVN3), bảng kẻ vector, ảnh nhúng, làm sạch OCR."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool.document import Figure, Paragraph, Table  # noqa: E402
from ocr_tool.export_docx import export_docx  # noqa: E402
from ocr_tool.legacy_encoding import decode_tcvn3, is_upper_font, looks_tcvn3  # noqa: E402
from ocr_tool.ocr_cleanup import clean_ocr_text  # noqa: E402
from ocr_tool.pipeline import Options, Pipeline  # noqa: E402
from ocr_tool.spellfix import restore_u_hyphen  # noqa: E402

FONT = ROOT / "tests" / "fonts" / "DejaVuSerif.ttf"


def test_tcvn3_decode():
    assert restore_u_hyphen(decode_tcvn3("§éng c¬ ®-îc thiÕt kÕ cho trùc th¨ng")) == \
        "Động cơ được thiết kế cho trực thăng"
    assert decode_tcvn3("§ÆC TÝNH Kü THUËT CñA §éNG C¥", upper=True) == "ĐẶC TÍNH KỸ THUẬT CỦA ĐỘNG CƠ"
    assert restore_u_hyphen(decode_tcvn3("Ch-¬ng 1")) == "Chương 1"
    assert looks_tcvn3("thiÕt kÕ cho trùc th¨ng")
    assert not looks_tcvn3("thiết kế cho trực thăng")
    assert is_upper_font("ABCDEE+.VnTimeH,Bold") and not is_upper_font("ABCDEE+.VnTime,Bold")


def test_ocr_cleanup():
    assert clean_ocr_text("áp suất 3,5 kG/cm”.") == "áp suất 3,5 kG/cm2."
    assert clean_ocr_text("động cơ TB2-I17A") == "động cơ TB2-117A"
    assert clean_ocr_text("| 55 |") == "55"
    for s in ("HP-40BA", "Mi-8", "Il-76", "Động cơ"):
        assert clean_ocr_text(s) == s


def _make_pdf(path: Path, tcvn3: bool) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    fontname = ".VnTime" if tcvn3 else "dejavu"
    page.insert_font(fontname=fontname, fontfile=str(FONT))
    if tcvn3:
        lines = ["§éng c¬ TB2-117A ®-îc thiÕt kÕ cho trùc th¨ng Mi-8.",
                 "Kh«ng khÝ ®-îc nÐn theo h-íng trôc, nh- vËy ¸p suÊt t¨ng."]
    else:
        lines = ["Động cơ TB2-117A được thiết kế cho trực thăng Mi-8.",
                 "Không khí được nén theo hướng trục, như vậy áp suất tăng."]
    y = 90
    for ln in lines:
        page.insert_text((80, y), ln, fontname=fontname, fontsize=13)
        y += 20
    # bảng 2x3 kẻ bằng đường vector
    xs, ys = [80, 250, 380, 510], [160, 190, 220]
    for yy in ys:
        page.draw_line((xs[0], yy), (xs[-1], yy), width=0.5)
    for xx in xs:
        page.draw_line((xx, ys[0]), (xx, ys[-1]), width=0.5)
    cells = [["Tầng nén", "1", "2"], ["Số lượng lá", "21", "23"]] if not tcvn3 else \
        [["TÇng nÐn", "1", "2"], ["Sè l-îng l¸", "21", "23"]]
    for r, row in enumerate(cells):
        for c, txt in enumerate(row):
            page.insert_text((xs[c] + 6, ys[r] + 20), txt, fontname=fontname, fontsize=12)
    # ảnh nhúng + chú thích
    arr = (np.random.default_rng(0).random((120, 200)) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    page.insert_image(pymupdf.Rect(150, 260, 450, 440), stream=buf.getvalue())
    cap = "Hình 2.1. Vỏ máy nén" if not tcvn3 else "H×nh 2.1. Vá m¸y nÐn"
    page.insert_text((230, 460), cap, fontname=fontname, fontsize=12)
    page.insert_text((290, 810), "15", fontname=fontname, fontsize=12)
    doc.save(str(path))


@pytest.mark.parametrize("tcvn3", [False, True])
def test_pdf_text_layer(tmp_path, tcvn3):
    pdf = tmp_path / ("tcvn3.pdf" if tcvn3 else "unicode.pdf")
    _make_pdf(pdf, tcvn3)
    pages = Pipeline(Options()).process_file(str(pdf))
    page = pages[0]
    paras = [e for e in page.elements if isinstance(e, Paragraph)]
    text = " ".join(" ".join(p.text.split()) for p in paras)
    assert "Động cơ TB2-117A được thiết kế cho trực thăng Mi-8." in text, text
    assert "Không khí được nén theo hướng trục, như vậy áp suất tăng." in text, text
    assert "Hình 2.1. Vỏ máy nén" in text
    assert all(p.lang == "pdf" for p in paras)
    tables = [e for e in page.elements if isinstance(e, Table)]
    assert len(tables) == 1 and (tables[0].n_rows, tables[0].n_cols) == (2, 3)
    grid = {(c.row, c.col): c.text for c in tables[0].cells}
    assert grid[(1, 0)] == "Số lượng lá" and grid[(1, 2)] == "23"
    figs = [e for e in page.elements if isinstance(e, Figure)]
    assert len(figs) == 1
    # số trang -> footer
    assert page.footer_text == "15"
    out = tmp_path / "out.docx"
    export_docx(pages, str(out))
    from docx import Document
    d = Document(str(out))
    assert len(d.inline_shapes) == 1 and len(d.tables) == 1
    assert d.sections[0].footer.paragraphs[0].text == "15"


def test_pdf_text_can_be_disabled(tmp_path):
    pdf = tmp_path / "u.pdf"
    _make_pdf(pdf, False)
    page = Pipeline(Options(use_pdf_text=False)).process_file(str(pdf))[0]
    assert not any(isinstance(e, Paragraph) and e.lang == "pdf" for e in page.elements)

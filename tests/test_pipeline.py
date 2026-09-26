"""Kiểm thử đầu-cuối: ảnh mẫu 3 ngôn ngữ + bảng -> Page -> .docx."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool.config import find_tesseract, list_languages  # noqa: E402
from ocr_tool.document import Paragraph, Table  # noqa: E402
from ocr_tool.export_docx import export_docx  # noqa: E402
from ocr_tool.lang import choose_lang, to_tesseract_langs  # noqa: E402
from ocr_tool.pipeline import Options, Pipeline  # noqa: E402
from tests.make_sample import TABLE, find_times_fonts, make_sample  # noqa: E402

def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]



TESS = find_tesseract()
HAVE_LANGS = set(list_languages(TESS)) if TESS else set()
needs_tesseract = pytest.mark.skipif(
    not TESS or not {"vie", "eng", "rus"} <= HAVE_LANGS,
    reason="cần Tesseract với vie+eng+rus",
)


def test_lang_mapping():
    assert to_tesseract_langs(["vi", "en", "ru", "vi"]) == ["vie", "eng", "rus"]
    avail = ["vie", "eng", "rus"]
    assert choose_lang("Это русский текст для проверки", avail) == "rus"
    assert choose_lang("Đây là đoạn văn bản tiếng Việt có dấu", avail) == "vie"
    assert choose_lang("This is plain English text without accents", avail) == "eng"
    assert choose_lang("Гайка M12 for engine block", avail) == "rus+eng"
    assert choose_lang("ab", avail) == "vie+eng+rus"


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    img = tmp_path_factory.mktemp("in") / "sample.png"
    make_sample(str(img))
    pipe = Pipeline(Options(tesseract_cmd=TESS))
    return pipe.process_file(str(img))


@needs_tesseract
def test_text_all_languages(pages):
    assert len(pages) == 1
    text = " ".join(pages[0].text.split())
    for frag in ("tiếng Việt", "bố cục", "English paragraph", "Word document",
                 "русский абзац", "таблицы"):
        assert frag in text, f"thiếu: {frag}\n{text}"


@needs_tesseract
def test_layout_properties(pages):
    page = pages[0]
    paras = [e for e in page.elements if isinstance(e, Paragraph)]
    title = paras[0]
    assert _levenshtein(title.text, "BÁO CÁO TỔNG HỢP KẾT QUẢ") <= 3, title.text
    assert title.alignment == "center" and title.bold
    assert title.font_size_pt > paras[1].font_size_pt
    body = paras[1]
    assert body.alignment == "left"
    assert 0.3 <= body.first_line_indent_in <= 0.5
    assert 9 <= body.font_size_pt <= 12
    sign = paras[-1]
    assert "Hà Nội" in sign.text and sign.alignment == "right"
    assert abs(page.page_width_in - 8.27) < 0.05 and not page.landscape


@needs_tesseract
def test_table_structure(pages):
    tables = [e for e in pages[0].elements if isinstance(e, Table)]
    assert len(tables) == 1
    t = tables[0]
    assert (t.n_rows, t.n_cols) == (4, 4)
    assert len(t.cells) == 16
    grid = {(c.row, c.col): c.text for c in t.cells}
    for r, row in enumerate(TABLE):
        for c, expected in enumerate(row):
            assert _levenshtein(grid[(r, c)], expected) <= 1, f"ô ({r},{c}): {grid[(r, c)]!r} != {expected!r}"
    # cột STT / Số lượng canh giữa, cột tên canh trái
    assert {c.alignment for c in t.cells if c.col == 0} == {"center"}
    assert {c.alignment for c in t.cells if c.col == 1} == {"left"}
    # bảng nằm sau 4 đoạn văn và trước dòng ký tên
    idx = pages[0].elements.index(t)
    assert idx == 4 and isinstance(pages[0].elements[-1], Paragraph)


@needs_tesseract
def test_export_docx(pages, tmp_path):
    from docx import Document

    out = tmp_path / "out.docx"
    export_docx(pages, str(out))
    doc = Document(str(out))
    assert len(doc.tables) == 1
    tbl = doc.tables[0]
    assert len(tbl.rows) == 4 and len(tbl.columns) == 4
    assert _levenshtein(tbl.cell(3, 3).text, "Проверено") <= 1
    assert _levenshtein(tbl.cell(0, 1).text, TABLE[0][1]) <= 1
    full = "\n".join(p.text for p in doc.paragraphs)
    assert "English paragraph" in full and "русский" in full
    assert _levenshtein(doc.paragraphs[0].text, "BÁO CÁO TỔNG HỢP KẾT QUẢ") <= 3
    sec = doc.sections[0]
    assert abs(sec.page_width.inches - 8.27) < 0.05


@needs_tesseract
def test_cli(tmp_path):
    from ocr_tool.cli import main

    img = tmp_path / "a.png"
    make_sample(str(img))
    rc = main([str(img), "-o", str(tmp_path / "out"), "--txt", "--tesseract", TESS])
    assert rc == 0
    assert (tmp_path / "out" / "a.docx").exists()
    assert "tiếng Việt" in (tmp_path / "out" / "a.txt").read_text(encoding="utf-8")


def test_merged_cells_grid():
    """Lưới có ô gộp: 2 hàng x 3 cột, hàng đầu gộp cột 1-2."""
    import numpy as np
    from ocr_tool.tables import detect_tables

    img = np.full((600, 900), 255, dtype=np.uint8)
    xs, ys = [100, 350, 600, 850], [100, 300, 500]
    for y in ys:
        img[y - 2:y + 2, xs[0]:xs[-1]] = 0
    for x in xs:
        img[ys[0]:ys[-1], x - 2:x + 2] = 0
    # xoá đoạn kẻ dọc giữa cột 1 và 2 ở hàng đầu -> ô gộp
    img[ys[0] + 3:ys[1] - 3, xs[2] - 4:xs[2] + 4] = 255
    grids = detect_tables(img, text_height=30)
    assert len(grids) == 1
    g = grids[0]
    assert (g.n_rows, g.n_cols) == (2, 3)
    spans = {(c.row, c.col): (c.row_span, c.col_span) for c in g.cells}
    assert spans[(0, 1)] == (1, 2)
    assert (0, 2) not in spans and len(g.cells) == 5


@needs_tesseract
@pytest.mark.skipif(find_times_fonts() is None, reason="không có font Times New Roman / Liberation Serif")
def test_times_font_document(tmp_path):
    """Ảnh mẫu vẽ bằng font Times (phổ biến nhất trong văn bản tiếng Việt): kiểm tra với dung sai OCR."""
    reg, bold = find_times_fonts()
    img = tmp_path / "times.png"
    make_sample(str(img), font_path=reg, bold_font_path=bold)
    page = Pipeline(Options(tesseract_cmd=TESS)).process_file(str(img))[0]
    text = " ".join(page.text.split())
    for frag in ("tiếng Việt", "English paragraph", "Word document", "русский абзац"):
        assert frag in text, f"thiếu: {frag}\n{text}"
    paras = [e for e in page.elements if isinstance(e, Paragraph)]
    tables = [e for e in page.elements if isinstance(e, Table)]
    assert paras[0].alignment == "center" and paras[0].bold
    body = paras[1]
    assert len(body.lines) == 2, "đoạn thụt đầu dòng bị tách làm hai"
    assert body.alignment == "left" and 0.3 <= body.first_line_indent_in <= 0.5
    assert paras[-1].alignment == "right"
    assert len(tables) == 1 and (tables[0].n_rows, tables[0].n_cols) == (4, 4)
    grid = {(c.row, c.col): c.text for c in tables[0].cells}
    for r, row in enumerate(TABLE):
        for c, expected in enumerate(row):
            got = grid[(r, c)]
            assert _levenshtein(got, expected) <= 1, f"ô ({r},{c}): {got!r} != {expected!r}"

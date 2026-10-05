"""Kiểm thử nhận diện hình vẽ / ảnh và giữ nguyên ảnh khi xuất Word."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool.config import find_tesseract, list_languages  # noqa: E402
from ocr_tool.document import Figure, Paragraph, Table  # noqa: E402
from ocr_tool.export_docx import export_docx  # noqa: E402
from ocr_tool.pipeline import Options, Pipeline  # noqa: E402
from tests.make_figure_sample import LABELS, make_figure_sample  # noqa: E402

TESS = find_tesseract()
needs_tesseract = pytest.mark.skipif(
    not TESS or not {"vie", "eng", "rus"} <= set(list_languages(TESS)), reason="cần Tesseract vie+eng+rus")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    img = tmp_path_factory.mktemp("fig") / "figure_sample.png"
    make_figure_sample(str(img))
    return Pipeline(Options(tesseract_cmd=TESS)).process_file(str(img))


@needs_tesseract
def test_figures_detected(page):
    els = page[0].elements
    figs = [e for e in els if isinstance(e, Figure)]
    assert len(figs) == 2, [type(e).__name__ for e in els]
    for f in figs:
        assert f.image is not None and f.image.shape[0] > 100 and f.image.shape[1] > 300
        assert f.alignment == "center"


@needs_tesseract
def test_labels_not_ocr_text_and_captions_kept(page):
    paras = [e for e in page[0].elements if isinstance(e, Paragraph)]
    text = "\n".join(p.text for p in paras)
    for lab in LABELS:
        assert lab not in text, f"nhãn trong hình bị OCR: {lab}"
    assert "Hình 2.1. Vỏ máy nén" in text
    assert "Hình 2.2" in text
    # thứ tự: đoạn 1, hình 1, chú thích 1, đoạn 2, hình 2, chú thích 2, bảng
    kinds = [type(e).__name__ for e in page[0].elements]
    assert kinds == ["Paragraph", "Figure", "Paragraph", "Paragraph", "Figure", "Paragraph", "Table"], kinds


@needs_tesseract
def test_spelling_fixed_in_pipeline(page):
    text = " ".join(page[0].text.split())
    for frag in ("được lắp trước buồng đốt", "theo hướng trục", "Lượng không khí", "như vậy bảo đảm"):
        assert frag in text, (frag, text)
    assert "-ợc" not in text and "—" not in text


@needs_tesseract
def test_real_table_kept(page):
    tables = [e for e in page[0].elements if isinstance(e, Table)]
    assert len(tables) == 1 and (tables[0].n_rows, tables[0].n_cols) == (2, 4)


@needs_tesseract
def test_export_with_images(page, tmp_path):
    from docx import Document

    out = tmp_path / "fig.docx"
    export_docx(page, str(out))
    doc = Document(str(out))
    assert len(doc.inline_shapes) == 2
    assert len(doc.tables) == 1


@needs_tesseract
def test_figures_can_be_disabled(tmp_path):
    img = tmp_path / "f.png"
    make_figure_sample(str(img))
    pg = Pipeline(Options(tesseract_cmd=TESS, detect_figures=False, spellfix=False)).process_file(str(img))[0]
    assert not any(isinstance(e, Figure) for e in pg.elements)

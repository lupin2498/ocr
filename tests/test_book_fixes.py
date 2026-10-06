"""Kiểm thử các lỗi tìm ra khi đối chiếu sách scan 'Bản đồ về dòng tiền'."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool.config import find_tesseract, list_languages  # noqa: E402
from ocr_tool.document import Figure, Paragraph, Table  # noqa: E402
from ocr_tool.ocr_cleanup import clean_ocr_text  # noqa: E402
from ocr_tool.pipeline import Options, Pipeline  # noqa: E402
from ocr_tool.spellfix import DocModel, SpellFixer, get_lexicon  # noqa: E402
from tests.make_sample import FONT_CANDIDATES, _font  # noqa: E402
from tests.make_scan_sample import BODY, BOX_TEXT, BOX_TITLE, LABELS, make_scan_sample  # noqa: E402

TESS = find_tesseract()
needs_tesseract = pytest.mark.skipif(
    not TESS or not {"vie", "eng", "rus"} <= set(list_languages(TESS)), reason="cần Tesseract vie+eng+rus")


def test_toc_leaders_and_homoglyphs():
    assert clean_ocr_text("PL (Báo cáo kết quả hoạt động kinh doanh)........66") == \
        "PL (Báo cáo kết quả hoạt động kinh doanh)\t66"
    assert clean_ocr_text("CÔNG TY?..zmrrtrrn2rrt2trEarnroerrerorrnrrervrvoneunseevstarn, 24") == "CÔNG TY?\t24"
    assert clean_ocr_text("PHAN КЁТ.........оаенаеоненеооеооосооооововоовао 226") == "PHAN KÊT\t226"
    assert clean_ocr_text("PHAN Ш. BẢN THÂN") == "PHAN III. BẢN THÂN"
    for s in ("Это русский абзац", "Hệ thống СΠЗ-15 lắp", "Năm 2010... rồi 2011", "Bookkeeping là gì 12"):
        assert clean_ocr_text(s) == s


def test_systematic_errors_fixed_by_dictionary():
    # lỗi lặp lại có hệ thống: tài liệu "học" luôn cái sai, từ điển từ ghép phải thắng
    doc = ["cắt giảm chỉ phí", "giảm chỉ phí", "chỉ phí thuê ngoài"] * 30 + ["là tỉnh thần cống hiến"] * 3
    f = SpellFixer(get_lexicon(), DocModel.build(doc))
    assert f.fix_line("cắt giảm chỉ phí mỗi năm") == "cắt giảm chi phí mỗi năm"
    assert f.fix_line("là tỉnh thần cống hiến") == "là tinh thần cống hiến"
    assert f.fix_line("“Ghi số” là một phần của kế toán") == "“Ghi sổ” là một phần của kế toán"
    assert f.fix_line("chắc hắn nhiều người sẽ") == "chắc hẳn nhiều người sẽ"
    assert f.fix_line("có thể an tâm đề hoàn thành công việc") == "có thể an tâm để hoàn thành công việc"


def test_no_false_compound_swaps():
    f = SpellFixer(get_lexicon(), DocModel())
    for s in ("ta sẽ bắt đầu từ bảng cân đối kế toán", "Bởi có bản đồ, bạn sẽ biết",
              "Thời gian để tài sản quy đổi", "thay vì vay nợ", "Kinh doanh sở thú",
              "đây là vấn đề lớn", "như đã đề cập ở trên", "các công tắc điện", "khuếch đại tín hiệu",
              "Các đầu tự do của tay lắc"):
        assert f.fix_line(s) == s, (s, f.fix_line(s))


@needs_tesseract
def test_scanned_page_frame_and_edges(tmp_path):
    img = tmp_path / "scan.png"
    make_scan_sample(str(img))
    page = Pipeline(Options(tesseract_cmd=TESS)).process_file(str(img))[0]
    kinds = [type(e).__name__ for e in page.elements]
    assert kinds.count("Figure") == 1, kinds          # chỉ sơ đồ là hình
    assert "Table" not in kinds, kinds                 # khung bao không phải bảng
    fig = next(e for e in page.elements if isinstance(e, Figure))
    assert fig.bbox[2] - fig.bbox[0] < 0.75 * page.width_px  # không nuốt cả trang
    text = " ".join(" ".join(p.text.split()) for p in page.elements if isinstance(p, Paragraph))
    for frag in (BODY[:40], BOX_TITLE, BOX_TEXT[:40]):
        assert frag in text, (frag, text)
    for lab in LABELS:
        assert lab not in text


@needs_tesseract
def test_running_header_removed(tmp_path):
    font = _font(FONT_CANDIDATES, 34)
    paths = []
    bodies = ["Kế toán là ngôn ngữ chung của thế giới kinh doanh.",
              "Dòng tiền cho biết tiền vào và ra khỏi công ty.",
              "Bảng cân đối kế toán gồm tài sản và nguồn vốn.",
              "Doanh thu trừ chi phí bằng lợi nhuận.",
              "Cổ đông là chủ sở hữu của công ty."]
    for k, body in enumerate(bodies):
        im = Image.new("RGB", (1500, 2100), "white")
        d = ImageDraw.Draw(im)
        d.text((150, 60), "BẢN ĐỒ VỀ DÒNG TIỀN", font=font, fill="black")
        d.text((150, 600), body, font=font, fill="black")
        d.text((740, 1980), str(20 + k), font=font, fill="black")
        p = tmp_path / f"p{k}.png"
        im.save(p, dpi=(250, 250))
        paths.append(str(p))
    from ocr_tool.pipeline import strip_running_headers
    pipe = Pipeline(Options(tesseract_cmd=TESS))
    pages = [pipe.process_file(p)[0] for p in paths]
    strip_running_headers(pages)
    for pg, body in zip(pages, bodies):
        text = " ".join(e.text for e in pg.elements if isinstance(e, Paragraph))
        assert "DÒNG TIỀN" not in text and "BẢN ĐỒ" not in text, text
        assert body[:20] in " ".join(text.split())


def test_scan_font_fits_original_line_width():
    """Trang scan: cỡ chữ phải đủ nhỏ để Times New Roman ngắt dòng như bản gốc (không tràn trang)."""
    from ocr_tool.document import Line, Word
    from ocr_tool.layout import build_page, text_width_em

    dpi = 300
    texts = ["Không ít người bắt đầu công việc kế toán bằng việc học",
             "“Ghi sổ” (Bookkeeping). “Ghi sổ” là một phần của kế toán.",
             "Việc ghi chép sổ sách như thế nào cho đúng đương nhiên là",
             "điều hết sức quan trọng."]
    lines, top = [], 300
    for k, t in enumerate(texts):
        width = 1500 if k < 3 else 600  # dòng đầy ~5 inch; chữ thật cao ~60px (~14pt)
        n = len(t.split())
        step = width // n
        lines.append(Line([Word(w, 300 + i * step, top, step - 10, 60, 95) for i, w in enumerate(t.split())]))
        top += 90
    para = Paragraph(lines=lines)
    page = build_page(2480, 3508, dpi, [para])
    p = page.elements[0]
    for ln in lines[:3]:
        need_pt = text_width_em(ln.text) * p.font_size_pt
        assert need_pt <= (ln.bbox[2] - ln.bbox[0]) / dpi * 72 + 1
    assert p.line_pitch_pt == pytest.approx(90 / dpi * 72, abs=0.6)

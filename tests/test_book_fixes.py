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


def test_arrow_glyph_and_diagram_table():
    import numpy as np

    from ocr_tool.figures import table_is_figure

    assert clean_ocr_text("GỌI VỐN ¬ CHUYỂN TIỀN ĐÓ") == "GỌI VỐN → CHUYỂN TIỀN ĐÓ"
    blank = np.full((400, 600), 255, np.uint8)
    diagram_cells = ["TIỀN MẶT\n— TÀI", "¬\n*@\nN", "NỢ -~—\nN", "`\nTÀI SẢN\nRÒNG _"]
    assert table_is_figure(blank, (0, 0, 600, 400), None, [], diagram_cells, 30)
    real_cells = ["Doanh thu", "1.250.000", "Chi phí", "980.000", "Lợi nhuận", "270.000"]
    assert not table_is_figure(blank, (0, 0, 600, 400), None, [], real_cells, 30)


def test_noise_paragraphs_dropped():
    """Lốm đốm nền xám bị OCR thành 'chữ' tin cậy thấp -> bỏ; chữ đọc trùng (bóng chữ) -> bỏ."""
    import numpy as np

    from ocr_tool.document import Line, Word
    from ocr_tool.engine import Block

    def para(text, box, conf):
        x0, y0, x1, y1 = box
        return Paragraph(lines=[Line([Word(text, x0, y0, x1 - x0, y1 - y0, conf)])])

    good = para("Việc giải trình dòng tiền vào ra của một công ty", (600, 1500, 1900, 1710), 92)
    dup = para("c giải trình dè của mội", (770, 1520, 1280, 1700), 88)
    noise = [para("ye Ges <i", (430, 830, 476, 960), 18), para("`. “._. `", (540, 1646, 760, 1818), 61)]
    blocks = [Block([good, dup] + noise)]
    gray = np.full((3500, 2475), 255, np.uint8)
    figs = Pipeline(Options())._drop_noise(gray, blocks, 40)
    assert figs == []
    assert [p.text for p in blocks[0].paragraphs] == [good.text]


def test_shaded_panel_frame_detected():
    """Hộp minh hoạ viền xám dày (bị làm phẳng mất trước OCR) phải được tìm thấy trên ảnh gốc."""
    import numpy as np

    from ocr_tool.figures import detect_shaded_panels

    img = np.full((3000, 2200, 3), 255, np.uint8)
    img[400:2400, 300:1900] = 200          # viền xám dày ~50px
    img[450:2350, 350:1850] = 255
    img[1000:1010, 500:1600] = 0           # nét vẽ bên trong
    panels = detect_shaded_panels(img, 40)
    assert len(panels) == 1
    x0, y0, x1, y1 = panels[0]
    assert abs(x0 - 300) < 30 and abs(y0 - 400) < 30 and abs(x1 - 1900) < 30 and abs(y1 - 2400) < 30
    plain = np.full((3000, 2200, 3), 255, np.uint8)
    plain[500:520, 300:1900] = 0           # chỉ có một đường kẻ
    assert detect_shaded_panels(plain, 40) == []


def test_lone_glyphs_and_border_strays():
    from ocr_tool.document import Line, Word
    from ocr_tool.engine import Block

    arrow = Paragraph(lines=[Line([Word("1", 1200, 1980, 60, 90, 94)])])
    page_no = Paragraph(lines=[Line([Word("23", 2000, 150, 60, 40, 90)])])
    blocks = Pipeline._drop_lone_glyphs([Block([arrow, page_no])], 50, 3500)
    assert [p.text for p in blocks[0].paragraphs] == ["23"]
    assert Pipeline._stray_word(Word(":", 280, 2520, 10, 30, 55))
    assert Pipeline._stray_word(Word("TÌ", 280, 1620, 40, 50, 30))
    assert not Pipeline._stray_word(Word("PBR", 330, 1620, 90, 50, 90))


def test_spellfix_rare_glued_and_cross_line():
    lex = get_lexicon()
    f = SpellFixer(lex, DocModel.build(["kết quả rất tốt", "một công ty rất tốt"]))
    assert f.fix_line("Ngược lại, nều cần một ví dụ") == "Ngược lại, nếu cần một ví dụ"
    assert f.fix_line("đang kiếm tiền rất tót") == "đang kiếm tiền rất tốt"
    # âm tiết hiếm, không có ngữ cảnh ủng hộ ứng viên: giữ nguyên (từ hiếm nhưng đúng)
    assert f.fix_line("có ngọn lửa phụt mạnh ra") == "có ngọn lửa phụt mạnh ra"
    assert f.fix_line("lợi nhuận trên bình củakỳ trước") == "lợi nhuận trên bình của kỳ trước"
    assert f.fix_line("Khách hàng CôngtY") == "Khách hàng Công ty"
    assert f.fix_line("sau VHC tầngI), đồng thời") == "sau VHC tầng I), đồng thời"
    assert f.fix_line("rôto và tua bin") == "rôto và tua bin"
    # âm tiết hiếm nhưng ghép đúng với từ bên cạnh thì giữ nguyên
    assert f.fix_line("công ty niêm yết") == "công ty niêm yết"
    assert f.fix_line("chỉ khư khư giữ tiền") == "chỉ khư khư giữ tiền"
    # ngữ cảnh qua chỗ xuống dòng: "dõng | dạc" là đúng, không đổi
    assert f.fix_line("đều có thể dõng", right="dạc") == "đều có thể dõng"
    assert f.fix_line("nếu khoản này lớn thì có vấn", right="đề") == "nếu khoản này lớn thì có vấn"
    assert f.fix_line("đề xảy ra ngay", left="vấn") == "đề xảy ra ngay"


def test_page_numbers_and_running_headers_to_word_header():
    from ocr_tool.document import Line, Page, Word
    from ocr_tool.pipeline import strip_running_headers

    def para(text, y):
        return Paragraph(lines=[Line([Word(w, 300 + 120 * k, y, 100, 40, 90) for k, w in enumerate(text.split())])])

    headers = ["PHẨN lÌ: XÃ HỘI ĐÒI HÒI GÌ Ở CÔNG TY?", "PHẦN II: XÃ HỘI ĐÒI HỎI GÌ Ở CÔNG TY?",
               "PHẨN II: XÃ HỘI ĐÒI HỎI GÌ Ở CÔNG TY?", "PHAN II: XÃ HỘI BO! HO! GÌ Ở CÔNG TY?"]
    pages = []
    for i in range(16):
        els = [para(headers[(i // 2) % 4], 160) if i % 2 == 0 else para(f"| {i + 100}", 160),
               para("Nội dung trang sách", 900)]
        pages.append(Page(width_px=2475, height_px=3500, dpi=300, page_width_in=8.25, page_height_in=11.67,
                          elements=els))
    strip_running_headers(pages)
    for i, pg in enumerate(pages):
        assert [e.text for e in pg.elements] == ["Nội dung trang sách"]
        if i % 2:
            assert pg.header_text == str(i + 100)
        else:
            assert pg.header_text.startswith(f"{i + 100} | ") and "XÃ HỘI" in pg.header_text

"""Kiểm thử hồi quy vòng 2: tên file kết quả trùng nhau, --merge / -o, thư mục không ghi được, từ điển người dùng,
bảng bị biến thành hình hoặc đọc rác do đường kẻ, đoạn văn cột trái bị cắt đôi, Dừng khi OCR song song."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool import spellfix  # noqa: E402
from ocr_tool.cli import main as cli_main  # noqa: E402
from ocr_tool.config import find_tesseract, list_languages  # noqa: E402
from ocr_tool.document import Figure, Line, Paragraph, Table, Word  # noqa: E402
from ocr_tool.engine import Block, TesseractEngine  # noqa: E402
from ocr_tool.export_docx import check_writable_dir, unique_output  # noqa: E402
from ocr_tool.pipeline import Cancelled, Options, Pipeline, _peel_ruling  # noqa: E402
from ocr_tool.preprocess import _fitz  # noqa: E402
from tests.make_sample import FONT_CANDIDATES, _font  # noqa: E402

TESS = find_tesseract()
needs_tesseract = pytest.mark.skipif(
    not TESS or not {"vie", "eng", "rus"} <= set(list_languages(TESS)), reason="cần Tesseract vie+eng+rus")
FONT = next(c for c in FONT_CANDIDATES if Path(c).exists())


def _text_pdf(path: Path, line: str) -> Path:
    fitz = _fitz()
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_font(fontname="dv", fontfile=FONT)
    pg.insert_text((72, 100), line, fontname="dv", fontsize=13)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def _docx_text(path: Path) -> str:
    from docx import Document

    return "\n".join(p.text for p in Document(str(path)).paragraphs)


# ------------------------------------------------------------------ tên file kết quả
def test_unique_output_names():
    used: set = set()
    t = Path("out") / "scan.docx"
    assert unique_output(t, used) == t
    assert unique_output(t, used).name == "scan (2).docx"
    assert unique_output(t, used).name == "scan (3).docx"
    assert unique_output(Path("out") / "khac.docx", used).name == "khac.docx"


@needs_tesseract
def test_same_stem_inputs_do_not_overwrite_each_other(tmp_path):
    """a/scan.pdf + b/scan.pdf cùng -o: trước đây file sau ghi đè file trước, mất kết quả."""
    a = _text_pdf(tmp_path / "a" / "scan.pdf", "Tài liệu A: báo cáo tháng một.")
    b = _text_pdf(tmp_path / "b" / "scan.pdf", "Tài liệu B: kế hoạch tháng hai.")
    out = tmp_path / "out"
    assert cli_main([str(a), str(b), "-o", str(out), "--tesseract", TESS]) == 0
    assert "Tài liệu A" in _docx_text(out / "scan.docx")
    assert "Tài liệu B" in _docx_text(out / "scan (2).docx")


@needs_tesseract
def test_merge_and_output_paths(tmp_path, capsys):
    a = _text_pdf(tmp_path / "in" / "mot.pdf", "Trang một của bộ hồ sơ.")
    b = _text_pdf(tmp_path / "in" / "hai.pdf", "Trang hai của bộ hồ sơ.")
    # --merge không có đuôi .docx -> tự thêm (trước đây ghi ra file không đuôi, Word không mở được)
    assert cli_main([str(a), str(b), "--merge", str(tmp_path / "gop"), "--tesseract", TESS]) == 0
    assert (tmp_path / "gop.docx").is_file() and not (tmp_path / "gop").exists()
    # --merge là thư mục -> file gộp đặt trong thư mục (trước đây: lỗi IsADirectoryError, traceback)
    d = tmp_path / "thu_muc"
    d.mkdir()
    assert cli_main([str(a), str(b), "--merge", str(d), "--tesseract", TESS]) == 0
    assert (d / "mot_merged.docx").is_file()
    # -o x.docx với nhiều đầu vào: báo lỗi rõ ràng (trước đây tạo THƯ MỤC "x.docx" và các file ghi đè nhau)
    assert cli_main([str(a), str(b), "-o", str(tmp_path / "x.docx"), "--tesseract", TESS]) == 2
    assert not (tmp_path / "x.docx").exists()
    assert "--merge" in capsys.readouterr().err


@needs_tesseract
def test_unwritable_output_dir_fails_before_ocr(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "la_file.txt"
    blocker.write_text("x")
    bad_dir = blocker / "ket_qua"  # không tạo được thư mục bên trong một file
    assert check_writable_dir(bad_dir) is not None
    assert check_writable_dir(tmp_path / "moi" / "ok") is None
    called = []
    monkeypatch.setattr(Pipeline, "process_file", lambda self, p: called.append(p) or [])
    pdf = _text_pdf(tmp_path / "in" / "mot.pdf", "Trang một.")
    assert cli_main([str(pdf), "-o", str(bad_dir), "--tesseract", TESS]) == 2
    assert called == []  # không OCR cả lô rồi mới báo lỗi


# ------------------------------------------------------------------ từ điển người dùng
def _to_cp1258(text: str) -> bytes:
    """Mã hoá như Notepad lưu "ANSI" trên Windows tiếng Việt: chữ cái có mũ/móc dựng sẵn + dấu thanh tổ hợp."""
    tones = {"̀", "́", "̃", "̉", "̣"}
    out = []
    for ch in text:
        d = unicodedata.normalize("NFD", ch)
        out.append(unicodedata.normalize("NFC", "".join(c for c in d if c not in tones)) +
                   "".join(c for c in d if c in tones))
    return "".join(out).encode("cp1258")


def test_user_dict_replaces_whole_words_only(tmp_path):
    d = tmp_path / "tu_dien.txt"
    d.write_text("ng => người\na =>\nCKHA => СКНА\n", encoding="utf-8")
    fx = spellfix.SpellFixer(spellfix.Lexicon.load(extra_user_dict=d), spellfix.DocModel())
    out = fx.fix_line("Những người trong nhà không ngủ được, ng ta nói a vậy.")
    assert out == "Những người trong nhà không ngủ được, người ta nói vậy."
    assert "СКНА-22" in fx.fix_line("Thiết bị điểm lửa CKHA-22")


@pytest.mark.parametrize("encode", [lambda s: s.encode("utf-16"), _to_cp1258, lambda s: s.encode("utf-8-sig")])
def test_user_dict_any_windows_encoding(tmp_path, encode):
    """Từ điển lưu bằng Notepad dạng "Unicode" (UTF-16) hoặc "ANSI" từng làm hỏng mọi lần chạy (UnicodeDecodeError)."""
    d = tmp_path / "tu_dien.txt"
    d.write_bytes(encode("ống khuếch tán\r\nđ-ợc => được\r\n\r\n# ghi chú\r\n=> thiếu vế trái\r\n"))
    lex = spellfix.Lexicon.load(extra_user_dict=d)
    assert ("ống", "khuếch") in lex.compounds and ("khuếch", "tán") in lex.compounds
    assert ("đ-ợc", "được") in lex.replacements


def test_lexicon_reloads_after_user_dict_edit(tmp_path, monkeypatch):
    d = tmp_path / "tu_dien.txt"
    monkeypatch.setattr(spellfix, "user_dict_path", lambda: d)
    monkeypatch.setattr(spellfix, "_LEXICON", None)
    assert ("abc", "xyz") not in spellfix.get_lexicon().replacements
    d.write_text("abc => xyz\n", encoding="utf-8")
    os.utime(d, (time.time() + 5, time.time() + 5))
    assert ("abc", "xyz") in spellfix.get_lexicon().replacements
    monkeypatch.setattr(spellfix, "_LEXICON", None)


# ------------------------------------------------------------------ bảng
def test_peel_ruling_removes_border_remnants_only():
    crop = np.full((60, 400), 255, np.uint8)
    crop[0:2, :] = 0          # sót đường kẻ trên
    crop[:, 0:2] = 0          # sót đường kẻ trái
    crop[20:45, 30:34] = 0    # nét chữ dọc ("1") gần mép trái
    crop[20:24, 30:90] = 0    # nét chữ ngang
    out = _peel_ruling(crop.copy())
    assert (out[0:2, :] == 255).all() and (out[:, 0:2] == 255).all()
    assert (out[20:45, 30:34] == 0).all() and (out[20:24, 30:90] == 0).all()


def _table_image(path: Path, rows, col_x, top=300, row_h=110, lw=4, size=(2480, 1400), title=True) -> Path:
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    font = _font(FONT_CANDIDATES, 34)
    if title:
        d.text((col_x[0], top - 120), "Bảng kiểm tra số liệu vật tư", fill="black", font=font)
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if cell is None:
                continue
            text, rs, cs = cell if isinstance(cell, tuple) else (cell, 1, 1)
            x0, y0 = col_x[c], top + row_h * r
            x1, y1 = col_x[c + cs], top + row_h * (r + rs)
            d.rectangle((x0, y0, x1, y1), outline="black", width=lw)
            d.text((x0 + 16, y0 + row_h // 2 - 20), text, fill="black", font=font)
    im.save(path, dpi=(300, 300))
    return path


@needs_tesseract
def test_table_on_small_image_is_not_turned_into_picture(tmp_path):
    """Ảnh nhỏ (cắt riêng một bảng) được phóng to trước OCR: đường kẻ bảng thành "viền dày" của hộp minh hoạ
    và cả bảng bị xuất thành ảnh, không sửa được chữ trong Word."""
    rows = [["A", "B", "C", "D"],
            ["E", ("F gộp bốn ô", 2, 2), None, "G"],
            ["H", None, None, "I"],
            [("J dọc", 2, 1), "K", "L", "M"],
            [None, "N", ("O ngang", 1, 2), None]]
    img = _table_image(tmp_path / "bang.png", rows, [200, 700, 1200, 1700, 2200])
    page = Pipeline(Options(tesseract_cmd=TESS)).process_file(str(img))[0]
    assert not any(isinstance(e, Figure) for e in page.elements)
    tables = [e for e in page.elements if isinstance(e, Table)]
    assert len(tables) == 1 and (tables[0].n_rows, tables[0].n_cols) == (5, 4)
    spans = {(c.row, c.col): (c.row_span, c.col_span) for c in tables[0].cells}
    assert spans[(1, 1)] == (2, 2) and spans[(3, 0)] == (2, 1) and spans[(4, 2)] == (1, 2)


@needs_tesseract
def test_numeric_cells_not_replaced_by_ruling_junk(tmp_path, caplog):
    """Ô chỉ có số: OCR cả trang đọc dính đường kẻ ra "PE—— :", "СОНИ" và rác này từng thay chữ đọc lại đúng;
    mô hình tiếng Việt đọc "8" thành "ö"."""
    n = 12
    rows = [[str(r + 1), f"Bu lông M{10 + r}", str(5 * r + 3), "Đạt"] for r in range(n)]
    img = _table_image(tmp_path / "so.png", rows, [150, 450, 1300, 1800, 2330], top=250, row_h=64,
                       size=(2480, 250 + 64 * n + 200), title=False)
    import logging

    caplog.set_level(logging.DEBUG, logger="ocr_tool")
    page = Pipeline(Options(tesseract_cmd=TESS)).process_file(str(img))[0]
    tables = [e for e in page.elements if isinstance(e, Table)]
    # kết quả OCR khác nhau theo bản dữ liệu ngôn ngữ (máy CI dùng tessdata_best): in đủ để chẩn đoán
    assert tables, "không nhận ra bảng: %s\n%s" % (
        [(type(e).__name__, getattr(e, "bbox", None)) for e in page.elements],
        "\n".join(r.getMessage() for r in caplog.records)[-4000:])
    t = tables[0]
    got = {(c.row, c.col): " ".join(c.lines).strip() for c in t.cells}
    numeric = [(got.get((r, c), "").strip(".,:;'|"), rows[r][c]) for r in range(n) for c in (0, 2)]
    wrong = [(g, e) for g, e in numeric if g != e]
    # độ chính xác đọc số còn tuỳ bản dữ liệu ngôn ngữ (tessdata_best đọc khác bản rút gọn): cho phép vài ô sai,
    # nhưng không được có rác đường kẻ ("Bo", "TM", "СОНИ", "ö") ở phần lớn các ô như trước khi sửa
    assert sum(g.isdigit() for g, _ in numeric) >= 0.9 * len(numeric), wrong
    assert len(wrong) <= 0.2 * len(numeric), wrong
    import re

    texts = [got.get((r, 1), "") for r in range(n)]
    # cột chữ không lẫn rác đường kẻ (dấu thanh có thể sai do OCR, không kiểm tra ở đây)
    assert not any(re.search(r"[|\[\]—_]|[\u0400-\u04ff]", t) for t in texts), texts
    assert sum(bool(re.fullmatch(r"Bu l\w{1,3}ng M\d\d", t)) for t in texts) >= n - 2, texts


@needs_tesseract
def test_table_kept_when_page_ocr_returns_tall_confident_junk(tmp_path, monkeypatch):
    """Bộ cài Windows dùng tessdata_best: OCR cả trang trên ảnh chỉ có bảng trả về vài "dòng" cao hàng trăm px mà
    độ tin cậy vẫn cao -> chiều cao chữ ước lượng sai, ngưỡng dò bảng sai và mất cả bảng. Chiều cao chữ phải được
    đối chiếu với cỡ vết mực khi trang có ít dòng tin cậy."""
    n = 12
    rows = [[str(r + 1), f"Bu lông M{10 + r}", str(5 * r + 3), "Đạt"] for r in range(n)]
    img = _table_image(tmp_path / "so.png", rows, [150, 450, 1300, 1800, 2330], top=250, row_h=64,
                       size=(2480, 250 + 64 * n + 200), title=False)

    def tall_lines(self, gray, psm=3, refine=True):
        def line(y):
            return Line([Word("Bu", 300, y, 120, 320, 90), Word("lông", 450, y, 200, 320, 90),
                         Word("M10", 680, y, 160, 320, 90)])
        return [Block([Paragraph(lines=[line(y)])], lang="vie") for y in (100, 600, 1100)]

    monkeypatch.setattr(TesseractEngine, "ocr_page", tall_lines)
    monkeypatch.setattr(TesseractEngine, "refine_page", lambda self, gray, blocks: blocks)
    page = Pipeline(Options(tesseract_cmd=TESS, spellfix=False)).process_file(str(img))[0]
    tables = [e for e in page.elements if isinstance(e, Table)]
    assert tables and (tables[0].n_rows, tables[0].n_cols) == (n, 4), \
        [(type(e).__name__, getattr(e, "bbox", None)) for e in page.elements]


# ------------------------------------------------------------------ bố cục hai cột
def _line(text, x, y, h=38, gap=12, ch=20):
    words, cx = [], x
    for w in text.split():
        words.append(Word(w, cx, y, ch * len(w), h, 95))
        cx += ch * len(w) + gap
    return Line(words)


def test_split_paragraph_in_left_column_is_rejoined():
    """Tesseract tách dòng cuối của đoạn ở cột trái thành khối riêng; trước đây không nối lại vì so với mép phải
    của cả trang (cột phải) -> đoạn văn bị cắt đôi giữa câu."""
    left = Block([Paragraph([_line("Cột trái đoạn một hệ thống làm mát gồm", 200, 450),
                             _line("bơm nước quạt gió và các đường ống dẫn", 200, 510),
                             _line("mát được bơm tuần hoàn qua thân máy để", 200, 570)])])
    tail = Block([Paragraph([_line("nhiệt rồi đưa ra két.", 200, 630)])])
    right = Block([Paragraph([_line("Cột phải đoạn hai hệ thống bôi trơn cung", 1300, 450),
                              _line("cấp dầu tới các bề mặt ma sát giảm mài", 1300, 510),
                              _line("mòn và mang nhiệt ra khỏi chi tiết.", 1300, 570)])])
    out = TesseractEngine.merge_split_blocks([left, tail, right])
    assert len(out) == 2
    assert out[0].paragraphs[0].text.endswith("nhiệt rồi đưa ra két.")


# ------------------------------------------------------------------ Dừng khi OCR song song
@needs_tesseract
def test_parallel_cancel_is_prompt_and_leaves_no_temp_files(tmp_path, monkeypatch):
    """Bấm Dừng khi OCR song song: trước đây phải chờ tiến trình con xong cả trang (có khi hàng chục giây) và
    ảnh tạm tess_*_input.PNG của tiến trình con bị diệt nằm lại trong %TEMP%."""
    page = Image.new("RGB", (2480, 3508), "white")
    d = ImageDraw.Draw(page)
    font = _font(FONT_CANDIDATES, 40)
    for k in range(40):
        d.text((150, 150 + 80 * k), f"Dòng {k + 1}: nội dung kiểm tra dừng giữa chừng khi đang nhận dạng.",
               fill="black", font=font)
    pdf = tmp_path / "quet.pdf"
    page.save(pdf, save_all=True, append_images=[page] * 3, resolution=300)
    tmp = tmp_path / "temp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    t0 = time.time()
    with pytest.raises(Cancelled):
        Pipeline(Options(tesseract_cmd=TESS, workers=3, cancel=lambda: time.time() - t0 > 3)).process_file(str(pdf))
    assert time.time() - t0 < 10  # trước đây: chờ tiến trình con OCR xong cả trang
    time.sleep(1)
    left = [p for p in tmp.rglob("*") if p.is_file()]
    assert left == [], left

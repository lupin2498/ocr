"""Kiểm thử hồi quy cho các lỗi tìm ra khi kiểm thử toàn diện (đầu vào hỏng, file Word sai chuẩn,
file đang mở trong Word, dừng giữa chừng, trang bị xoay, OCR song song gián đoạn, cấu hình Tesseract)."""
from __future__ import annotations

import collections
import math
import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
from lxml import etree
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool import export_docx as exp  # noqa: E402
from ocr_tool import preprocess as pre  # noqa: E402
from ocr_tool.cli import describe_error  # noqa: E402
from ocr_tool.cli import main as cli_main  # noqa: E402
from ocr_tool.config import configure_tesseract, find_tesseract, list_languages, resolve_tessdata  # noqa: E402
from ocr_tool.document import Line, Page, Paragraph, Table, TableCell, Word  # noqa: E402
from ocr_tool.pipeline import Cancelled, Options, Pipeline, strip_running_headers  # noqa: E402
from tests.make_sample import FONT_CANDIDATES, _font  # noqa: E402

TESS = find_tesseract()
needs_tesseract = pytest.mark.skipif(
    not TESS or not {"vie", "eng", "rus"} <= set(list_languages(TESS)), reason="cần Tesseract vie+eng+rus")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
# thứ tự phần tử con bắt buộc theo lược đồ OOXML (Word báo "file hỏng" nếu sai, LibreOffice thì bỏ qua)
_SEQ = {
    "tblPr": ("tblStyle tblpPr tblOverlap bidiVisual tblStyleRowBandSize tblStyleColBandSize tblW jc "
              "tblCellSpacing tblInd tblBorders shd tblLayout tblCellMar tblLook tblCaption tblDescription"),
    "pPr": ("pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr shd "
            "tabs suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi "
            "adjustRightInd snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc "
            "textDirection textAlignment textboxTightWrap outlineLvl divId cnfStyle rPr sectPr"),
    "rPr": ("rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof "
            "snapToGrid vanish webHidden color spacing w kern position sz szCs highlight u effect bdr shd "
            "fitText vertAlign rtl cs em lang eastAsianLayout specVanish oMath"),
    "tcPr": "cnfStyle tcW gridSpan hMerge vMerge tcBorders shd noWrap tcMar textDirection tcFitText vAlign hideMark",
    "sectPr": ("HF footnotePr endnotePr type pgSz pgMar paperSrc pgBorders lnNumType pgNumType cols formProt "
               "vAlign noEndnote titlePg textDirection bidi rtlGutter docGrid printerSettings"),
}
_SEQ = {k: v.split() for k, v in _SEQ.items()}


def ooxml_problems(path: Path) -> list:
    problems = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not (name.startswith("word/") and name.endswith(".xml")):
                continue
            root = etree.fromstring(z.read(name))  # XML hỏng -> ngoại lệ, test thất bại
            for el in root.iter(f"{{{W_NS}}}*"):
                seq = _SEQ.get(etree.QName(el).localname)
                if not seq:
                    continue
                last, seen = -1, collections.Counter()
                for ch in el:
                    if not isinstance(ch.tag, str) or etree.QName(ch).namespace != W_NS:
                        continue
                    ln = etree.QName(ch).localname
                    key = "HF" if ln in ("headerReference", "footerReference") else ln
                    if key not in seq:
                        continue
                    seen[key] += 1
                    if seen[key] > 1 and key != "HF":
                        problems.append(f"{name}: <{etree.QName(el).localname}> lặp <{ln}>")
                    if seq.index(key) < last:
                        problems.append(f"{name}: <{etree.QName(el).localname}> sai thứ tự <{ln}>")
                    last = max(last, seq.index(key))
    return problems


def _para(text: str, y: int, x: int = 300, **kw) -> Paragraph:
    return Paragraph(lines=[Line([Word(w, x + 120 * k, y, 100, 40, 90) for k, w in enumerate(text.split())])], **kw)


def _page(elements, w_in=8.27, h_in=11.69, **kw) -> Page:
    return Page(width_px=int(w_in * 300), height_px=int(h_in * 300), dpi=300, page_width_in=w_in,
                page_height_in=h_in, elements=elements, **kw)


def _table() -> Table:
    cells = [TableCell(0, 0, lines=["STT"], bold=True), TableCell(0, 1, col_span=2, lines=["Gộp\x01 hai cột"]),
             TableCell(1, 0, lines=["1"]), TableCell(1, 1, lines=["Bu lông\x0b M10"]),
             TableCell(1, 2, lines=["120"]),
             TableCell(5, 7, lines=["ô ngoài lưới – phải bị bỏ qua, không làm hỏng file"])]
    return Table(bbox=(300, 1200, 2100, 1500), row_edges=[1200, 1350, 1500], col_edges=[300, 900, 1500, 2100],
                 cells=cells)


# ------------------------------------------------------------------ xuất Word
def test_control_chars_and_schema_order(tmp_path):
    """PDF/ảnh có ký tự điều khiển (\\x01, \\x0b, \\ufffe ...) từng làm python-docx ném lỗi; bảng có tblPr sai
    thứ tự (Word báo file hỏng)."""
    from docx import Document

    bad = "Chữ\x01 lỗi\x0b điều\x1f khiển\ufffe hết"
    pages = [_page([_para(bad, 300, bold=True, italic=True, char_spacing_pt=-0.4), _table(),
                    _para("Đoạn sau bảng", 1700, alignment="justify")],
                   header_text="12 | PHẦN I:\x02 MỞ ĐẦU", footer_text="13\x07")]
    out = Path(exp.export_docx(pages, str(tmp_path / "ctl.docx")))
    assert out.name == "ctl.docx"
    doc = Document(str(out))
    body = "\n".join(p.text for p in doc.paragraphs)
    assert "Chữ lỗi điều khiển hết" in body
    assert not any(ord(c) < 32 and c not in "\t\n\r" for c in body)
    cells = {c.text for row in doc.tables[0].rows for c in row.cells}
    assert {"STT", "Gộp hai cột", "Bu lông M10", "120"} <= cells
    assert "PHẦN I: MỞ ĐẦU" in doc.sections[0].header.paragraphs[0].text
    assert ooxml_problems(out) == []


def test_control_chars_removed_from_pdf_text_and_txt(tmp_path):
    """Lớp chữ PDF chứa ký tự điều khiển (glyph không có mã Unicode -> \\x00): từng lọt vào file .txt."""
    fitz = pre._fitz()
    font = next(c for c in FONT_CANDIDATES if Path(c).exists())
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_font(fontname="dejavu", fontfile=font)
    pg.insert_text((70, 90), "Ký tự điều khiển:\x01\x02\x03 hết.", fontname="dejavu", fontsize=13)
    pg.insert_text((70, 120), "Dòng hai \x00 kết thúc.", fontname="dejavu", fontsize=13)
    pdf = tmp_path / "ctl.pdf"
    doc.save(str(pdf))
    pages = Pipeline(Options(detect_figures=False)).process_file(str(pdf))
    text = pages[0].text
    assert "hết." in text and "kết thúc." in text
    assert not any(ord(c) < 32 and c not in "\t\n\r" for c in text), repr(text)
    raw_page = _page([_para("A\x00B\x07C\x1bD", 300)])
    out = exp.write_text([raw_page], str(tmp_path / "t.txt"))
    assert Path(out).read_text(encoding="utf-8-sig") == "ABCD"


@pytest.mark.parametrize("w_in,h_in,size", [(60.0, 80.0, 900.0), (0.0, 0.0, 0.0), (8.27, 11.69, float("nan"))])
def test_extreme_page_and_font_sizes(tmp_path, w_in, h_in, size):
    """Trang/cỡ chữ bất thường (ảnh panorama, ảnh 0 dpi, NaN) vẫn xuất được file Word mở được."""
    from docx import Document

    pages = [_page([_para("Nội dung", 300, font_size_pt=size)], w_in=w_in, h_in=h_in,
                   margin_left_in=30, margin_right_in=30)]
    out = exp.export_docx(pages, str(tmp_path / "x.docx"))
    sec = Document(out).sections[0]
    assert 0.9 <= sec.page_width.inches <= 22.01 and 0.9 <= sec.page_height.inches <= 22.01
    content = sec.page_width.inches - sec.left_margin.inches - sec.right_margin.inches
    assert content >= 0.49
    assert ooxml_problems(Path(out)) == []


def test_locked_output_saved_under_new_name(tmp_path, monkeypatch):
    """File .docx/.txt đích đang mở trong Word (Windows khoá file): lưu thành "tên (1).docx" thay vì báo lỗi."""
    target = tmp_path / "out.docx"

    class LockedDoc:
        def save(self, p):
            if Path(p) == target:
                raise PermissionError(13, "Permission denied", p)
            Path(p).write_bytes(b"docx")

    monkeypatch.setattr(exp.DocxExporter, "build", lambda self, pages: LockedDoc())
    written = exp.export_docx([], str(target))
    assert Path(written).name == "out (1).docx" and Path(written).exists()

    real_write = Path.write_text

    def locked_write(self, *a, **kw):
        if self.name == "out.txt":
            raise PermissionError(13, "Permission denied", str(self))
        return real_write(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", locked_write)
    txt = exp.write_text([_page([_para("Tiếng Việt", 300)])], str(tmp_path / "out.txt"))
    assert Path(txt).name == "out (1).txt"
    raw = Path(txt).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and "Tiếng Việt" in raw.decode("utf-8-sig")


# ------------------------------------------------------------------ đầu vào hỏng
def _real_pdf(path: Path, pages: int = 1, password: str = "") -> Path:
    fitz = pre._fitz()
    doc = fitz.open()
    font = next(c for c in FONT_CANDIDATES if Path(c).exists())
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)
        pg.insert_font(fontname="dejavu", fontfile=font)
        for k in range(6):
            pg.insert_text((70, 90 + 22 * k), f"Trang {i + 1}: dòng chữ tiếng Việt số {k + 1} để kiểm tra.",
                           fontname="dejavu", fontsize=13)
    kw = {}
    if password:
        kw = dict(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "o")
    doc.save(str(path), **kw)
    doc.close()
    return path


def test_bad_inputs_give_clear_errors(tmp_path):
    cases = {
        "missing.png": None,
        "empty.png": b"",
        "notimage.png": b"day khong phai anh" * 10,
        "broken.pdf": b"%PDF-1.4\n%garbage without objects\n",
    }
    for name, data in cases.items():
        p = tmp_path / name
        if data is not None:
            p.write_bytes(data)
        with pytest.raises(pre.InputError) as ei:
            pre.load_inputs(str(p))
        assert p.name in str(ei.value) or "Không tìm thấy" in str(ei.value)
    enc = _real_pdf(tmp_path / "secret.pdf", password="abc")
    with pytest.raises(pre.InputError, match="mật khẩu"):
        pre.count_pages(str(enc))
    with pytest.raises(pre.InputError, match="mật khẩu"):
        pre.load_inputs(str(enc))


def test_pdf_detected_by_content_and_truncated_image(tmp_path):
    """PDF bị đặt đuôi .jpg vẫn đọc được; ảnh JPEG tải dở (cắt cụt) vẫn lấy được phần còn lại."""
    fake_jpg = tmp_path / "scan.jpg"
    fake_jpg.write_bytes(_real_pdf(tmp_path / "a.pdf", pages=2).read_bytes())
    assert pre.count_pages(str(fake_jpg)) == 2
    assert len(pre.load_inputs(str(fake_jpg), pdf_dpi=72)) == 2

    im = Image.new("RGB", (800, 600), "white")
    ImageDraw.Draw(im).rectangle((100, 100, 700, 500), fill="black")
    full = tmp_path / "full.jpg"
    im.save(full, quality=90)
    cut = tmp_path / "cut.jpg"
    cut.write_bytes(full.read_bytes()[: full.stat().st_size // 2])
    li = pre.load_inputs(str(cut))
    assert len(li) == 1 and li[0].image.shape[:2] == (600, 800)


def test_image_modes_and_huge_images(monkeypatch):
    # 16 bit (máy scan "48-bit"): co giãn theo dải thật, không ra ảnh đen/trắng toàn bộ
    a16 = np.full((60, 80), 1200, np.uint16)
    a16[20:40, 20:60] = 3000
    bgr, _ = pre._pil_to_bgr(Image.fromarray(a16))
    assert bgr.dtype == np.uint8 and bgr.shape == (60, 80, 3) and bgr.min() < 30 and bgr.max() > 220
    # trong suốt: nền trắng, chữ giữ nguyên
    la = Image.new("LA", (40, 40), (0, 0))
    la.paste((0, 255), (10, 10, 30, 30))
    bgr, _ = pre._pil_to_bgr(la)
    assert bgr[0, 0].tolist() == [255, 255, 255] and bgr[20, 20].tolist() == [0, 0, 0]
    for mode in ("1", "CMYK", "P", "I", "F"):
        bgr, _ = pre._pil_to_bgr(Image.new(mode, (30, 20)))
        assert bgr.shape == (20, 30, 3) and bgr.dtype == np.uint8
    # ảnh quá lớn: thu nhỏ trước khi OCR, dpi giảm tương ứng
    monkeypatch.setattr(pre, "MAX_WORK_PIXELS", 10_000)
    bgr, dpi = pre._pil_to_bgr(Image.new("RGB", (400, 300), "white"), 600.0)
    f = math.ceil(math.sqrt(400 * 300 / 10_000))
    assert bgr.shape[:2] == (300 // f, 400 // f) and dpi == pytest.approx(600.0 / f)


def test_describe_error_messages():
    assert "đang mở trong Word" in describe_error(PermissionError(13, "denied", "a.docx"))
    assert "Ổ đĩa đã đầy" in describe_error(OSError(28, "No space left on device"))
    assert describe_error(pre.InputError("PDF có mật khẩu")) == "PDF có mật khẩu"
    assert "RAM" in describe_error(MemoryError())


# ------------------------------------------------------------------ cấu hình Tesseract
def test_tessdata_resolution_and_no_quoted_args(tmp_path, monkeypatch):
    """--tessdata-dir "..." trong config bị pytesseract (Windows) giữ nguyên dấu ngoặc -> hỏng khi đường dẫn có
    dấu cách; nay dùng TESSDATA_PREFIX. Dữ liệu cạnh tesseract.exe đóng gói được ưu tiên hơn TESSDATA_PREFIX cũ."""
    from ocr_tool.engine import TesseractEngine

    bundled = tmp_path / "Program Files" / "OCR2Word" / "tesseract"
    (bundled / "tessdata").mkdir(parents=True)
    exe = bundled / "tesseract.exe"
    exe.write_bytes(b"")
    for lang in ("vie", "eng", "rus"):
        (bundled / "tessdata" / f"{lang}.traineddata").write_bytes(b"x")
    stale = tmp_path / "old tesseract" / "tessdata"
    stale.mkdir(parents=True)
    (stale / "eng.traineddata").write_bytes(b"x")
    monkeypatch.setenv("TESSDATA_PREFIX", str(stale))

    assert resolve_tessdata(str(exe), None, ["vie", "eng", "rus"]) == bundled / "tessdata"
    explicit = tmp_path / "my data"
    explicit.mkdir()
    (explicit / "vie.traineddata").write_bytes(b"x")
    assert resolve_tessdata(str(exe), str(explicit), ["vie"]) == explicit
    # thư mục chỉ định thiếu ngôn ngữ -> vẫn tìm thư mục khác có đủ
    assert resolve_tessdata(str(exe), str(explicit), ["vie", "rus"]) == bundled / "tessdata"

    assert configure_tesseract(str(exe), None, ["vie", "eng", "rus"]) == str(bundled / "tessdata")
    assert os.environ["TESSDATA_PREFIX"] == str(bundled / "tessdata")
    eng = TesseractEngine(langs=["vi", "en"], tesseract_cmd=str(exe), tessdata_dir=str(explicit))
    assert "tessdata" not in eng._config(3) and '"' not in eng._config(3)
    assert os.environ["TESSDATA_PREFIX"] == str(bundled / "tessdata")  # explicit thiếu eng -> bản đầy đủ


def test_cli_rejects_bad_paths(tmp_path, capsys):
    assert cli_main(["x.png", "--tessdata", str(tmp_path / "khong-co")]) == 2
    assert cli_main(["x.png", "--tesseract", str(tmp_path / "tesseract.exe")]) == 2
    err = capsys.readouterr().err
    assert "tessdata" in err and "tesseract" in err


# ------------------------------------------------------------------ số trang / tiêu đề chạy
def test_page_number_cleanup_keeps_part_label():
    """Gỡ số trang "22 | PHẦN I: ..." từng xoá luôn "I:" (coi là rác) -> tiêu đề thành "PHẦN DÒNG TIỀN"."""
    pages = []
    for i in range(12):
        head = f"{i + 21} | PHẦN I: DÒNG TIỀN VÀ LỢI NHUẬN" if i % 2 else f"BẢN ĐỒ KẾ TOÁN {i + 21} ]"
        pages.append(_page([_para(head, 160), _para("Nội dung chính của trang sách này", 900)]))
    strip_running_headers(pages)
    for i, pg in enumerate(pages):
        assert [e.text for e in pg.elements] == ["Nội dung chính của trang sách này"]
        assert pg.header_text.startswith(str(i + 21))
        if i % 2:
            assert "PHẦN I: DÒNG TIỀN" in pg.header_text, pg.header_text
        else:
            assert "BẢN ĐỒ KẾ TOÁN" in pg.header_text and "]" not in pg.header_text


# ------------------------------------------------------------------ OCR song song gián đoạn
class _BrokenPool:
    """Giả lập tiến trình con chết (thiếu RAM, bị phần mềm diệt virus chặn...)."""

    def __init__(self, *a, **kw):
        self._processes = {}

    def submit(self, fn, *args):
        from concurrent.futures import Future
        from concurrent.futures.process import BrokenProcessPool

        fut = Future()
        fut.set_exception(BrokenProcessPool("tiến trình con chết đột ngột"))
        return fut

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def test_parallel_pool_failure_falls_back_to_sequential(tmp_path, monkeypatch):
    import concurrent.futures

    pdf = _real_pdf(tmp_path / "book.pdf", pages=4)
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", _BrokenPool)
    done = []
    opts = Options(workers=3, use_pdf_text=True, detect_figures=False, spellfix=False,
                   page_progress=lambda d, t: done.append((d, t)))
    pages = Pipeline(opts).process_file(str(pdf))
    assert len(pages) == 4
    for i, pg in enumerate(pages):
        assert f"Trang {i + 1}: dòng chữ tiếng Việt số 1" in pg.text
    assert done[-1] == (4, 4)


def test_cancel_before_and_between_pages(tmp_path):
    pdf = _real_pdf(tmp_path / "c.pdf", pages=2)
    with pytest.raises(Cancelled):
        Pipeline(Options(cancel=lambda: True)).process_file(str(pdf))
    seen = []

    def cancel():
        return len(seen) >= 1  # dừng ngay sau khi xong trang 1

    opts = Options(workers=1, detect_figures=False, cancel=cancel, page_progress=lambda d, t: seen.append(d))
    with pytest.raises(Cancelled):
        Pipeline(opts).process_file(str(pdf))
    assert seen == [1]


# ------------------------------------------------------------------ cần Tesseract
def _text_image(path: Path, lines, size=(1750, 1240)) -> Path:
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    font = _font(FONT_CANDIDATES, 40)
    for k, ln in enumerate(lines):
        d.text((90, 90 + 70 * k), ln, fill="black", font=font)
    im.save(path, dpi=(300, 300))
    return path


LINES = ["Báo cáo kiểm tra chất lượng sản phẩm năm nay.",
         "Các thiết bị đều hoạt động ổn định và an toàn.",
         "Đề nghị tiếp tục theo dõi trong quý tiếp theo.",
         "Người lập biểu đã ký tên và đóng dấu xác nhận."]


@needs_tesseract
@pytest.mark.parametrize("turn", [Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_180])
def test_rotated_scan_is_turned_upright(tmp_path, turn):
    img = _text_image(tmp_path / "upright.png", LINES)
    rot = tmp_path / "rotated.png"
    Image.open(img).transpose(turn).save(rot, dpi=(300, 300))
    page = Pipeline(Options(tesseract_cmd=TESS, detect_figures=False)).process_file(str(rot))[0]
    text = " ".join(page.text.split())
    assert "kiểm tra chất lượng" in text and "đóng dấu xác nhận" in text, text
    assert page.width_px > page.height_px  # ảnh gốc khổ ngang được xoay về đúng khổ ngang


@needs_tesseract
def test_cli_batch_continues_after_bad_file(tmp_path, capsys):
    good = _text_image(tmp_path / "tot.png", LINES[:2])
    bad = tmp_path / "hong.png"
    bad.write_bytes(b"not an image at all")
    out = tmp_path / "kết quả"
    rc = cli_main([str(bad), str(good), "-o", str(out), "--tesseract", TESS, "--txt"])
    assert rc == 1
    assert (out / "tot.docx").exists() and (out / "tot.txt").exists()
    assert not (out / "hong.docx").exists()
    cap = capsys.readouterr()
    assert "hong.png" in cap.err and "Có 1/2 file lỗi" in cap.err


@needs_tesseract
def test_cancel_inside_page(tmp_path):
    img = _text_image(tmp_path / "p.png", LINES)
    calls = []

    def cancel():
        calls.append(1)
        return len(calls) > 3  # dừng giữa các bước OCR của trang

    with pytest.raises(Cancelled):
        Pipeline(Options(tesseract_cmd=TESS, cancel=cancel)).process_file(str(img))


def test_windowed_exe_with_dropped_files_opens_gui(monkeypatch):
    """Kéo-thả ảnh vào OCR2Word.exe (không có console): mở giao diện với file đó, không chạy ngầm dòng lệnh."""
    pytest.importorskip("tkinter")
    import ocr_tool.__main__ as entry
    import ocr_tool.gui as gui

    got = []
    monkeypatch.setattr(gui, "run_gui", lambda files=None: got.append(files))
    monkeypatch.setattr(sys, "argv", ["OCR2Word.exe", "a.png"])
    monkeypatch.setattr(sys, "stdout", None)
    assert entry.main() == 0 and got == [["a.png"]]


@needs_tesseract
@pytest.mark.skipif(os.name != "nt", reason="lỗi bảng mã ANSI của Tesseract chỉ có trên Windows")
def test_non_ascii_tessdata_without_short_names(tmp_path, monkeypatch):
    """Cài cho riêng người dùng có tên tiếng Việt (C:\\Users\\Nguyễn Văn A\\...) trên ổ đĩa tắt tên ngắn 8.3:
    Tesseract không mở được tessdata có dấu -> dùng bản chép sang thư mục không dấu."""
    import shutil

    import ocr_tool.config as cfg

    src = resolve_tessdata(TESS, None, ["vie", "eng", "rus"])
    td = tmp_path / "Nguyễn Văn Ả" / "tessdata"
    (td / "configs").mkdir(parents=True)
    for lang in ("vie", "eng", "rus"):
        shutil.copy2(src / f"{lang}.traineddata", td)
    if (src / "configs").is_dir():
        for f in (src / "configs").iterdir():
            if f.is_file():
                shutil.copy2(f, td / "configs")
    monkeypatch.setattr(cfg, "ascii_path", lambda p: str(p))  # giả lập không có tên ngắn 8.3
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("TESSDATA_PREFIX", str(td))
    assert {"vie", "eng", "rus"} <= set(cfg.list_languages(TESS, str(td)))
    img = _text_image(tmp_path / "a.png", LINES[:2])
    page = Pipeline(Options(tesseract_cmd=TESS, tessdata_dir=str(td), detect_figures=False)).process_file(str(img))[0]
    assert os.environ["TESSDATA_PREFIX"] == str(tmp_path / "pd" / "OCR2Word" / "tessdata")
    assert "kiểm tra chất lượng" in " ".join(page.text.split())

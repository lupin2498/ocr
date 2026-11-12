"""Giao diện dòng lệnh: python -m ocr_tool <ảnh/pdf...> [-o thư_mục] [--lang vi en ru] ..."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List

from . import __version__
from .config import find_tesseract, list_languages, resolve_tessdata
from .export_docx import export_docx, write_text
from .lang import LANG_MAP
from .pipeline import Cancelled, Options, Pipeline
from .preprocess import IMAGE_EXTS, PDF_EXTS, InputError

log = logging.getLogger("ocr_tool.cli")


def describe_error(e: BaseException) -> str:
    """Thông báo lỗi dễ hiểu cho người dùng (dùng chung cho dòng lệnh và giao diện)."""
    if isinstance(e, InputError):
        return str(e)
    if isinstance(e, PermissionError):
        return (f"Không ghi/đọc được file (đang mở trong Word hoặc thư mục không cho phép ghi): "
                f"{getattr(e, 'filename', '') or e}")
    if isinstance(e, MemoryError):
        return "Không đủ bộ nhớ (RAM) để xử lý file này – thử đóng bớt chương trình hoặc giảm độ phân giải (--pdf-dpi 200)."
    if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
        return "Ổ đĩa đã đầy, không ghi được kết quả."
    try:
        import pytesseract

        if isinstance(e, pytesseract.TesseractNotFoundError):
            return "Không chạy được Tesseract (chưa cài hoặc đường dẫn sai)."
    except ImportError:  # pragma: no cover
        pass
    return f"{type(e).__name__}: {e}"


def selftest_gui() -> int:
    """Kiểm tra nhanh bản đóng gói: dựng được cửa sổ giao diện (Tcl/Tk đi kèm đủ) rồi đóng ngay."""
    try:
        from .gui import App

        app = App()
        app.update()
        app.destroy()
    except Exception as e:  # noqa: BLE001
        print(f"Giao diện lỗi: {describe_error(e)}", file=sys.stderr)
        return 1
    print("GUI OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ocr_tool",
        description="OCR2Word - chuyển ảnh/PDF có chữ (Việt, Anh, Nga) thành file Word giữ bố cục và bảng.")
    ap.add_argument("inputs", nargs="*", help="ảnh (.png .jpg .tif .bmp .webp) hoặc .pdf hoặc thư mục")
    ap.add_argument("-o", "--output", help="thư mục hoặc file .docx đầu ra (mặc định: cạnh file gốc)")
    ap.add_argument("--merge", metavar="FILE.docx", help="gộp tất cả đầu vào thành một file Word")
    ap.add_argument("--lang", nargs="+", default=["vi", "en", "ru"], choices=sorted(LANG_MAP),
                    help="ngôn ngữ cần nhận dạng (mặc định: vi en ru)")
    ap.add_argument("--tesseract", help="đường dẫn tesseract.exe (tự tìm nếu bỏ trống)")
    ap.add_argument("--tessdata", help="thư mục tessdata (nếu Tesseract không tự tìm được)")
    ap.add_argument("--font", default="Times New Roman", help="font chữ trong file Word")
    ap.add_argument("--keep-line-breaks", action="store_true", help="giữ ngắt dòng như trong ảnh")
    ap.add_argument("--no-tables", action="store_true", help="không phát hiện bảng")
    ap.add_argument("--no-deskew", action="store_true", help="không khử nghiêng")
    ap.add_argument("--denoise", action="store_true", help="khử nhiễu (ảnh chụp/scan xấu)")
    ap.add_argument("--binarize", action="store_true", help="nhị phân hoá ảnh trước khi OCR")
    ap.add_argument("--no-refine", action="store_true",
                    help="tắt bước nhận dạng lại theo ngôn ngữ từng khối/dòng (nhanh hơn, kém chính xác)")
    ap.add_argument("--fast-tables", action="store_true", help="không OCR lại từng ô bảng")
    ap.add_argument("--no-figures", action="store_true",
                    help="không nhận diện hình vẽ/ảnh (mặc định: hình được giữ nguyên dạng ảnh)")
    ap.add_argument("--no-pdf-text", action="store_true",
                    help="PDF có lớp chữ vẫn OCR từ ảnh (mặc định: đọc trực tiếp lớp chữ, kể cả mã TCVN3)")
    ap.add_argument("--no-spellfix", action="store_true", help="không sửa chính tả tiếng Việt sau OCR")
    ap.add_argument("--dict", metavar="FILE", help="từ điển bổ sung (mỗi dòng: 'sai => đúng' hoặc một từ)")
    ap.add_argument("--psm", type=int, default=3, help="chế độ phân trang Tesseract (mặc định 3)")
    ap.add_argument("--pdf-dpi", type=int, default=300, help="độ phân giải khi đọc PDF")
    ap.add_argument("--txt", action="store_true", help="xuất thêm file .txt thuần")
    ap.add_argument("--check", action="store_true", help="chỉ kiểm tra Tesseract và ngôn ngữ đã cài")
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)  # kiểm tra bản đóng gói (CI)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"OCR2Word {__version__}")
    return ap


def expand_inputs(items: List[str]) -> List[Path]:
    out: List[Path] = []
    for it in items:
        p = Path(it)
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.suffix.lower() in IMAGE_EXTS | PDF_EXTS:
                    out.append(child)
        elif p.is_file():
            out.append(p)
        else:
            print(f"Bỏ qua (không tồn tại): {it}", file=sys.stderr)
    return out


def check_environment(tesseract: str | None, tessdata: str | None, langs: List[str]) -> tuple[str | None, list[str]]:
    cmd = tesseract or find_tesseract()
    if not cmd or not Path(cmd).is_file():
        return None, []
    need = [LANG_MAP[code] for code in langs]
    td = resolve_tessdata(cmd, tessdata, need)
    have = list_languages(cmd, str(td) if td else None)
    missing = [code for code in need if code not in have]
    return cmd, missing


def main(argv: List[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # console Windows: in được tiếng Việt / tiếng Nga
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    ap = build_parser()
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("pytesseract").setLevel(logging.INFO)

    if args.selftest:
        return selftest_gui()
    if args.tesseract and not Path(args.tesseract).is_file():
        print(f"Không thấy tesseract tại: {args.tesseract}", file=sys.stderr)
        return 2
    if args.tessdata and not Path(args.tessdata).is_dir():
        print(f"Thư mục tessdata không tồn tại: {args.tessdata}", file=sys.stderr)
        return 2
    cmd, missing = check_environment(args.tesseract, args.tessdata, args.lang)
    if not cmd:
        print("Không tìm thấy Tesseract. Cài Tesseract-OCR (xem README) hoặc dùng --tesseract <đường dẫn>.",
              file=sys.stderr)
        return 2
    if missing:
        print(f"Thiếu dữ liệu ngôn ngữ Tesseract: {', '.join(missing)}. "
              f"Chạy setup_windows.bat hoặc tải file .traineddata vào thư mục tessdata.", file=sys.stderr)
        return 2
    if args.check:
        print(f"Tesseract: {cmd}")
        print(f"Ngôn ngữ: {', '.join(LANG_MAP[l] for l in args.lang)} - OK")
        return 0
    if not args.inputs:
        ap.print_help()
        return 1

    files = expand_inputs(args.inputs)
    if not files:
        print("Không có file đầu vào hợp lệ.", file=sys.stderr)
        return 1

    opts = Options(
        langs=args.lang, tesseract_cmd=cmd, tessdata_dir=args.tessdata,
        deskew=not args.no_deskew, denoise=args.denoise, binarize=args.binarize,
        detect_tables=not args.no_tables, reocr_cells=not args.fast_tables,
        refine_blocks=not args.no_refine, refine_lines=not args.no_refine,
        psm=args.psm, pdf_dpi=args.pdf_dpi, progress=lambda m: print("  " + m),
        detect_figures=not args.no_figures, spellfix=not args.no_spellfix, use_pdf_text=not args.no_pdf_text,
    )
    if args.dict:
        if not Path(args.dict).is_file():
            print(f"Không thấy file từ điển: {args.dict}", file=sys.stderr)
            return 2
        from .spellfix import get_lexicon
        get_lexicon().load_user_dict(Path(args.dict))
    pipe = Pipeline(opts)

    out_dir: Path | None = None
    single_out: Path | None = None
    if args.output:
        op = Path(args.output)
        if op.suffix.lower() == ".docx" and len(files) == 1 and not args.merge:
            single_out = op
        else:
            out_dir = op
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                print(f"Không tạo được thư mục kết quả {out_dir}: {e}", file=sys.stderr)
                return 2

    all_pages = []
    failed: List[Path] = []
    try:
        for f in files:
            print(f"Đang xử lý: {f}")
            try:
                pages = pipe.process_file(str(f))
                if args.merge:
                    all_pages.extend(pages)
                    continue
                target = single_out or (out_dir or f.parent) / (f.stem + ".docx")
                written = export_docx(pages, str(target), font_name=args.font,
                                      keep_line_breaks=args.keep_line_breaks)
                if Path(written) != target:
                    print(f"  ! {target.name} đang mở/bị khoá – đã lưu thành {Path(written).name}")
                print(f"  -> {written}")
                if args.txt:
                    print(f"  -> {write_text(pages, str(Path(written).with_suffix('.txt')))}")
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 - một file lỗi không làm dừng các file còn lại
                log.debug("lỗi xử lý %s", f, exc_info=True)
                print(f"  ✖ Lỗi: {describe_error(e)}", file=sys.stderr)
                failed.append(f)
        if args.merge and all_pages:
            written = export_docx(all_pages, args.merge, font_name=args.font, keep_line_breaks=args.keep_line_breaks)
            print(f"  -> {written}")
            if args.txt:
                print(f"  -> {write_text(all_pages, str(Path(written).with_suffix('.txt')))}")
    except (KeyboardInterrupt, Cancelled):
        print("Đã dừng.", file=sys.stderr)
        return 130
    if failed:
        print(f"Có {len(failed)}/{len(files)} file lỗi: " + ", ".join(p.name for p in failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""Giao diện dòng lệnh: python -m ocr_tool <ảnh/pdf...> [-o thư_mục] [--lang vi en ru] ..."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List

from . import __version__
from .config import find_tesseract, list_languages, tessdata_dir_for
from .export_docx import export_docx
from .lang import LANG_MAP
from .pipeline import Options, Pipeline
from .preprocess import IMAGE_EXTS, PDF_EXTS


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
    if not cmd:
        return None, []
    td = tessdata or (str(tessdata_dir_for(cmd)) if tessdata_dir_for(cmd) else None)
    have = list_languages(cmd, td)
    need = [LANG_MAP[l] for l in langs]
    missing = [l for l in need if l not in have]
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
            out_dir.mkdir(parents=True, exist_ok=True)

    all_pages = []
    for f in files:
        print(f"Đang xử lý: {f}")
        pages = pipe.process_file(str(f))
        if args.merge:
            all_pages.extend(pages)
            continue
        target = single_out or (out_dir or f.parent) / (f.stem + ".docx")
        export_docx(pages, str(target), font_name=args.font, keep_line_breaks=args.keep_line_breaks)
        print(f"  -> {target}")
        if args.txt:
            txt = target.with_suffix(".txt")
            txt.write_text("\n\n".join(p.text for p in pages), encoding="utf-8")
            print(f"  -> {txt}")
    if args.merge:
        target = Path(args.merge)
        target.parent.mkdir(parents=True, exist_ok=True)
        export_docx(all_pages, str(target), font_name=args.font, keep_line_breaks=args.keep_line_breaks)
        print(f"  -> {target}")
        if args.txt:
            target.with_suffix(".txt").write_text("\n\n".join(p.text for p in all_pages), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

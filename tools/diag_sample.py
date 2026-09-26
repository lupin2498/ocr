"""Chẩn đoán nhanh: OCR hai ảnh mẫu (font DejaVu và Times) bằng Tesseract đang cấu hình
(biến môi trường TESSERACT_CMD) và in tiêu đề, cấu trúc đoạn, các ô bảng đọc sai."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool.document import Paragraph, Table  # noqa: E402
from ocr_tool.pipeline import Options, Pipeline  # noqa: E402
from tests.make_sample import TABLE, find_times_fonts, make_sample  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    cmd = os.environ.get("TESSERACT_CMD") or "tesseract"
    ver = subprocess.run([cmd, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    print(f"=== {ver}  ({cmd})")
    tmp = Path(tempfile.mkdtemp())
    samples = {"dejavu": make_sample(str(tmp / "dejavu.png"))}
    fonts = find_times_fonts()
    if fonts:
        samples["times"] = make_sample(str(tmp / "times.png"), font_path=fonts[0], bold_font_path=fonts[1])
    pipe = Pipeline(Options(tesseract_cmd=cmd))
    for name, img in samples.items():
        page = pipe.process_file(img)[0]
        paras = [e for e in page.elements if isinstance(e, Paragraph)]
        tables = [e for e in page.elements if isinstance(e, Table)]
        print(f"--- {name}")
        print(f"  tiêu đề : {paras[0].text!r} ({paras[0].alignment}, đậm={paras[0].bold})")
        body = paras[1]
        print(f"  đoạn 1  : {len(body.lines)} dòng, thụt đầu dòng {body.first_line_indent_in:.2f} in, {body.alignment}")
        print(f"  ký tên  : {paras[-1].text!r} ({paras[-1].alignment})")
        for t in tables:
            grid = {(c.row, c.col): c.text for c in t.cells}
            bad = [(r, c, grid.get((r, c)), exp) for r, row in enumerate(TABLE)
                   for c, exp in enumerate(row) if grid.get((r, c)) != exp]
            print(f"  bảng    : {t.n_rows}x{t.n_cols}, ô sai: {bad}")
        if not tables:
            print("  bảng    : KHÔNG phát hiện")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Chẩn đoán đọc ô bảng: với bảng mẫu toàn ô số (font DejaVu), in kết quả đọc từng ô theo mô hình ngôn ngữ và tỉ lệ
phóng – để chọn cách đọc ô ngắn đúng cho cả dữ liệu tessdata_best (bộ cài Windows) lẫn bản rút gọn."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool.engine import TesseractEngine  # noqa: E402
from ocr_tool.pipeline import _ink_text_height, _peel_ruling  # noqa: E402
from ocr_tool.preprocess import load_inputs, preprocess  # noqa: E402
from ocr_tool.tables import detect_tables  # noqa: E402
from tests.test_batch_tables import _table_image  # noqa: E402


def read(engine, crop, lang, scale):
    img = crop if scale == 1 else cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    pad = 10
    canvas = np.full((img.shape[0] + 2 * pad, img.shape[1] + 2 * pad), 255, dtype=np.uint8)
    canvas[pad:-pad, pad:-pad] = img
    lines = engine.ocr_region(canvas, lang, 6)
    words = [w for ln in lines for w in ln.words]
    conf = sum(w.conf for w in words) / len(words) if words else 0
    return " ".join(ln.text for ln in lines), conf


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    cmd = os.environ.get("TESSERACT_CMD") or "tesseract"
    n = 12
    rows = [[str(r + 1), f"Bu lông M{10 + r}", str(5 * r + 3), "Đạt"] for r in range(n)]
    path = _table_image(Path(tempfile.mkdtemp()) / "so.png", rows, [150, 450, 1300, 1800, 2330], top=250,
                        row_h=64, size=(2480, 250 + 64 * n + 200), title=False)
    engine = TesseractEngine(tesseract_cmd=cmd, refine_blocks=False, refine_lines=False)
    pre = preprocess(load_inputs(str(path))[0].image)
    th = _ink_text_height(pre.gray) or 50.0
    grids = detect_tables(pre.gray, text_height=th)
    print(f"=== bảng số: scale trang {pre.scale:.2f}, cao chữ {th:.1f}, lưới {[(g.n_rows, g.n_cols) for g in grids]}")
    if not grids:
        return 0
    langs = ["eng", "rus+eng", engine.combined_lang, "vie"]
    tally = {(lang, s): 0 for lang in langs for s in (1, 2)}
    for gc in grids[0].cells:
        if gc.col not in (0, 1, 2) or (gc.col == 1 and gc.row > 3):
            continue
        exp = rows[gc.row][gc.col]
        l, t, r, b = gc.bbox
        inset = max(3, int((b - t) * 0.06))
        crop = _peel_ruling(pre.gray[t + inset:b - inset, l + inset:r - inset].copy())
        out = []
        for lang in langs:
            for s in (1, 2):
                text, conf = read(engine, crop, lang, s)
                tally[(lang, s)] += text.strip(".,:;'|") == exp
                out.append(f"{lang}x{s}={text!r}({conf:.0f})")
        print(f"  {exp:>12} crop {crop.shape[1]}x{crop.shape[0]} | " + " ".join(out))
    print("  đúng:", {f"{k[0]}x{k[1]}": v for k, v in tally.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())

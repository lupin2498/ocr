"""Chẩn đoán chi tiết: mã băm ảnh mẫu, OCR vùng tiêu đề và một ô bảng với nhiều thiết lập
(mô hình ngôn ngữ, psm, tỉ lệ phóng) để so sánh giữa các máy / bản build Tesseract."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool.engine import TesseractEngine  # noqa: E402
from ocr_tool.preprocess import load_inputs, preprocess  # noqa: E402
from ocr_tool.tables import detect_tables  # noqa: E402
from tests.make_sample import find_times_fonts, make_sample  # noqa: E402


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()[:12]


def ocr_variants(engine: TesseractEngine, crop: np.ndarray, label: str) -> None:
    print(f"  [{label}] crop {crop.shape[1]}x{crop.shape[0]} md5={md5(crop.tobytes())}")
    for lang in (engine.combined_lang, "vie", "eng"):
        for psm in (6, 7):
            for scale in (1.0, 2.0, 0.6):
                img = crop if scale == 1.0 else cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                pad = 10
                canvas = np.full((img.shape[0] + 2 * pad, img.shape[1] + 2 * pad), 255, dtype=np.uint8)
                canvas[pad:-pad, pad:-pad] = img
                lines = engine.ocr_region(canvas, lang, psm)
                text = " | ".join(ln.text for ln in lines)
                conf = sum(ln.conf for ln in lines) / len(lines) if lines else 0
                print(f"    {lang:<12} psm={psm} x{scale:<3}: {text!r} ({conf:.0f})")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    cmd = os.environ.get("TESSERACT_CMD") or "tesseract"
    ver = subprocess.run([cmd, "--version"], capture_output=True, text=True)
    print("===", (ver.stdout or ver.stderr).strip().replace("\n", " / ")[:300])
    print("OMP_THREAD_LIMIT =", os.environ.get("OMP_THREAD_LIMIT"))
    tmp = Path(tempfile.mkdtemp())
    samples = {"dejavu": make_sample(str(tmp / "dejavu.png"))}
    fonts = find_times_fonts()
    if fonts:
        samples["times"] = make_sample(str(tmp / "times.png"), font_path=fonts[0], bold_font_path=fonts[1])
    engine = TesseractEngine(tesseract_cmd=cmd, refine_blocks=False, refine_lines=False)
    for name, path in samples.items():
        print(f"--- {name}: file md5={md5(Path(path).read_bytes())}")
        li = load_inputs(path)[0]
        pre = preprocess(li.image)
        gray = pre.gray
        print(f"  gray {gray.shape[1]}x{gray.shape[0]} md5={md5(gray.tobytes())} scale={pre.scale} angle={pre.angle}")
        blocks = engine.ocr_page(gray)
        first = blocks[0]
        print(f"  khối đầu: {first.text!r} conf={first.conf:.0f} bbox={first.bbox}")
        l, t, r, b = first.bbox
        pad = 20
        ocr_variants(engine, gray[max(0, t - pad):b + pad, max(0, l - pad):r + pad], "tiêu đề")
        grids = detect_tables(gray, text_height=45)
        if grids:
            g = grids[0]
            cell = next((c for c in g.cells if (c.row, c.col) == (1, 3)), None)
            if cell:
                l, t, r, b = cell.bbox
                inset = max(3, int((b - t) * 0.06))
                ocr_variants(engine, gray[t + inset:b - inset, l + inset:r - inset], "ô (1,3)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

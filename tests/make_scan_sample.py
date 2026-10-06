"""Trang mẫu kiểu sách scan (photocopy): nền xám, bóng đen ở mép trang, tiêu đề chạy có gạch chân,
một khung chữ nhật bao quanh tiêu đề + sơ đồ + đoạn văn (như sách 'Bản đồ về dòng tiền')."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.make_sample import BOLD_CANDIDATES, FONT_CANDIDATES, _font, wrap  # noqa: E402

HEADER = "28  PHẦN I: TÔI ĐANG CỐNG HIẾN NHƯ THẾ NÀO CHO CÔNG TY?"
BOX_TITLE = "Bán hàng cho khách"
BOX_TEXT = ("Sơ đồ trên không đề cập tới chi phí phải trả (chi phí thuê nhân công, chi phí xúc tiến "
            "bán hàng) để bán được hàng hóa và tạo ra doanh thu.")
BODY = ("Lợi thế thương mại là những tài sản vô hình như uy tín, bí quyết kinh doanh và sức mạnh "
        "thương hiệu. Những giá trị này không thể có ngay lập tức mà phải trải qua nhiều công sức.")
LABELS = ["TIỀN MẶT", "NỢ", "HÀNG HÓA", "TÀI SẢN RÒNG"]


def make_scan_sample(out_path: str, dpi: int = 300) -> str:
    W, H = int(6.0 * dpi), int(8.5 * dpi)
    img = Image.new("L", (W, H), 225)  # nền xám kiểu photocopy
    d = ImageDraw.Draw(img)
    body = _font(FONT_CANDIDATES, 40)
    bold = _font(BOLD_CANDIDATES, 46)
    small = _font(FONT_CANDIDATES, 30)
    m = int(0.7 * dpi)
    # bóng đen mép trái (gáy sách) và mép dưới
    for x in range(0, 70):
        d.line([(x, 0), (x, H)], fill=int(30 + x * 2.5))
    d.rectangle([0, H - 40, W, H], fill=60)
    # tiêu đề chạy + gạch chân
    d.text((m, 120), HEADER, font=small, fill=20)
    d.line([(m, 170), (W - m, 170)], fill=20, width=3)
    y = 260
    for ln in wrap(d, BODY, body, W - 2 * m):
        d.text((m, y), ln, font=body, fill=15)
        y += 58
    y += 50
    # khung bao: tiêu đề + sơ đồ + đoạn văn
    box_top = y
    d.text((m + 60, y + 40), BOX_TITLE, font=bold, fill=10)
    gy = y + 140
    gx0, gx1 = m + 260, W - m - 260
    d.rectangle([gx0, gy, gx1, gy + 420], outline=0, width=6)
    d.line([((gx0 + gx1) // 2, gy), ((gx0 + gx1) // 2, gy + 420)], fill=0, width=6)
    d.line([(gx0, gy + 210), (gx1, gy + 210)], fill=0, width=4)
    for k, lab in enumerate(LABELS):
        lx = gx0 + 30 + (k % 2) * ((gx1 - gx0) // 2)
        ly = gy + 60 + (k // 2) * 210
        d.text((lx, ly), lab, font=small, fill=0)
    d.ellipse([gx0 - 200, gy + 120, gx0 - 110, gy + 210], outline=0, width=5)   # người mua
    d.line([(gx0 - 155, gy + 210), (gx0 - 155, gy + 330)], fill=0, width=5)
    d.line([(gx0 - 110, gy + 260), (gx0, gy + 260)], fill=0, width=4)
    y = gy + 480
    for ln in wrap(d, BOX_TEXT, body, W - 2 * m - 120):
        d.text((m + 60, y), ln, font=body, fill=15)
        y += 58
    d.rectangle([m, box_top, W - m, y + 40], outline=0, width=5)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    arr = np.array(img).astype(np.int16) + np.random.default_rng(3).normal(0, 6, (H, W)).astype(np.int16)
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(out_path, dpi=(dpi, dpi))
    return out_path


if __name__ == "__main__":
    print(make_scan_sample(sys.argv[1] if len(sys.argv) > 1 else "scan_sample.png"))

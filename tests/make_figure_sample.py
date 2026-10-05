"""Trang mẫu giống sách kỹ thuật scan: chữ 'ư' in thành dấu trừ (đ−ợc, tr−ớc), hình vẽ kỹ thuật
có nhãn chú thích, dòng 'Hình ...', một ảnh chụp (vùng bán sắc) và một bảng thật."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.make_sample import BOLD_CANDIDATES, FONT_CANDIDATES, _font, wrap  # noqa: E402

PARA1 = ("Máy nén đ−ợc lắp tr−ớc buồng đốt, không khí đ−ợc nén theo h−ớng trục. "
         "L−ợng không khí qua máy nén phụ thuộc vào vòng quay của rôto.")
PARA2 = ("Vỏ máy nén gồm vỏ tr−ớc, vỏ giữa và vỏ sau. Các lá dẫn dòng đ−ợc chế tạo "
         "thành khối, nh− vậy bảo đảm độ cứng vững cho stato.")
CAPTION1 = "Hình 2.1. Vỏ máy nén"
CAPTION2 = "Hình 2.2. Ảnh chụp động cơ"
LABELS = ["Vỏ trước", "Vỏ giữa", "Vỏ sau", "Lá dẫn dòng", "Mặt bích"]
TABLE = [["Tầng nén", "1", "2", "3"], ["Số lượng lá", "21", "23", "33"]]


def draw_mechanical(d: ImageDraw.ImageDraw, x0: int, y0: int, w: int, h: int, font) -> None:
    """Vẽ một bản vẽ cắt dọc đơn giản: thân côn, gạch mặt cắt, trục, ổ bi, đường dẫn nhãn."""
    cx, cy = x0 + w // 2, y0 + h // 2
    # thân côn
    poly = [(x0 + 120, cy - 150), (x0 + w - 160, cy - 230), (x0 + w - 160, cy + 230), (x0 + 120, cy + 150)]
    d.polygon(poly, outline="black", width=5)
    # gạch mặt cắt
    for i in range(0, w - 300, 28):
        d.line([(x0 + 140 + i, cy - 140), (x0 + 170 + i, cy - 180)], fill="black", width=2)
        d.line([(x0 + 140 + i, cy + 180), (x0 + 170 + i, cy + 140)], fill="black", width=2)
    # trục
    d.rectangle([x0 + 40, cy - 35, x0 + w - 60, cy + 35], outline="black", width=4)
    d.line([(x0 + 20, cy), (x0 + w - 30, cy)], fill="black", width=2)
    # ổ bi
    for bx in (x0 + 200, x0 + w - 260):
        d.ellipse([bx - 60, cy - 100, bx + 60, cy + 20], outline="black", width=4)
        for k in range(8):
            a = 2 * math.pi * k / 8
            d.ellipse([bx + 40 * math.cos(a) - 9, cy - 40 + 40 * math.sin(a) - 9,
                       bx + 40 * math.cos(a) + 9, cy - 40 + 40 * math.sin(a) + 9], fill="black")
    # các tầng lá
    for k in range(6):
        xx = x0 + 330 + k * 120
        d.line([(xx, cy - 190 + k * 8), (xx + 40, cy - 60)], fill="black", width=4)
        d.line([(xx, cy + 190 - k * 8), (xx + 40, cy + 60)], fill="black", width=4)
    # nhãn + đường dẫn
    pts = [(x0 + 160, cy - 160), (x0 + w // 2, cy - 200), (x0 + w - 200, cy - 220), (x0 + 400, cy + 170),
           (x0 + w - 170, cy + 200)]
    lab_pos = [(x0 + 10, y0 + 5), (x0 + w // 2 - 80, y0 + 5), (x0 + w - 230, y0 + 5),
               (x0 + 230, y0 + h - 45), (x0 + w - 250, y0 + h - 45)]
    for (px, py), (lx, ly), lab in zip(pts, lab_pos, LABELS):
        d.text((lx, ly), lab, font=font, fill="black")
        d.line([(lx + 40, ly + (38 if ly < cy else -4)), (px, py)], fill="black", width=2)


def make_figure_sample(out_path: str, dpi: int = 300) -> str:
    W, H = int(8.27 * dpi), int(11.69 * dpi)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    body = _font(FONT_CANDIDATES, 42)
    small = _font(FONT_CANDIDATES, 30)
    margin = int(1.0 * dpi)
    max_w = W - 2 * margin
    y = margin
    for para in (PARA1,):
        for k, ln in enumerate(wrap(d, para, body, max_w)):
            d.text((margin + (120 if k == 0 else 0), y), ln, font=body, fill="black")
            y += 62
    y += 40
    # hình vẽ kỹ thuật
    fig_w, fig_h = 1500, 760
    fx = (W - fig_w) // 2
    draw_mechanical(d, fx, y, fig_w, fig_h, small)
    y += fig_h + 30
    tw = d.textlength(CAPTION1, font=body)
    d.text(((W - tw) / 2, y), CAPTION1, font=body, fill="black")
    y += 90
    for k, ln in enumerate(wrap(d, PARA2, body, max_w)):
        d.text((margin + (120 if k == 0 else 0), y), ln, font=body, fill="black")
        y += 62
    y += 40
    # ảnh chụp (vùng bán sắc)
    ph_w, ph_h = 900, 520
    rng = np.random.default_rng(1)
    yy, xx = np.mgrid[0:ph_h, 0:ph_w]
    photo = (128 + 90 * np.sin(xx / 60.0) * np.cos(yy / 45.0) + rng.normal(0, 18, (ph_h, ph_w))).clip(0, 255)
    photo = Image.fromarray(photo.astype(np.uint8)).convert("RGB")
    px = (W - ph_w) // 2
    img.paste(photo, (px, y))
    y += ph_h + 30
    tw = d.textlength(CAPTION2, font=body)
    d.text(((W - tw) / 2, y), CAPTION2, font=body, fill="black")
    y += 100
    # bảng thật
    col_w = [520, 260, 260, 260]
    xs = [margin]
    for cw in col_w:
        xs.append(xs[-1] + cw)
    ys = [y + i * 90 for i in range(len(TABLE) + 1)]
    for yy_ in ys:
        d.line([(xs[0], yy_), (xs[-1], yy_)], fill="black", width=3)
    for xx_ in xs:
        d.line([(xx_, ys[0]), (xx_, ys[-1])], fill="black", width=3)
    for r, row in enumerate(TABLE):
        for c, txt in enumerate(row):
            d.text((xs[c] + 20, ys[r] + 22), txt, font=body, fill="black")
    img.save(out_path, dpi=(dpi, dpi))
    return out_path


if __name__ == "__main__":
    print(make_figure_sample(sys.argv[1] if len(sys.argv) > 1 else "figure_sample.png"))

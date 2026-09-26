"""Tạo ảnh mẫu (A4 300dpi) chứa tiếng Việt, Anh, Nga và một bảng có kẻ khung để kiểm thử."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    r"C:\Windows\Fonts\times.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    r"C:\Windows\Fonts\timesbd.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]

TITLE = "BÁO CÁO TỔNG HỢP KẾT QUẢ"
VI = ("Đây là đoạn văn bản tiếng Việt dùng để kiểm tra khả năng nhận dạng chữ có dấu. "
      "Công cụ phải giữ nguyên bố cục của trang và các bảng biểu.")
EN = ("This is an English paragraph used to verify recognition accuracy. "
      "The tool should reconstruct the layout and export it to a Word document.")
RU = ("Это русский абзац для проверки распознавания кириллицы. "
      "Программа должна сохранить структуру документа и таблицы.")
TABLE = [
    ["STT", "Tên vật tư", "Số lượng", "Ghi chú"],
    ["1", "Bu lông M10", "120", "Đạt yêu cầu"],
    ["2", "Steel washer", "45", "Imported"],
    ["3", "Гайка М12", "60", "Проверено"],
]


def _font(cands, size):
    for c in cands:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_sample(out_path: str, dpi: int = 300) -> str:
    W, H = int(8.27 * dpi), int(11.69 * dpi)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    body = _font(FONT_CANDIDATES, 42)
    bold = _font(BOLD_CANDIDATES, 64)
    margin = int(1.0 * dpi)
    max_w = W - 2 * margin
    y = margin

    tw = d.textlength(TITLE, font=bold)
    d.text(((W - tw) / 2, y), TITLE, font=bold, fill="black")
    y += 64 + 70

    for para in (VI, EN, RU):
        lines = wrap(d, para, body, max_w)
        for k, ln in enumerate(lines):
            x = margin + (int(0.4 * dpi) if k == 0 else 0)
            d.text((x, y), ln, font=body, fill="black")
            y += 62
        y += 45

    # bảng
    col_w = [int(0.9 * dpi), int(2.6 * dpi), int(1.3 * dpi), int(1.47 * dpi)]
    row_h = 90
    x0, y0 = margin, y
    xs = [x0]
    for cw in col_w:
        xs.append(xs[-1] + cw)
    ys = [y0 + i * row_h for i in range(len(TABLE) + 1)]
    for yy in ys:
        d.line([(xs[0], yy), (xs[-1], yy)], fill="black", width=3)
    for xx in xs:
        d.line([(xx, ys[0]), (xx, ys[-1])], fill="black", width=3)
    for r, row in enumerate(TABLE):
        for c, txt in enumerate(row):
            f = body
            tw = d.textlength(txt, font=f)
            if c in (0, 2):
                tx = xs[c] + (col_w[c] - tw) / 2
            else:
                tx = xs[c] + 20
            d.text((tx, ys[r] + 22), txt, font=f, fill="black")
    y = ys[-1] + 80

    # đoạn canh phải (ký tên)
    sign = "Hà Nội, ngày 26 tháng 9 năm 2026"
    tw = d.textlength(sign, font=body)
    d.text((W - margin - tw, y), sign, font=body, fill="black")

    img.save(out_path, dpi=(dpi, dpi))
    return out_path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample.png"
    print(make_sample(out))

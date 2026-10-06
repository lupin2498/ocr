"""Làm sạch các lỗi OCR có quy luật (rút ra từ đối chiếu với bản gốc TB2-117A):
* ký tự '|', ']', '[' lẫn vào từ đường kẻ bảng;
* đơn vị 'kG/cm2' bị đọc thành 'kG/cm”', 'kG/cm?', 'kG/cmˆ';
* chữ 'I'/'l' bị đọc thay số '1' trong mã hiệu (TB2-I17A -> TB2-117A).
"""
from __future__ import annotations

import re

_BAR_TOKEN = re.compile(r"(?<!\S)[|\[\]{}]+(?!\S)")
_BAR_EDGE = re.compile(r"(?<!\S)[|\[\]](?=\w)|(?<=\w)[|\[\]](?!\S)")
_UNIT_SQ = re.compile(r"(?<=[/\s\d])(cm|mm|m)[”\"?ˆ’'²]")
_CODE_I = re.compile(r"\b(?=[A-ZА-Я0-9-]*\d)[A-ZА-Я0-9]+(?:-[A-ZА-Я0-9Il|]+)+\b")


def _fix_code(m: re.Match) -> str:
    tok = m.group(0)
    # I / l / | nằm giữa hoặc ngay cạnh chữ số trong mã -> 1
    return re.sub(r"(?<=[\d-])[Il|](?=\d)|(?<=\d)[Il|](?=[\d-])", "1", tok)


def clean_ocr_text(text: str) -> str:
    if not text:
        return text
    out = _BAR_TOKEN.sub("", text)
    out = _BAR_EDGE.sub("", out)
    out = _UNIT_SQ.sub(lambda m: m.group(1) + "2", out)
    out = _CODE_I.sub(_fix_code, out)
    return re.sub(r"[ \t]{2,}", " ", out).strip() if out != text else text

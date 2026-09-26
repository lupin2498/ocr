"""Nhận diện hệ chữ (Latin / Việt / Cyrillic) để chọn mô hình OCR phù hợp."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List

# Mã ngôn ngữ người dùng -> mã traineddata của Tesseract
LANG_MAP: Dict[str, str] = {"vi": "vie", "en": "eng", "ru": "rus"}
LANG_NAMES: Dict[str, str] = {"vi": "Tiếng Việt", "en": "Tiếng Anh", "ru": "Tiếng Nga"}

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[A-Za-zÀ-ɏẠ-ỿ]")
# Ký tự chỉ có trong tiếng Việt (có dấu / đ)
_VIET_SPECIFIC = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯ"
    r"àảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ"
    r"ÀẢÃÁẠẰẲẴẮẶẦẨẪẤẬÈẺẼÉẸỀỂỄẾỆÌỈĨÍỊÒỎÕÓỌỒỔỖỐỘỜỞỠỚỢÙỦŨÚỤỪỬỮỨỰỲỶỸÝỴ]"
)


def to_tesseract_langs(codes: Iterable[str]) -> List[str]:
    out = []
    for c in codes:
        c = c.strip().lower()
        if c in LANG_MAP:
            out.append(LANG_MAP[c])
        elif c in LANG_MAP.values():
            out.append(c)
    # loại trùng, giữ thứ tự
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def script_profile(text: str) -> Dict[str, int]:
    return {
        "cyr": len(_CYRILLIC.findall(text)),
        "lat": len(_LATIN.findall(text)),
        "viet": len(_VIET_SPECIFIC.findall(text)),
    }


def choose_lang(text: str, available: List[str]) -> str:
    """Chọn chuỗi lang cho Tesseract ("rus", "vie", "eng", "rus+eng", ...) dựa trên
    kết quả OCR sơ bộ của một khối văn bản. `available` là các mã tesseract được bật."""
    avail = list(available)
    if len(avail) <= 1:
        return "+".join(avail)
    p = script_profile(text)
    letters = p["cyr"] + p["lat"]
    if letters < 3:
        return "+".join(avail)
    cyr_ratio = p["cyr"] / letters

    chosen: List[str] = []
    if cyr_ratio >= 0.85 and "rus" in avail:
        chosen = ["rus"]
    elif cyr_ratio <= 0.15:
        # Latin: phân biệt Việt / Anh
        viet_ratio = p["viet"] / max(1, p["lat"])
        if viet_ratio >= 0.04 and "vie" in avail:
            chosen = ["vie"]
        elif "eng" in avail and "vie" in avail:
            chosen = ["eng"] if viet_ratio < 0.01 else ["vie", "eng"]
        elif "eng" in avail:
            chosen = ["eng"]
        elif "vie" in avail:
            chosen = ["vie"]
    else:
        # lẫn Cyrillic và Latin
        chosen = [l for l in ("rus", "vie", "eng") if l in avail]
        if "vie" in chosen and "eng" in chosen and p["viet"] == 0:
            chosen.remove("vie")
    if not chosen:
        return "+".join(avail)
    return "+".join(chosen)

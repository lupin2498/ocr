"""Giải mã chữ Việt mã cũ (TCVN3/ABC – phông .VnTime, .VnTimeH) trong lớp chữ của PDF.

Nhiều tài liệu kỹ thuật cũ được soạn bằng phông .VnTime rồi xuất PDF: lớp chữ trong PDF là
mã TCVN3 ("®-îc" = "được", "§éNG C¥" = "ĐỘNG CƠ"). Chữ "ư" (mã 0xAD) thường bị chương trình
xuất PDF đổi thành dấu gạch "-", nên cần khôi phục theo luật âm tiết.
"""
from __future__ import annotations

import re
import unicodedata

# Bảng TCVN3 (ABC) -> Unicode (chữ thường; phông "H" dùng cùng mã cho chữ hoa)
_TCVN3_LOWER = {
    "µ": "à", "¸": "á", "¶": "ả", "·": "ã", "¹": "ạ",
    "¨": "ă", "»": "ằ", "¾": "ắ", "¼": "ẳ", "½": "ẵ", "Æ": "ặ",
    "©": "â", "Ç": "ầ", "Ê": "ấ", "È": "ẩ", "É": "ẫ", "Ë": "ậ",
    "®": "đ",
    "Ì": "è", "Ð": "é", "Î": "ẻ", "Ï": "ẽ", "Ñ": "ẹ",
    "ª": "ê", "Ò": "ề", "Õ": "ế", "Ó": "ể", "Ô": "ễ", "Ö": "ệ",
    "×": "ì", "Ý": "í", "Ø": "ỉ", "Ü": "ĩ", "Þ": "ị",
    "ß": "ò", "ã": "ó", "á": "ỏ", "â": "õ", "ä": "ọ",
    "«": "ô", "å": "ồ", "è": "ố", "æ": "ổ", "ç": "ỗ", "é": "ộ",
    "¬": "ơ", "ê": "ờ", "í": "ớ", "ë": "ở", "ì": "ỡ", "î": "ợ",
    "ï": "ù", "ó": "ú", "ñ": "ủ", "ò": "ũ", "ô": "ụ",
    "\xad": "ư", "õ": "ừ", "ø": "ứ", "ö": "ử", "÷": "ữ", "ù": "ự",
    "ú": "ỳ", "ý": "ý", "û": "ỷ", "ü": "ỹ", "þ": "ỵ",
    # chữ hoa có mũ/móc riêng
    "¡": "Ă", "¢": "Â", "§": "Đ", "£": "Ê", "¤": "Ô", "¥": "Ơ", "¦": "Ư",
}
_TCVN3_SPECIFIC = set("®¸µ¶·¹¨»¾¼½Æ©ÇÊÈÉËÌÐÎÏÑªÒÕÓÔÖ×ÝØÜÞßãáâä«åèæçé¬êíëìîïóñòô\xadõøö÷ùúûüþ§¥¦")
_TRANS = str.maketrans(_TCVN3_LOWER)


def is_tcvn3_font(fontname: str) -> bool:
    f = fontname.lower()
    return ".vn" in f or "vntime" in f or "vnarial" in f or f.startswith("vn")


def is_upper_font(fontname: str) -> bool:
    """.VnTimeH, .VnArialH ... = phông chữ hoa."""
    base = fontname.split("+")[-1].split(",")[0].split("-")[0]
    return base.lower().startswith(".vn") and base.endswith("H")


def looks_tcvn3(text: str) -> bool:
    if not text:
        return False
    hits = sum(ch in _TCVN3_SPECIFIC for ch in text)
    letters = sum(ch.isalpha() for ch in text)
    return letters > 0 and hits / letters > 0.05


def decode_tcvn3(text: str, upper: bool = False) -> str:
    out = text.translate(_TRANS)
    if upper:
        out = out.upper()
    return unicodedata.normalize("NFC", out)


_U_HYPHEN = re.compile(r"(?<=[^\W\d_])-(?=[^\W\d_])")


def restore_u(text: str) -> str:
    """'đ-ợc' -> 'được' sau khi giải mã TCVN3 (chữ ư bị xuất thành '-')."""
    from .spellfix import restore_u_hyphen
    return restore_u_hyphen(text)

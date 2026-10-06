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


# Chữ Cyrillic giống hệt chữ Latin (OCR nhầm mô hình tiếng Nga trong dòng tiếng Việt)
_CYR2LAT = str.maketrans({"А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
                          "С": "C", "Т": "T", "У": "Y", "Х": "X", "а": "a", "е": "e", "о": "o", "р": "p",
                          "с": "c", "у": "y", "х": "x", "Ё": "Ê", "Ш": "III", "і": "i"})
_CYR = re.compile(r"[\u0400-\u04ff]")
_CYR2LAT_KEYS = set("АВЕКМНОРСТУХаеорсухЁШі")
_LEADER = re.compile(r"(?P<title>.*?\S)\s*[.…·•,_\-]{4,}.*?(?P<page>\d{1,4})\s*\S{0,6}\s*$")


def fix_homoglyphs(text: str) -> str:
    """Trong dòng chủ yếu là chữ Latin (tiếng Việt/Anh): đổi chữ Cyrillic trông giống Latin."""
    if not _CYR.search(text):
        return text
    letters = [c for c in text if c.isalpha()]
    cyr = [c for c in letters if _CYR.match(c)]
    latin = len(letters) - len(cyr)
    if not cyr or latin == 0:
        return text
    if len(cyr) > 0.3 * len(letters) and not all(c in _CYR2LAT_KEYS for c in cyr):
        return text  # dòng tiếng Nga thật
    out = []
    for tok in re.split(r"(\s+)", text):
        tc = [c for c in tok if _CYR.match(c)]
        if tc and all(c in _CYR2LAT_KEYS for c in tc):
            tok = tok.translate(_CYR2LAT)
        out.append(tok)
    return "".join(out)


def _is_leader_junk(tok: str) -> bool:
    t = tok.strip(",;:")
    if len(t) < 10:
        return False
    if t.count(".") >= 3:
        return True
    letters = [c for c in t.lower() if c.isalpha()]
    if len(letters) < 10:
        return False
    common = max(letters.count(c) for c in set(letters))
    vowels = sum(c in "aeiouyăâêôơưáàảãạ" for c in letters)
    return common / len(letters) >= 0.2 or vowels / len(letters) < 0.25


_DIGITISH = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "S": "5", "s": "5", "l": "1", "I": "1",
                           "i": "1", "|": "1", "Z": "2", "z": "2", "B": "8", "g": "9", "q": "9"})


_DIGITLIKE = set("0123456789OoDQSsLlIi|ZzBgq")


def _as_page_number(tok: str) -> str:
    """Số trang cuối dòng mục lục bị đọc thành chữ: 'LOS' -> '105'? (chỉ khi gần như toàn ký tự giống số)."""
    t = tok.strip(".,;:")
    if re.fullmatch(r"\d{1,4}", t):
        return t
    conv = t.replace("L", "1").translate(_DIGITISH)
    if 1 <= len(t) <= 4 and re.fullmatch(r"\d{1,4}", conv) and all(c in _DIGITLIKE for c in t):
        return conv
    return ""


def fix_toc_leader(text: str) -> str:
    """Dòng mục lục 'Tên mục ........ 24' (dấu chấm dẫn bị OCR thành ký tự rác) -> 'Tên mục\t24'."""
    # "Doanh thu........lO5" -> tách số trang dính sau dấu chấm dẫn
    m0 = re.search(r"\.{3,}([A-Za-z0-9|]{1,4})\s*$", text)
    if m0 and _as_page_number(m0.group(1)):
        text = text[:m0.start(1)] + " " + _as_page_number(m0.group(1))
    toks = text.split()
    if len(toks) >= 3 and not re.fullmatch(r"\d{1,4}", toks[-1]) and _as_page_number(toks[-1]) and \
            (_is_leader_junk(toks[-2]) or toks[-2].count(".") >= 3):
        toks[-1] = _as_page_number(toks[-1])
        text = " ".join(toks)
    if len(toks) >= 3 and re.fullmatch(r"\d{1,4}", toks[-1]) or (len(toks) >= 4 and re.fullmatch(r"\d{1,4}", toks[-2])):
        idx = len(toks) - 1 if re.fullmatch(r"\d{1,4}", toks[-1]) else len(toks) - 2
        before = toks[idx - 1]
        m2 = re.match(r"(.*?\S)\.{2,}(.*)$", before)
        if _is_leader_junk(before) or (m2 and _is_leader_junk(before)):
            head = " ".join(toks[:idx - 1])
            lead = re.split(r"\.{2,}", before)[0] if not _is_leader_junk(re.split(r"\.{2,}", before)[0]) else ""
            title = (head + " " + lead).strip()
            title = re.split(r"\s*[.…·,_\-«»<>~]{3,}", title)[0].strip().rstrip(" .,")
            return f"{title}\t{toks[idx]}"
    if text.count(".") < 4 and "…" not in text:
        return text
    m = _LEADER.match(text)
    if not m:
        return text
    title = m.group("title").rstrip(" .,")
    return f"{title}\t{m.group('page')}"


def clean_ocr_text(text: str) -> str:
    if not text:
        return text
    text = fix_homoglyphs(fix_toc_leader(text))
    out = _BAR_TOKEN.sub("", text)
    out = _BAR_EDGE.sub("", out)
    out = _UNIT_SQ.sub(lambda m: m.group(1) + "2", out)
    out = _CODE_I.sub(_fix_code, out)
    out = re.sub(r"(?<=\S) ¬ (?=\S)", " → ", out)  # mũi tên in đậm bị đọc thành "¬"
    return re.sub(r"[ \t]{2,}", " ", out).strip() if out != text else text

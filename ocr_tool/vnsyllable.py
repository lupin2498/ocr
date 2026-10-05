"""Kiểm tra âm tiết tiếng Việt hợp lệ theo luật ghép âm (phụ âm đầu + vần + thanh)
và các tiện ích bỏ dấu / tách dấu thanh."""
from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import Optional, Tuple

# Bảng nguyên âm mang thanh: base -> (ngang, huyền, sắc, hỏi, ngã, nặng)
_TONE_TABLE = {
    "a": "aàáảãạ", "ă": "ăằắẳẵặ", "â": "âầấẩẫậ",
    "e": "eèéẻẽẹ", "ê": "êềếểễệ",
    "i": "iìíỉĩị",
    "o": "oòóỏõọ", "ô": "ôồốổỗộ", "ơ": "ơờớởỡợ",
    "u": "uùúủũụ", "ư": "ưừứửữự",
    "y": "yỳýỷỹỵ",
}
TONED_TO_BASE = {}
for _base, _row in _TONE_TABLE.items():
    for _tone, _ch in enumerate(_row):
        TONED_TO_BASE[_ch] = (_base, _tone)

# Bỏ toàn bộ dấu (kể cả dấu mũ, móc, đ) -> "khung" chữ Latin
_SKELETON = {}
for _ch, (_base, _t) in TONED_TO_BASE.items():
    _SKELETON[_ch] = {"ă": "a", "â": "a", "ê": "e", "ô": "o", "ơ": "o", "ư": "u"}.get(_base, _base)
_SKELETON["đ"] = "d"

VIET_LETTERS = set("abcdefghijklmnopqrstuvwxyzđ") | set(TONED_TO_BASE)

ONSETS = ("ngh", "ng", "nh", "ch", "gh", "gi", "kh", "ph", "qu", "th", "tr",
          "b", "c", "d", "đ", "g", "h", "k", "l", "m", "n", "p", "r", "s", "t", "v", "x", "")

# Các vần hợp lệ (không thanh)
_RIMES = """
a ai ao au ay an am ang anh ac ach ap at
ăn ăm ăng ăc ăp ăt
âu ây ân âm âng âc âp ât
e eo en em eng ec ep et
ê êu ên êm ênh êch êp êt
i ia iu in im inh ich ip it
iêu iên iêm iêng iêc iêp iêt
o oa oai oao oay oan oam oang oanh oac oach oap oat
oăn oăm oăng oăc oăt
oe oeo oen oet oi on om ong oc op ot ooc oong
ô ôi ôn ôm ông ôc ôp ôt
ơ ơi ơn ơm ơp ơt
u ua uâ uân uâng uât uây uê uêch uênh uy uya uyu uyên uyêt uyn uynh uych uyt
ui un um ung uc up ut uôi uôn uôm uông uôc uôt uơ
ư ưa ưi ưu ưn ưm ưng ưc ưt ươi ươu ươn ươm ương ươc ươp ươt
y yêu yên yêm yêt
""".split()
RIMES = frozenset(_RIMES)
_STOP_FINALS = ("ch", "c", "p", "t")


def split_tone(syl: str) -> Tuple[str, int]:
    """Tách thanh: 'được' -> ('đươc', 5). Trả về tone = -1 nếu có >1 dấu thanh."""
    out = []
    tone = 0
    for ch in syl:
        info = TONED_TO_BASE.get(ch)
        if info is None:
            out.append(ch)
            continue
        base, t = info
        if t:
            if tone and tone != t:
                return "".join(out) + ch, -1
            tone = t
        out.append(base)
    return "".join(out), tone


def skeleton(word: str) -> str:
    return "".join(_SKELETON.get(ch, ch) for ch in word.lower())


def strip_tone(word: str) -> str:
    return split_tone(word.lower())[0]


@lru_cache(maxsize=200_000)
def is_valid_syllable(syl: str) -> bool:
    """True nếu `syl` (chữ thường hoặc hoa) là âm tiết tiếng Việt hợp lệ về mặt ghép âm."""
    s = unicodedata.normalize("NFC", syl.lower())
    if not s or not all(c in VIET_LETTERS for c in s):
        return False
    base, tone = split_tone(s)
    if tone < 0:
        return False
    for on in ONSETS:
        if not base.startswith(on):
            continue
        rime = base[len(on):]
        if on == "qu":
            # qu + vần không bắt đầu bằng u (qua, quê, quy, quyên, quốc -> qu + ôc)
            if not rime or rime.startswith("u"):
                continue
            if rime not in RIMES and ("u" + rime) not in RIMES:
                continue
        elif on == "gi":
            # gì, giữa, giếng (gi + êng), giờ ...
            if rime == "":
                rime = "i"
            elif rime not in RIMES and ("i" + rime) not in RIMES:
                continue
        else:
            if rime not in RIMES:
                continue
            # chính tả: k/gh/ngh chỉ đứng trước i, e, ê, y(ia, iê...)
            first = rime[0]
            if on in ("k", "gh", "ngh") and first not in "ieêy":
                continue
            if on in ("c", "ng") and first in "ieêy":
                continue
            if on == "g" and first in "eêy":
                continue
            if on == "" and rime.startswith("y") is False and rime.startswith("iê"):
                continue  # iêu/iên đứng đầu phải viết yêu/yên
        if rime.endswith(_STOP_FINALS) and tone not in (2, 5):
            return False
        return True
    return False


def has_viet_marks(word: str) -> bool:
    return any(ch in TONED_TO_BASE and ch not in "aeiouy" for ch in word.lower()) or "đ" in word.lower()

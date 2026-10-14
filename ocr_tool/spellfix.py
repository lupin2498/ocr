"""Sửa chính tả tiếng Việt sau OCR.

Các bước:
1. Chuẩn hoá ký tự dễ nhầm (Ð -> Đ, ký tự điều khiển, dấu gạch các loại).
2. Khôi phục chữ "ư" bị in/đọc thành dấu gạch: "đ-ợc", "tr- ớc", "nh- sau" -> "được", "trước", "như sau"
   (lỗi rất phổ biến ở tài liệu chuyển từ phông TCVN3/.VnTime sang PDF).
3. Sửa từng âm tiết: âm tiết sai luật ghép âm hoặc không có trong từ điển được thay bằng ứng viên
   gần nhất (khác dấu, hoặc sai 1 ký tự) có điểm cao nhất theo tần suất chung + ngữ cảnh
   (cặp từ xuất hiện trong chính tài liệu và trong danh sách từ ghép).
4. Từ điển người dùng: thay thế cố định "sai => đúng" và danh sách từ giữ nguyên.

Chỉ sửa đoạn văn tiếng Việt; tiếng Anh / tiếng Nga, số liệu, ký hiệu kỹ thuật được giữ nguyên.
"""
from __future__ import annotations

import logging
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .vnsyllable import VIET_LETTERS, has_viet_marks, is_valid_syllable, skeleton, split_tone

log = logging.getLogger(__name__)

DATA_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "ocr_tool" / "data"
if not DATA_DIR.is_dir():
    DATA_DIR = Path(__file__).resolve().parent / "data"

_SK_ALPHABET = "abcdefghijklmnopqrstuvwxyz"
_HYPHENS = "-‐‑‒–−"
_CHAR_FIX = {"Ð": "Đ", "Đ": "Đ", "ð": "đ", "đ": "đ", "−": "-", "–": "-",
             "‐": "-", "‑": "-", "‒": "-", "­": ""}
_LEFT_DIGIT_AS_L = {"1": "l", "I": "l", "|": "l", "l": "l"}
_WORD_RE = re.compile(r"^([^\w]*)([\ẁ-ͯ]+?)([^\w]*)$", re.UNICODE)
_CAPTION_RE = re.compile(r"^\s*(hình|bảng|sơ đồ|рис|рисунок|таблица|fig|figure|table)\b", re.I)


def user_dict_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "OCR2Word"
    else:
        base = Path.home() / ".ocr2word"
    return base / "tu_dien.txt"


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def is_viet_token(tok: str) -> bool:
    return bool(tok) and all(c in VIET_LETTERS for c in tok.lower())


def _case_like(src: str, word: str) -> str:
    if len(src) > 1 and src.isupper():
        return word.upper()
    if src[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


# ---------------------------------------------------------------------- từ điển
@dataclass
class Lexicon:
    freq: Dict[str, int] = field(default_factory=dict)
    by_skeleton: Dict[str, List[str]] = field(default_factory=dict)
    by_base: Dict[str, List[str]] = field(default_factory=dict)  # bỏ thanh, giữ mũ/móc
    compounds: Counter = field(default_factory=Counter)  # (w1, w2) -> điểm (từ ghép đã kiểm duyệt)
    words: Set[Tuple[str, str]] = field(default_factory=set)  # cặp âm tiết của từ điển từ chung (pyvi)
    keep: Set[str] = field(default_factory=set)          # từ người dùng muốn giữ nguyên
    replacements: List[Tuple[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, extra_user_dict: Optional[Path] = None) -> "Lexicon":
        lex = cls()
        syl_file = DATA_DIR / "vi_syllables.tsv"
        if syl_file.is_file():
            for line in syl_file.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                w, _, c = line.partition("\t")
                lex.add_word(_nfc(w), int(c or 1))
        comp_file = DATA_DIR / "vi_compounds.txt"
        if comp_file.is_file():
            for line in comp_file.read_text(encoding="utf-8").splitlines():
                lex.add_compound(line)
        words_file = DATA_DIR / "vi_words.txt"
        if words_file.is_file():
            for line in words_file.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#"):
                    sy = _nfc(line).split()
                    lex.words.update(zip(sy, sy[1:]))
        for path in (DATA_DIR / "custom_dict.txt", user_dict_path(), extra_user_dict):
            if path and Path(path).is_file():
                lex.load_user_dict(Path(path))
        return lex

    def add_word(self, w: str, count: int = 1) -> None:
        if w not in self.freq:
            self.by_skeleton.setdefault(skeleton(w), []).append(w)
            self.by_base.setdefault(split_tone(w)[0], []).append(w)
        self.freq[w] = self.freq.get(w, 0) + count

    def add_compound(self, line: str, weight: int = 3) -> None:
        line = _nfc(line.strip().lower())
        if not line or line.startswith("#"):
            return
        sy = line.split()
        for s in sy:
            if is_valid_syllable(s):
                self.add_word(s, 50)
        for a, b in zip(sy, sy[1:]):
            self.compounds[(a, b)] += weight

    def load_user_dict(self, path: Path) -> None:
        """Mỗi dòng: "sai => đúng" (thay thế cố định) hoặc một từ/cụm từ cần giữ nguyên / bổ sung."""
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = _nfc(raw.strip())
            if not line or line.startswith("#"):
                continue
            if "=>" in line:
                wrong, _, right = line.partition("=>")
                if wrong.strip():
                    self.replacements.append((wrong.strip(), right.strip()))
                    self.add_compound(right)
                continue
            for w in line.split():
                self.keep.add(w.lower())
            self.add_compound(line, weight=5)

    def count(self, w: str) -> int:
        return self.freq.get(w, 0)

    def pair(self, a: str, b: str) -> int:
        """Điểm cặp âm tiết liền nhau: từ ghép kiểm duyệt (3+) hoặc có trong từ điển chung (1)."""
        return self.compounds.get((a, b), 0) or (1 if (a, b) in self.words else 0)


_LEXICON: Optional[Lexicon] = None


def get_lexicon() -> Lexicon:
    global _LEXICON
    if _LEXICON is None:
        _LEXICON = Lexicon.load()
    return _LEXICON


# ---------------------------------------------------------------------- mô hình ngữ cảnh của tài liệu
@dataclass
class DocModel:
    unigram: Counter = field(default_factory=Counter)
    bigram: Counter = field(default_factory=Counter)

    @classmethod
    def build(cls, lines: Iterable[str]) -> "DocModel":
        m = cls()
        for line in lines:
            toks = [t.lower() for t in tokenize_words(restore_u_hyphen(normalize_chars(line)))]
            core = [_core(t) for t in toks]
            for c in core:
                if c:
                    m.unigram[c] += 1
            for a, b in zip(core, core[1:]):
                if a and b:
                    m.bigram[(a, b)] += 1
        return m


def _core(tok: str) -> str:
    m = _WORD_RE.match(tok)
    if not m:
        return ""
    core = m.group(2)
    return core.lower() if is_viet_token(core) else ""


def tokenize_words(text: str) -> List[str]:
    return text.split()


# ---------------------------------------------------------------------- bước 1-2
_DASH_IN_WORD = re.compile(r"(?<=[^\W\d_])[\-\u2010-\u2015\u2212]+(?=\s?[^\W\d_])")


def normalize_chars(text: str) -> str:
    text = _nfc(text)
    text = "".join(_CHAR_FIX.get(ch, ch) for ch in text)
    # dấu gạch dài / gạch trừ dính liền chữ (đ—ợc, h—-ớng) -> "-"
    return _DASH_IN_WORD.sub("-", text)


_HY_RE = re.compile(r"([^\W\d_]+|(?<![\w])[1I|])-(\s?)([^\W\d_]*)", re.UNICODE)


def restore_u_hyphen(text: str) -> str:
    """'đ-ợc' -> 'được', 'tr- ớc' -> 'trước', 'nh- sau' -> 'như sau', '1-u' -> 'lưu'.
    Không động đến 'Mi-8', 'T-1', 'TB2-117A', 'la-bi-rin', 'kim loại-gốm'."""

    def repl(m: re.Match) -> str:
        left, space, right = m.group(1), m.group(2), m.group(3)
        after = m.string[m.end():m.end() + 1]
        if left in ("1", "I", "|", "Í", "í", "ì", "ỉ", "ĩ", "ị", "i"):
            if not right:
                return m.group(0)
            L = "l"
        else:
            L = left
        upper = L.isupper() and (len(L) > 1 or right[:1].isupper())
        U = "Ư" if upper else "ư"
        valid_left = is_valid_syllable(L)
        if right:
            joined = L + U + right
            if is_valid_syllable(joined) and (not valid_left or space):
                return joined
            # chỉ phần trái: "nh- sau" -> "như sau"
            if space and not valid_left and is_valid_syllable(L + U) and is_valid_syllable(right):
                return L + U + space + right
            return m.group(0)
        # gạch ở cuối: "nh-," / "nh- " ; bỏ qua nếu sau đó là chữ/số (ký hiệu "T-1", "Mi- 8")
        if after and after.isalnum() and not space:
            return m.group(0)
        if space and after and after.isalnum():
            return m.group(0)
        if not valid_left and is_valid_syllable(L + U):
            return L + U + space
        return m.group(0)

    return _HY_RE.sub(repl, text)


# ---------------------------------------------------------------------- bước 3
def _edits1(sk: str) -> Set[str]:
    splits = [(sk[:i], sk[i:]) for i in range(len(sk) + 1)]
    deletes = {a + b[1:] for a, b in splits if b}
    subs = {a + c + b[1:] for a, b in splits if b for c in _SK_ALPHABET if c != b[0]}
    inserts = {a + c + b for a, b in splits for c in _SK_ALPHABET}
    transposes = {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
    return deletes | subs | inserts | transposes


_OCR_CONFUSION = {"e": ("ư",), "u": ("ư",), "o": ("ơ", "ô"), "a": ("ă", "â"), "ư": ("u",), "ơ": ("o",),
                  "y": ("ơ",)}
_OCR_DIGIT_MAP = {"0": "o", "1": "l", "6": "ô", "8": "8", "5": "s", "$": "s", "|": "l", "€": "e"}


@dataclass
class SpellFixer:
    lex: Lexicon
    doc: DocModel = field(default_factory=DocModel)
    min_ctx_swap: int = 3          # số lần cặp từ phải xuất hiện để đổi dấu một từ đúng luật
    general: bool = True           # dùng từ điển từ chung (vi_words.txt)
    rare_rule: bool = True         # âm tiết hiếm -> âm tiết thông dụng cùng khung chữ
    _stage: int = 1                # 0 = lượt sơ bộ (chỉ sửa chắc chắn)
    _heading: bool = False         # đang sửa dòng tiêu đề in hoa
    changes: Counter = field(default_factory=Counter)

    # ------------------------------------------------------------ ngữ cảnh
    def ctx(self, prev: str, w: str, nxt: str) -> int:
        c = 0
        g = 2 if self.general else 0
        if prev:
            c += self.doc.bigram.get((prev, w), 0) + self.lex.compounds.get((prev, w), 0) + \
                (g if (prev, w) in self.lex.words else 0)
        if nxt:
            c += self.doc.bigram.get((w, nxt), 0) + self.lex.compounds.get((w, nxt), 0) + \
                (g if (w, nxt) in self.lex.words else 0)
        return c

    def known(self, w: str) -> bool:
        return w in self.lex.freq or self.doc.unigram.get(w, 0) >= 3 or w in self.lex.keep

    def prior(self, w: str) -> float:
        return 0.6 * math.log1p(self.lex.count(w)) + 1.0 * math.log1p(self.doc.unigram.get(w, 0))

    # ------------------------------------------------------------ ứng viên
    def candidates(self, w: str, allow_edit: bool) -> List[Tuple[str, float]]:
        """Trả về [(ứng viên, chi phí)]: cùng khung chữ (chỉ khác dấu) chi phí 1, sai 1 ký tự chi phí 3."""
        sk = skeleton(w)
        out: Dict[str, float] = {}
        for c in self.lex.by_skeleton.get(sk, []):
            if c != w:
                out[c] = 1.0
        for i, ch in enumerate(w):
            for alt in _OCR_CONFUSION.get(ch, ()):
                variant = w[:i] + alt + w[i + 1:]
                base, tone = split_tone(variant)
                for c in self.lex.by_base.get(base, []):
                    if c != w and c not in out:
                        out[c] = 1.5 if split_tone(c)[1] == tone else 2.0
        if allow_edit and len(sk) >= 2:
            for e in _edits1(sk):
                for c in self.lex.by_skeleton.get(e, []):
                    if c != w and c not in out:
                        out[c] = 3.0
        return list(out.items())

    def score(self, c: str, cost: float, prev: str, nxt: str) -> float:
        return self.prior(c) + 3.5 * math.log1p(self.ctx(prev, c, nxt)) - 3.0 * cost

    # ------------------------------------------------------------ sửa một âm tiết
    def fix_syllable(self, w_orig: str, prev: str, nxt: str, heading: bool,
                     prev2: str = "", nxt2: str = "") -> str:
        w = w_orig.lower()
        if w in self.lex.keep:
            return w_orig
        valid = is_valid_syllable(w)
        in_lex = w in self.lex.freq
        doc_n = self.doc.unigram.get(w, 0)
        if valid and not in_lex and doc_n < 3:
            allow_valid_edit = False
        else:
            allow_valid_edit = True

        if valid and (in_lex or doc_n >= 3):
            # âm tiết đúng, có trong từ điển: chỉ đổi dấu khi ngữ cảnh tài liệu chỉ rõ
            # (ví dụ "may nén" -> "máy nén", "Ô đỡ" -> "Ổ đỡ")
            if not prev and not nxt:
                return w_orig
            # Từ điển từ ghép (đã kiểm duyệt) thắng thống kê của tài liệu: lỗi OCR lặp lại có hệ thống
            # (phông chữ làm "chi phí" luôn thành "chỉ phí") khiến tài liệu "học" luôn cái sai.
            comp = self.lex.compounds
            lex = self.lex
            if self._stage == 0:
                # lượt sơ bộ: chỉ sửa âm tiết rất hiếm, để lượt sau có ngữ cảnh đúng
                # ("trước nảy sình" -> "nảy sinh" trước, rồi "nảy" mới không bị đổi thành "nay")
                return self._rare_fix(w_orig, w, prev, nxt) or w_orig
            # từ điển chung (pyvi) có nhiều mục lạ ("nhớ chỉ"): không dùng cho luật từ ghép kiểm duyệt
            P = lambda a, b: comp.get((a, b), 0)  # noqa: E731
            # từ bên cạnh đã thuộc từ ghép khác ("bắt đầu" từ, "bản đồ") thì không ghép với nó nữa
            prev_free = bool(prev) and P(prev2, prev) == 0
            nxt_free = bool(nxt) and P(nxt, nxt2) == 0
            if P(prev, w) == 0 and P(w, nxt) == 0 and lex.count(w) < 150_000:
                for c, _cost in self.candidates(w, allow_edit=False):
                    if c in (prev, nxt):
                        continue
                    if (prev_free and comp.get((prev, c), 0) > 0) or (nxt_free and comp.get((c, nxt), 0) > 0):
                        return self._apply(w_orig, c)
            if (prev_free and P(prev, w) > 0) or (nxt_free and P(w, nxt) > 0):
                return w_orig  # đã khớp từ ghép trong từ điển: không đổi theo thống kê tài liệu
            # ngữ cảnh: bỏ phần từ ghép ở phía từ bên cạnh đã thuộc từ ghép khác
            def side_ctx(c: str) -> Tuple[int, int]:
                cl = self.doc.bigram.get((prev, c), 0) + (P(prev, c) if prev_free else 0) if prev else 0
                cr = self.doc.bigram.get((c, nxt), 0) + (P(c, nxt) if nxt_free else 0) if nxt else 0
                return cl, cr
            own_l, own_r = side_ctx(w)
            own = own_l + own_r
            if own <= 1:
                r = self._rare_fix(w_orig, w, prev, nxt)
                if r:
                    return r
            best, best_ctx, best_l, best_r = None, 0, 0, 0
            for c, _cost in self.candidates(w, allow_edit=False):
                if c == prev or c == nxt:
                    continue  # tránh tạo từ lặp "động động"
                cl, cr = side_ctx(c)
                if cl + cr > best_ctx:
                    best, best_ctx, best_l, best_r = c, cl + cr, cl, cr
            if not best:
                return w_orig
            if own == 0:
                need = self.min_ctx_swap if (prev and nxt) else 2 * self.min_ctx_swap
                ok = best_ctx >= max(need, 2 * doc_n)
            else:
                # lỗi OCR lặp lại (CHẾ ĐÔ): ngữ cảnh của ứng viên áp đảo, không bên nào kém đi
                ok = (own <= 4 and best_ctx >= 10 * own and best_l >= own_l and best_r >= own_r
                      and self.lex.count(w) < 20000)
            if ok:
                return self._apply(w_orig, best)
            return w_orig

        # chữ in hoa ngoài dòng tiêu đề = chữ viết tắt / ký hiệu (ĐC, TBMN, BHA...)
        if w_orig.isupper() and len(w_orig) > 1 and not heading:
            return w_orig
        if not valid:
            m = re.fullmatch(r"([^\W\d_]+?)([IVXLC]+)", w_orig)
            if m and m.group(1).islower() and is_valid_syllable(m.group(1)) and self.lex.count(m.group(1)) >= 1000:
                return self._apply(w_orig, m.group(1) + " " + m.group(2))  # "tầngI" -> "tầng I", "PhầnII"
            cut = self._glued_split(w)
            if cut:
                a, b = w_orig[:cut], w_orig[cut:]
                if a[-1:].islower() and b[:1].isupper() and not b.isupper():
                    b = b.lower()  # "CôngtY" -> "Công ty"
                elif b[:1].islower() and b[1:].isupper():
                    b = b.lower()
                return self._apply(w_orig, a + " " + b)  # hai từ bị dính: "củakỳ" -> "của kỳ"
        if not valid and _splits_into_syllables(w):
            return w_orig  # từ mượn viết liền: rôto, stato, buji...
        short = len(w) <= 2
        allow_edit = allow_valid_edit and not valid and \
            not (doc_n >= 5 and not has_viet_marks(w))
        cands = self.candidates(w, allow_edit=allow_edit)
        if not cands:
            return w_orig
        scored = sorted(((self.score(c, cost, prev, nxt), c, cost) for c, cost in cands), reverse=True)
        best_score, best, cost = scored[0]
        if valid and best_score <= self.prior(w) + 3.5 * math.log1p(self.ctx(prev, w, nxt)):
            return w_orig
        k = self.ctx(prev, best, nxt)
        if short and (k == 0 or (cost > 1.5 and k < 5)):
            return w_orig
        if cost > 1.0 and k == 0:  # sai 1 ký tự: bắt buộc có ngữ cảnh ủng hộ
            return w_orig
        if valid and in_lex and k == 0 and self.lex.count(best) < 2000:
            return w_orig  # âm tiết đúng luật nhưng hiếm (néo, ngõng...): chỉ sửa khi có ngữ cảnh
        if k == 0 and self.lex.count(best) < 20 and self.doc.unigram.get(best, 0) < 3:
            return w_orig
        return self._apply(w_orig, best)

    def _glued_split(self, w: str) -> Optional[int]:
        """Hai âm tiết tiếng Việt thông dụng (ít nhất một âm tiết có dấu) bị OCR viết liền."""
        best = None
        for i in range(1, len(w)):
            a, b = w[:i], w[i:]
            ca, cb = self.lex.count(a), self.lex.count(b)
            if ca < 1000 or cb < 1000 or not (is_valid_syllable(a) and is_valid_syllable(b)):
                continue
            ma, mb = has_viet_marks(a), has_viet_marks(b)
            # cả hai có dấu (củakỳ, tăngcứng); hoặc phần không dấu là âm tiết thông dụng mở đầu bằng
            # phụ âm (côngty) — "bịin" (tua bin) không tách thành "bị in"
            ok = (ma and mb) or (ma and len(b) >= 2 and b[0] not in _VOWELS and cb >= 3000) or \
                (mb and len(a) >= 2 and a[0] not in _VOWELS and ca >= 3000)
            if ok and (best is None or min(ca, cb) > best[0]):
                best = (min(ca, cb), i)
        return best[1] if best else None

    def _rare_fix(self, w_orig: str, w: str, prev: str, nxt: str) -> Optional[str]:
        """Âm tiết đúng luật nhưng rất hiếm (nều, tót, hòi, xìn) trong khi một âm tiết rất thông dụng
        cùng khung chữ (nếu, tốt, hỏi, xin) có sẵn: gần như chắc chắn là OCR đọc nhầm dấu.
        Giữ nguyên nếu âm tiết hiếm ghép được với từ bên cạnh (sình lầy, niêm yết, khư khư)."""
        lex = self.lex
        if not self.rare_rule or lex.count(w) >= 50 or w in lex.keep:
            return None
        if (prev and lex.pair(prev, w)) or (nxt and lex.pair(w, nxt)):
            return None
        floor = max(3000, 300 * lex.count(w))
        doc_w = self.doc.unigram.get(w, 0)
        # tài liệu dùng từ hiếm này thật sự (nhiều lần, không kém ứng viên): giữ nguyên.
        # Lỗi OCR có hệ thống ("PHẨN" ở mọi tiêu đề) vẫn ít hơn hẳn từ đúng ("phần") trong tài liệu.
        common = [c for c, cost in self.candidates(w, allow_edit=False)
                  if cost == 1.0 and lex.count(c) >= floor and c not in (prev, nxt)
                  and not (doc_w >= 5 and doc_w >= 0.5 * self.doc.unigram.get(c, 0))]
        # ứng viên phải được ngữ cảnh ủng hộ (cặp từ trong tài liệu / từ điển): "rất tốt", "nếu cần";
        # không có thì giữ ("ngọn lửa phụt", "chả nhằm nhò", "lội ngược dòng" là từ hiếm nhưng đúng)
        # (trừ khi ứng viên phổ biến hơn áp đảo: "nều"/"nếu", "đì"/"đi", "xìn"/"xin")
        # Dòng tiêu đề in hoa: OCR hay sai dấu hơn và ngữ cảnh ngắn -> chỉ cần ứng viên phổ biến hơn hẳn.
        common = [c for c in common if self.ctx(prev, c, nxt) > 0 or self._heading
                  or lex.count(c) >= 3000 * max(1, lex.count(w))]
        if not common:
            return None
        c = max(common, key=lambda c: (self.ctx(prev, c, nxt), lex.count(c)))
        return self._apply(w_orig, c)

    def _apply(self, src: str, new: str) -> str:
        out = _case_like(src, new)
        if out != src:
            self.changes[(src, out)] += 1
        return out

    # ------------------------------------------------------------ cặp hay nhầm có luật riêng
    _DE_PREV = {"vấn", "chủ", "tiêu", "đầu", "mệnh", "nan", "luận", "đáp", "giải", "đặt", "nêu", "câu",
                "bàn", "nhan", "chuyên", "đối"}
    _DE_NEXT = {"nghị", "xuất", "cập", "tài", "ra", "án", "cương", "phòng", "bạt", "cao", "thi", "bài", "mục",
                "tựa", "kháng", "đạt", "can", "cử", "từ", "hình", "lao", "nghi", "pa", "tặng", "ốc"}

    def confusion_rule(self, w_orig: str, prev: str, nxt: str) -> Optional[str]:
        w = w_orig.lower()
        if w == "đề" and prev not in self._DE_PREV and nxt not in self._DE_NEXT and nxt and \
                self.lex.compounds.get((prev, "đề"), 0) == 0 and self.lex.compounds.get(("đề", nxt), 0) == 0:
            return self._apply(w_orig, "để")
        return None

    # ------------------------------------------------------------ sửa một dòng
    def fix_tokens(self, tokens: Sequence[str], heading: bool = False,
                   left: str = "", right: str = "") -> List[str]:
        parts = []
        for t in tokens:
            m = _WORD_RE.match(t)
            if m and is_viet_token(m.group(2)):
                parts.append([m.group(1), m.group(2), m.group(3)])
            else:
                parts.append([None, t, None])
        cores = [p[1].lower() if p[0] is not None else "" for p in parts]
        for i, p in enumerate(parts):
            if p[0] is None:
                continue
            if i > 0 and (parts[i - 1][1].endswith("-") or (parts[i - 1][2] or "").endswith("-")) \
                    or (p[0] and p[0].endswith("-")):
                continue  # mảnh vỡ của từ bị gạch (Í- ợng): không đoán
            prev = cores[i - 1] if i > 0 and not parts[i - 1][2] else ""
            nxt = cores[i + 1] if i + 1 < len(parts) and not p[2] else ""
            if i == 0 and not p[0]:
                prev = left  # từ cuối dòng trước (cùng đoạn): "dõng | dạc tuyên bố"
            if i + 1 == len(parts) and not p[2]:
                nxt = right
            prev2 = cores[i - 2] if prev and i > 1 and not parts[i - 2][2] else ""
            nxt2 = cores[i + 2] if nxt and i + 2 < len(parts) and not parts[i + 1][2] else ""
            new = (self.confusion_rule(p[1], prev, nxt) if self._stage else None) or \
                self.fix_syllable(p[1], prev, nxt, heading, prev2, nxt2)
            if new != p[1]:
                p[1] = new
                cores[i] = new.lower()
        return [(p[0] or "") + p[1] + (p[2] or "") if p[0] is not None else p[1] for p in parts]

    def fix_line(self, text: str, heading: Optional[bool] = None, left: str = "", right: str = "") -> str:
        """`left`/`right`: từ cuối dòng trước / từ đầu dòng sau của cùng đoạn (ngữ cảnh qua chỗ xuống dòng)."""
        if not text.strip():
            return text
        text = restore_u_hyphen(normalize_chars(text))
        if heading is None:
            letters = [c for c in text if c.isalpha()]
            heading = bool(letters) and len(text.split()) >= 3 and \
                sum(c.isupper() for c in letters) / len(letters) > 0.8
        toks = text.split(" ")
        self._heading = heading
        self._stage = 0
        try:
            toks = self.fix_tokens(toks, heading=heading, left=left, right=right)
        finally:
            self._stage = 1
        fixed = self.fix_tokens([t for t in toks], heading=heading, left=left, right=right)
        # lượt 2: dùng từ đã sửa làm ngữ cảnh
        fixed = self.fix_tokens(fixed, heading=heading, left=left, right=right)
        out = " ".join(fixed)
        out = re.sub(r"(?<=[^\W\d_]{3})(?<![A-Z0-9])(\d)", lambda m: " " + m.group(1), out) \
            if has_viet_marks(out) and re.search(r"[^\W\d_]\d", out) and _viet_word_digit(out) else out
        for wrong, right in self.lex.replacements:
            out = out.replace(wrong, right)
        return out


_VOWELS = set("aăâeêioôơuưyàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")


def line_context(lines: Sequence[str], k: int) -> Tuple[str, str]:
    """Âm tiết cuối dòng k-1 và đầu dòng k+1 (khi câu chạy liền qua chỗ xuống dòng)."""
    left = right = ""
    if k > 0:
        m = re.search(r"([^\W\d_]+)\s*$", lines[k - 1])
        if m and is_viet_token(m.group(1)):
            left = m.group(1).lower()
    if k + 1 < len(lines):
        m = re.match(r"\s*([^\W\d_]+)(?=\s|$)", lines[k + 1])
        if m and is_viet_token(m.group(1)):
            right = m.group(1).lower()
    return left, right


def _splits_into_syllables(w: str) -> bool:
    """'rôto' -> rô|to ; 'stato' -> không ('s' không thành âm tiết)."""
    n = len(w)
    if n < 4:
        return False
    for i in range(2, n - 1):
        a, b = w[:i], w[i:]
        if b[0] in _VOWELS:
            continue  # âm tiết sau của từ mượn bắt đầu bằng phụ âm (rô|to, bu|ji)
        if is_valid_syllable(a) and (is_valid_syllable(b) or _splits_into_syllables(b)):
            return True
    return False


def _viet_word_digit(text: str) -> bool:
    for m in re.finditer(r"([^\W\d_]+)(\d+)", text):
        w = m.group(1)
        if has_viet_marks(w) and is_valid_syllable(w):
            return True
    return False


def is_vietnamese_text(text: str) -> bool:
    letters = [c for c in text.lower() if c.isalpha()]
    if len(letters) < 2:
        return True
    cyr = sum("Ѐ" <= c <= "ӿ" for c in letters)
    if cyr > 0.5 * len(letters):
        return False
    words = [w for w in re.findall(r"[^\W\d_]+", text.lower())]
    if not words:
        return True
    viet = sum(1 for w in words if has_viet_marks(w))
    return viet / len(words) >= 0.12 or len(words) <= 3

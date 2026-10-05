"""Kiểm thử sửa chính tả tiếng Việt sau OCR."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool.spellfix import (DocModel, Lexicon, SpellFixer, get_lexicon, is_vietnamese_text,  # noqa: E402
                               normalize_chars, restore_u_hyphen)
from ocr_tool.vnsyllable import is_valid_syllable  # noqa: E402


def fix(s: str) -> str:
    return restore_u_hyphen(normalize_chars(s))


def test_syllable_validator():
    for w in ("được", "trước", "hướng", "khuếch", "nghiêng", "quyền", "giữa", "gì", "xoáy", "ổ", "đỡ"):
        assert is_valid_syllable(w), w
    for w in ("cấnh", "cy", "dầuưkhí", "nhưsau", "tiêt", "vđng"):
        assert not is_valid_syllable(w), w


def test_restore_u_from_hyphen():
    cases = {
        "nó đ-ợc thiết kế": "nó được thiết kế",
        "tr- ớc tua bin": "trước tua bin",
        "h- ớng trục": "hướng trục",
        "nh- sau:": "như sau:",
        "Nh- vậy": "Như vậy",
        "1-u l-ợng": "lưu lượng",
        "Ð-a vào": "Đưa vào",
        "nh-ng": "nhưng",
        "Ch-ơng1": "Chương1",
        "TR- ỚC": "TRƯỚC",
        "đ−ợc": "được",
        "đ—ợc": "được",
    }
    for src, want in cases.items():
        assert fix(src) == want, (src, fix(src))


def test_keep_codes_and_compounds():
    for s in ("Mi-8", "TB2-117A", "la-bi-rin", "kim loại-gốm", "dầu-khí", "CA-64", "T-1", "từ 1-2 lần",
              "- Tự động", "PO-40BP"):
        assert fix(s) == s, s


def _fixer(doc_lines):
    return SpellFixer(get_lexicon(), DocModel.build(doc_lines))


def test_context_corrections():
    doc = ["Máy nén là bộ phận chính.", "máy nén hướng trục"] * 4 + [ "Ổ đỡ thứ nhất", "ổ đỡ thứ hai",
           "chế độ cất cánh", "ở chế độ cất cánh", "chế độ cất cánh khi", "ổ bi đũa", "ổ bi đũa của rôto",
           "ổ bi đũa lắp", "ổ đỡ thứ ba", "vỏ ổ đỡ", "ổ đỡ trước", "ổ đỡ sau", "lắp ổ đỡ", "hệ thống phòng băng", "van phòng băng", "phòng băng của"]
    f = _fixer(doc + ["may nén ly tâm", "Ô đỡ thứ ba", "ở chế độ cất cănh", "định mữtc"])
    assert f.fix_line("Lắp may nén") == "Lắp máy nén"
    assert f.fix_line("Ô đỡ thứ ba") == "Ổ đỡ thứ ba"
    assert f.fix_line("ở chế độ cất cănh") == "ở chế độ cất cánh"
    assert f.fix_line("vành ngoài của ổ bi đữa của rôto") == "vành ngoài của ổ bi đũa của rôto"
    assert f.fix_line("Van phing bang") == "Van phòng băng"


def test_no_damage_to_correct_text():
    f = _fixer([])
    good = ["Động cơ TB2-117A là động cơ tua bin khí có tua bin tự do.",
            "Nhờ đó làm tăng tỷ số giãn nở của dòng khí cháy.",
            "Máy phát khởi động dòng 1 chiều: TC-18MO hoặc TC-18TO",
            "gồm có truyền cảm và khối điện tử.",
            "Rôto máy nén gồm 3 cụm chính, ĐC hoạt động ở chế độ ĐM."]
    for s in good:
        assert f.fix_line(s) == s, (s, f.fix_line(s))


def test_language_gate():
    assert not is_vietnamese_text("Это русский абзац для проверки распознавания")
    assert not is_vietnamese_text("This is an English paragraph used to verify recognition")
    assert is_vietnamese_text("Đây là đoạn văn bản tiếng Việt")


def test_user_dictionary(tmp_path):
    d = tmp_path / "tu_dien.txt"
    d.write_text("# test\nCKHA => СКНА\nống khuếch tán\n", encoding="utf-8")
    lex = Lexicon.load(extra_user_dict=d)
    f = SpellFixer(lex, DocModel())
    assert "СКНА-22" in f.fix_line("Thiết bị điểm lửa CKHA-22")
    assert ("ống", "khuếch") in lex.compounds

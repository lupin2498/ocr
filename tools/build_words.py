"""Tạo ocr_tool/data/vi_words.txt (từ nhiều âm tiết tiếng Việt) từ danh sách từ của pyvi
(Viet-Trung Tran, giấy phép MIT): https://github.com/trungtv/pyvi — tệp pyvi/models/words.txt

Cách chạy:  python tools/build_words.py words.txt
Chỉ giữ từ viết thường có 2–4 âm tiết, mọi âm tiết đúng luật ghép âm (bỏ tên riêng, từ mượn).
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool.vnsyllable import is_valid_syllable  # noqa: E402


def main(src: str) -> None:
    words = set()
    for line in Path(src).read_text(encoding="utf-8").splitlines():
        w = unicodedata.normalize("NFC", line.strip())
        sy = w.split()
        if not 2 <= len(sy) <= 4 or w != w.lower():
            continue
        if all(is_valid_syllable(s) for s in sy):
            words.add(w)
    out = ROOT / "ocr_tool" / "data" / "vi_words.txt"
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# Vietnamese multi-syllable words. Derived from the pyvi word list by Viet-Trung Tran\n")
        f.write("# (https://github.com/trungtv/pyvi, MIT License), filtered to valid lowercase syllables.\n")
        for w in sorted(words):
            f.write(w + "\n")
    print(f"{len(words)} từ -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])

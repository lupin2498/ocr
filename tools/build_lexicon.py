"""Tạo ocr_tool/data/vi_syllables.tsv (âm tiết tiếng Việt + tần suất) từ danh sách tần suất
FrequencyWords (Hermit Dave, dữ liệu OpenSubtitles 2018, CC BY-SA 4.0):
https://github.com/hermitdave/FrequencyWords/blob/master/content/2018/vi/vi_50k.txt

Cách chạy:  python tools/build_lexicon.py vi_50k.txt
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr_tool.vnsyllable import is_valid_syllable  # noqa: E402


def main(src: str) -> None:
    counts = {}
    for line in Path(src).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        word = unicodedata.normalize("NFC", parts[0].lower())
        if not is_valid_syllable(word):
            continue
        counts[word] = counts.get(word, 0) + int(parts[1])
    out = ROOT / "ocr_tool" / "data" / "vi_syllables.tsv"
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# Vietnamese syllables with frequency. Derived from FrequencyWords by Hermit Dave\n")
        f.write("# (OpenSubtitles 2018), licensed CC BY-SA 4.0, filtered to valid Vietnamese syllables.\n")
        for w, c in sorted(counts.items(), key=lambda kv: -kv[1]):
            f.write(f"{w}\t{c}\n")
    print(f"{len(counts)} âm tiết -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])

"""Bao bọc Tesseract: OCR toàn trang, OCR lại theo khối với mô hình ngôn ngữ phù hợp,
OCR lại các dòng có độ tin cậy thấp."""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pytesseract
from pytesseract import Output

from .document import Line, Paragraph, Word, union_bbox
from .lang import choose_lang, script_profile, to_tesseract_langs

log = logging.getLogger(__name__)


@dataclass
class Block:
    """Một khối (Tesseract block) gồm nhiều đoạn."""
    paragraphs: List[Paragraph]
    lang: str = ""

    @property
    def bbox(self):
        return union_bbox([p.bbox for p in self.paragraphs if not p.is_empty()])

    def is_empty(self) -> bool:
        return all(p.is_empty() for p in self.paragraphs)

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)

    @property
    def conf(self) -> float:
        ps = [p for p in self.paragraphs if not p.is_empty()]
        return sum(p.conf for p in ps) / len(ps) if ps else 0.0


class TesseractEngine:
    def __init__(self, langs: Sequence[str] = ("vi", "en", "ru"), tesseract_cmd: Optional[str] = None,
                 tessdata_dir: Optional[str] = None, oem: int = 1,
                 refine_blocks: bool = True, refine_lines: bool = True,
                 line_conf_threshold: float = 70.0,
                 progress: Optional[Callable[[str], None]] = None):
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        self.tessdata_dir = tessdata_dir
        self.langs = to_tesseract_langs(langs) or ["eng"]
        self.oem = oem
        self.refine_blocks = refine_blocks
        self.refine_lines = refine_lines
        self.line_conf_threshold = line_conf_threshold
        self.progress = progress or (lambda msg: None)

    # ------------------------------------------------------------------ cơ bản
    @property
    def combined_lang(self) -> str:
        return "+".join(self.langs)

    def _config(self, psm: int) -> str:
        cfg = f"--oem {self.oem} --psm {psm} -c preserve_interword_spaces=1"
        if self.tessdata_dir:
            cfg += f' --tessdata-dir "{self.tessdata_dir}"'
        return cfg

    def image_to_words(self, img: np.ndarray, lang: str, psm: int) -> List[Tuple[Tuple[int, int, int], Word]]:
        """Trả về [(khoá (block, par, line), Word)] theo thứ tự đọc của Tesseract."""
        data = pytesseract.image_to_data(img, lang=lang, config=self._config(psm), output_type=Output.DICT)
        out = []
        n = len(data["text"])
        for i in range(n):
            if int(data["level"][i]) != 5:
                continue
            text = str(data["text"][i]).strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = 0.0
            w = Word(text, int(data["left"][i]), int(data["top"][i]),
                     int(data["width"][i]), int(data["height"][i]), conf)
            key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
            out.append((key, w))
        return out

    @staticmethod
    def group_words(items: List[Tuple[Tuple[int, int, int], Word]]) -> List[Block]:
        blocks: "OrderedDict[int, OrderedDict[int, OrderedDict[int, Line]]]" = OrderedDict()
        for (b, p, l), w in items:
            blocks.setdefault(b, OrderedDict()).setdefault(p, OrderedDict()).setdefault(l, Line()).words.append(w)
        result: List[Block] = []
        for pars in blocks.values():
            paragraphs = []
            for lines in pars.values():
                lines_list = [ln for ln in lines.values() if ln.words]
                if lines_list:
                    paragraphs.append(Paragraph(lines=lines_list))
            if paragraphs:
                blk_bbox = union_bbox([p.bbox for p in paragraphs])
                for p in paragraphs:
                    p.block_bbox = blk_bbox
                result.append(Block(paragraphs))
        return result

    def ocr_region(self, img: np.ndarray, lang: Optional[str] = None, psm: int = 6) -> List[Line]:
        """OCR một vùng nhỏ (ô bảng) -> danh sách dòng (toạ độ theo ảnh vùng)."""
        if img.size == 0 or min(img.shape[:2]) < 4:
            return []
        items = self.image_to_words(img, lang or self.combined_lang, psm)
        blocks = self.group_words(items)
        return [ln for b in blocks for p in b.paragraphs for ln in p.lines if ln.words]

    # ------------------------------------------------------------------ toàn trang
    def ocr_page(self, gray: np.ndarray, psm: int = 3) -> List[Block]:
        self.progress(f"OCR toàn trang ({self.combined_lang}) ...")
        items = self.image_to_words(gray, self.combined_lang, psm)
        blocks = self.group_words(items)
        for b in blocks:
            b.lang = self.combined_lang
        blocks = self.merge_split_blocks(blocks)
        if self.refine_blocks and len(self.langs) > 1:
            blocks = self._refine_blocks(gray, blocks)
        if self.refine_lines:
            self._refine_lines(gray, blocks)
        for b in blocks:
            b.paragraphs = self.merge_split_paragraphs(b.paragraphs)
        return blocks

    @staticmethod
    def _is_continuation(a: Line, b: Line, body_left: int, ref_right: int, lh: float) -> bool:
        """Dòng b là phần nối tiếp (bị ngắt dòng) của dòng a?
        Điều kiện: khoảng cách dòng bình thường, b không thụt vào so với thân đoạn,
        a gần chạm mép phải (tức là bị ngắt dòng), cỡ chữ tương đương, cùng cột."""
        pitch = b.bbox[1] - a.bbox[1]
        not_indented = b.bbox[0] <= body_left + 0.3 * lh
        wrapped = a.bbox[2] >= ref_right - 2.5 * lh
        same_size = 0.7 <= (b.height / max(1, a.height)) <= 1.4
        same_column = b.bbox[2] > a.bbox[0] and a.bbox[2] > b.bbox[0]
        return 0 < pitch <= 2.0 * lh and not_indented and wrapped and same_size and same_column

    @staticmethod
    def merge_split_paragraphs(paragraphs: List[Paragraph]) -> List[Paragraph]:
        """Tesseract hay tách "dòng đầu thụt vào" và phần còn lại thành hai đoạn; gộp lại."""
        paragraphs = [p for p in paragraphs if not p.is_empty()]
        if len(paragraphs) < 2:
            return paragraphs
        block_bbox = union_bbox([p.bbox for p in paragraphs])
        heights = [ln.height for p in paragraphs for ln in p.lines if ln.words]
        lh = float(np.median(heights)) if heights else 0.0
        if lh <= 0:
            return paragraphs
        out: List[Paragraph] = [paragraphs[0]]
        for nxt in paragraphs[1:]:
            prev = out[-1]
            body_left = min(ln.bbox[0] for ln in prev.lines)
            if TesseractEngine._is_continuation(prev.lines[-1], nxt.lines[0], body_left, block_bbox[2], lh):
                prev.lines.extend(nxt.lines)
            else:
                out.append(nxt)
        return out

    @staticmethod
    def merge_split_blocks(blocks: List[Block]) -> List[Block]:
        """Tesseract (psm 3) đôi khi tách một đoạn 2 dòng thành hai khối; gộp lại
        khi dòng sau rõ ràng là phần ngắt dòng của dòng trước."""
        blocks = [b for b in blocks if not b.is_empty()]
        if len(blocks) < 2:
            return blocks
        heights = [ln.height for b in blocks for p in b.paragraphs for ln in p.lines if ln.words]
        lh = float(np.median(heights)) if heights else 0.0
        if lh <= 0:
            return blocks
        content_right = max(b.bbox[2] for b in blocks)
        out: List[Block] = [blocks[0]]
        for nxt in blocks[1:]:
            prev = out[-1]
            prev_par = prev.paragraphs[-1]
            body_left = min(ln.bbox[0] for ln in prev_par.lines)
            if TesseractEngine._is_continuation(prev_par.lines[-1], nxt.paragraphs[0].lines[0],
                                                body_left, content_right, lh):
                prev_par.lines.extend(nxt.paragraphs[0].lines)
                prev.paragraphs.extend(nxt.paragraphs[1:])
                new_bbox = prev.bbox
                for p in prev.paragraphs:
                    p.block_bbox = new_bbox
            else:
                out.append(nxt)
        return out

    # ------------------------------------------------------------------ tinh chỉnh theo khối
    def _crop(self, gray: np.ndarray, bbox, pad: int):
        h, w = gray.shape[:2]
        l = max(0, bbox[0] - pad)
        t = max(0, bbox[1] - pad)
        r = min(w, bbox[2] + pad)
        b = min(h, bbox[3] + pad)
        return gray[t:b, l:r], l, t

    def _refine_blocks(self, gray: np.ndarray, blocks: List[Block]) -> List[Block]:
        out: List[Block] = []
        # Ưu tiên tiếng Việt cho cả trang nếu phần lớn chữ Latin trên trang có dấu tiếng Việt
        page_profile = script_profile("\n".join(b.text for b in blocks))
        doc_vietnamese = "vie" in self.langs and page_profile["viet"] >= 0.04 * max(1, page_profile["lat"])
        for i, blk in enumerate(blocks):
            if blk.is_empty():
                continue
            lang = choose_lang(blk.text, self.langs)
            n_lines = sum(len(p.lines) for p in blk.paragraphs)
            cand_langs = [lang] if lang != self.combined_lang else []
            # Tiêu đề chữ in hoa thường bị mất dấu ở lượt đầu -> thử thêm mô hình tiếng Việt
            if "vie" in self.langs and lang != "vie" and n_lines <= 3 and "rus" not in lang:
                cand_langs.append("vie")
            if not cand_langs:
                out.append(blk)
                continue
            self.progress(f"Nhận dạng lại khối {i + 1}/{len(blocks)} bằng mô hình [{' | '.join(cand_langs)}] ...")
            pad = max(6, int(blk.paragraphs[0].median_line_height * 0.5))
            crop, ox, oy = self._crop(gray, blk.bbox, pad)
            best = blk
            best_conf = blk.conf
            for cl in cand_langs:
                items = self.image_to_words(crop, cl, psm=6)
                items = [(k, w.shifted(ox, oy)) for k, w in items]
                new_blocks = self.group_words(items)
                if not new_blocks:
                    continue
                # gộp toàn bộ đoạn nhận được thành một khối, giữ bbox mốc canh lề của khối cũ
                paragraphs = [p for nb in new_blocks for p in nb.paragraphs]
                for p in paragraphs:
                    p.block_bbox = blk.bbox
                    p.lang = cl
                new_blk = Block(paragraphs, lang=cl)
                new_conf = new_blk.conf
                if len(new_blk.text) < 0.7 * len(blk.text):
                    continue
                # mô hình đúng ngôn ngữ được ưu tiên: chấp nhận nếu không kém quá 3 điểm
                margin = 3.0 if cl == lang else 0.0
                # Trang tiếng Việt: chấp nhận bản có nhiều dấu hơn dù độ tin cậy thấp hơn chút
                # (Tesseract thường "tự tin" hơn với bản mất dấu của tiêu đề chữ in)
                if cl == "vie" and doc_vietnamese and \
                        script_profile(new_blk.text)["viet"] > script_profile(best.text)["viet"]:
                    margin = 8.0
                if new_conf >= best_conf - margin and (best is blk or new_conf > best_conf or margin > 0):
                    log.debug("block %d: %s conf %.1f -> [%s] conf %.1f (accept)", i, best.lang, best_conf, cl, new_conf)
                    best, best_conf = new_blk, new_conf
                else:
                    log.debug("block %d: keep %s conf %.1f (new [%s] %.1f)", i, best.lang, best_conf, cl, new_conf)
            out.append(best)
        return out

    # ------------------------------------------------------------------ tinh chỉnh dòng
    def _refine_lines(self, gray: np.ndarray, blocks: List[Block]) -> None:
        singles = list(self.langs)
        low = []
        for blk in blocks:
            for p in blk.paragraphs:
                for ln in p.lines:
                    if ln.words and ln.conf < self.line_conf_threshold and len(ln.text) >= 2:
                        low.append((blk, p, ln))
        if not low:
            return
        self.progress(f"Nhận dạng lại {len(low)} dòng có độ tin cậy thấp ...")
        for blk, p, ln in low:
            bbox = ln.bbox
            pad = max(4, int((bbox[3] - bbox[1]) * 0.35))
            crop, ox, oy = self._crop(gray, bbox, pad)
            if crop.size == 0:
                continue
            cands = []
            tried = set()
            for lang in [blk.lang] + singles:
                if lang in tried:
                    continue
                tried.add(lang)
                items = self.image_to_words(crop, lang, psm=7)
                words = [w.shifted(ox, oy) for _, w in items]
                if not words:
                    continue
                cand = Line(words)
                cands.append((cand.conf, lang, cand))
            if not cands:
                continue
            cands.sort(key=lambda c: c[0], reverse=True)
            best_conf, best_lang, best = cands[0]
            if best_conf > ln.conf + 5.0 and len(best.text) >= 0.6 * len(ln.text):
                log.debug("line '%s' (%.1f) -> '%s' [%s] (%.1f)", ln.text, ln.conf, best.text, best_lang, best_conf)
                ln.words = best.words

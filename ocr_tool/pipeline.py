"""Chuỗi xử lý: đọc ảnh -> tiền xử lý -> phát hiện bảng -> OCR -> bố cục -> Page."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from statistics import median
from typing import Callable, List, Optional, Sequence

import cv2
import numpy as np

from .document import BBox, Element, Figure, Line, Page, Paragraph, Table, TableCell, Word, union_bbox
from .engine import Block, TesseractEngine
from .lang import choose_lang
from .layout import build_page
from .preprocess import LoadedImage, load_inputs, preprocess
from .figures import (CAPTION_RE, crop_figure, detect_figures, merge_regions, table_is_figure,
                      text_quality)
from .spellfix import DocModel, SpellFixer, get_lexicon, is_vietnamese_text
from .tables import TableGrid, detect_tables, grid_line_mask

log = logging.getLogger(__name__)


@dataclass
class Options:
    langs: Sequence[str] = ("vi", "en", "ru")
    tesseract_cmd: Optional[str] = None
    tessdata_dir: Optional[str] = None
    deskew: bool = True
    denoise: bool = False
    binarize: bool = False
    upscale: bool = True
    detect_tables: bool = True
    reocr_cells: bool = True
    refine_blocks: bool = True
    refine_lines: bool = True
    line_conf_threshold: float = 70.0
    pdf_dpi: int = 300
    psm: int = 3
    detect_figures: bool = True     # nhận diện hình vẽ/ảnh và giữ nguyên dạng ảnh
    spellfix: bool = True           # sửa chính tả tiếng Việt sau OCR
    progress: Optional[Callable[[str], None]] = None


class Pipeline:
    def __init__(self, opts: Options):
        self.opts = opts
        self.progress = opts.progress or (lambda m: None)
        self.engine = TesseractEngine(
            langs=opts.langs, tesseract_cmd=opts.tesseract_cmd, tessdata_dir=opts.tessdata_dir,
            refine_blocks=opts.refine_blocks, refine_lines=opts.refine_lines,
            line_conf_threshold=opts.line_conf_threshold, progress=self.progress)

    # ------------------------------------------------------------------ vào/ra
    def process_file(self, path: str) -> List[Page]:
        images = load_inputs(path, pdf_dpi=self.opts.pdf_dpi)
        pages = []
        for li in images:
            self.progress(f"Trang {li.page_index + 1}/{len(images)} của {li.source}")
            pages.append(self.process_image(li, fix_spelling=False))
        self.fix_spelling(pages)
        return pages

    def fix_spelling(self, pages: List[Page]) -> None:
        """Sửa chính tả tiếng Việt cho cả tài liệu (dùng ngữ cảnh toàn tài liệu)."""
        if not self.opts.spellfix or "vie" not in self.engine.langs or not pages:
            return
        self.progress("Sửa chính tả tiếng Việt ...")
        texts: List[str] = []
        for pg in pages:
            for el in pg.elements:
                if isinstance(el, Paragraph):
                    texts.extend(ln.text for ln in el.lines if ln.words)
                elif isinstance(el, Table):
                    for c in el.cells:
                        texts.extend(c.lines)
        fixer = SpellFixer(get_lexicon(), DocModel.build(texts))
        for pg in pages:
            for el in pg.elements:
                if isinstance(el, Paragraph):
                    for ln in el.lines:
                        if not ln.words:
                            continue
                        old = ln.text
                        if not is_vietnamese_text(old):
                            continue
                        new = fixer.fix_line(old)
                        if new != old:
                            b = ln.bbox
                            conf = ln.conf
                            ln.words = [Word(new, b[0], b[1], b[2] - b[0], b[3] - b[1], conf)]
                elif isinstance(el, Table):
                    for c in el.cells:
                        c.lines = [fixer.fix_line(t) if is_vietnamese_text(t) else t for t in c.lines]
        n = sum(fixer.changes.values())
        if n:
            log.info("đã sửa %d lỗi chính tả", n)
            self.progress(f"Đã sửa {n} lỗi chính tả")

    def process_array(self, img: np.ndarray, dpi: Optional[float] = None, source: str = "") -> Page:
        return self.process_image(LoadedImage(img, dpi, source, 0))

    # ------------------------------------------------------------------ một trang
    def process_image(self, li: LoadedImage, fix_spelling: bool = True) -> Page:
        o = self.opts
        pre = preprocess(li.image, do_deskew=o.deskew, do_denoise=o.denoise,
                         do_binarize=o.binarize, upscale=o.upscale)
        gray = pre.gray
        h, w = gray.shape[:2]
        dpi = li.dpi * pre.scale if li.dpi else None
        if pre.angle:
            log.info("đã xoay %.2f độ", pre.angle)

        blocks = self.engine.ocr_page(gray, psm=o.psm)
        text_h = self._median_line_height(blocks, default=max(12.0, h / 90))

        tables: List[Table] = []
        grids: List[TableGrid] = []
        if o.detect_tables:
            self.progress("Phát hiện bảng ...")
            grids = detect_tables(gray, text_height=text_h)
            if grids:
                blocks, tables = self._extract_tables(gray, blocks, grids)

        figures: List[Figure] = []
        if o.detect_figures:
            self.progress("Nhận diện hình vẽ / ảnh ...")
            tables, fig_boxes = self._find_figures(gray, blocks, tables, text_h)
            if fig_boxes:
                blocks = self._remove_words_in(blocks, fig_boxes)
                figures = [Figure(bbox=b, image=crop_figure(pre.color, b)) for b in fig_boxes]
            # Tesseract hay bỏ sót dòng chữ nằm sát ảnh: đọc lại các vùng mực chưa được nhận dạng
            blocks = blocks + self._recover_missed_text(
                gray, blocks, [f.bbox for f in figures], [t.bbox for t in tables] + [g.bbox for g in grids], text_h)

        elements = self._order_elements(blocks, tables, figures)
        page = build_page(w, h, dpi, elements, source=li.source, page_index=li.page_index)
        if fix_spelling:
            self.fix_spelling([page])
        return page

    @staticmethod
    def _median_line_height(blocks: List[Block], default: float) -> float:
        hs = [ln.height for b in blocks for p in b.paragraphs for ln in p.lines if ln.words]
        return float(median(hs)) if hs else default

    # ------------------------------------------------------------------ hình vẽ / ảnh
    def _find_figures(self, gray: np.ndarray, blocks: List[Block], tables: List[Table], lh: float):
        h, w = gray.shape[:2]
        lines = [ln for b in blocks for p in b.paragraphs for ln in p.lines if ln.words]
        if lines:
            content_w = max(ln.bbox[2] for ln in lines) - min(ln.bbox[0] for ln in lines)
        else:
            content_w = w

        fig_boxes: List[BBox] = []
        real_tables: List[Table] = []
        mask = grid_line_mask(gray, lh) if tables else None
        for t in tables:
            cell_texts = [c.text for c in t.cells]
            if table_is_figure(gray, t.bbox, mask, t.word_boxes, cell_texts, lh):
                log.info("bảng %s được coi là hình vẽ", t.bbox)
                fig_boxes.append(t.bbox)
            else:
                real_tables.append(t)

        regions = detect_figures(gray, lines, lh, exclude=[t.bbox for t in real_tables], content_width=content_w)
        fig_boxes += [r.bbox for r in regions]

        # Đoạn "chữ" toàn ký tự rác -> thực chất là hình (giữ ảnh để không mất nội dung)
        for b in blocks:
            for p in b.paragraphs:
                if p.is_empty():
                    continue
                if any(CAPTION_RE.match(ln.text) for ln in p.lines):
                    continue
                q = text_quality(p.text, p.conf)
                pb = p.bbox
                if q < 0.4 and p.conf < 75:
                    small = (pb[2] - pb[0]) < 2 * lh and (pb[3] - pb[1]) < 2 * lh
                    near_fig = any(self._gap(pb, fb) < 2 * lh for fb in fig_boxes)
                    if not small or near_fig:
                        fig_boxes.append(pb)
        if not fig_boxes:
            return real_tables, []
        fig_boxes = merge_regions(fig_boxes, gap=0.8 * lh)
        # loại vùng hình quá nhỏ (nhiễu)
        fig_boxes = [fb for fb in fig_boxes if (fb[2] - fb[0]) >= 2 * lh and (fb[3] - fb[1]) >= 1.5 * lh]
        # bảng thật nằm trong hình -> thuộc hình
        kept = []
        for t in real_tables:
            ga = max(1, (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]))
            if any(self._inter(t.bbox, fb) > 0.5 * ga for fb in fig_boxes):
                continue
            kept.append(t)
        return kept, sorted(fig_boxes, key=lambda b: (b[1], b[0]))

    def _recover_missed_text(self, gray: np.ndarray, blocks: List[Block], fig_boxes: List[BBox],
                             table_boxes: List[BBox], lh: float) -> List[Block]:
        h, w = gray.shape[:2]
        ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        pad = int(0.35 * lh) + 2
        for b in blocks:
            for p in b.paragraphs:
                for ln in p.lines:
                    for wd in ln.words:
                        ink[max(0, wd.top - pad):wd.bottom + pad, max(0, wd.left - pad):wd.right + pad] = 0
        for bb in list(fig_boxes) + list(table_boxes):
            ink[max(0, bb[1] - pad):bb[3] + pad, max(0, bb[0] - pad):bb[2] + pad] = 0
        ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        if cv2.countNonZero(ink) < lh * lh:
            return []
        joined = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (int(1.2 * lh), max(3, int(0.4 * lh)))))
        contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        found: List[Block] = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            if cw < 1.5 * lh or ch < 0.5 * lh or ch > 6 * lh:
                continue
            if cv2.countNonZero(ink[y:y + ch, x:x + cw]) < 0.03 * cw * ch:
                continue
            m = int(0.3 * lh)
            l, t, r, b = max(0, x - m), max(0, y - m), min(w, x + cw + m), min(h, y + ch + m)
            items = self.engine.image_to_words(gray[t:b, l:r], self.engine.combined_lang, psm=6)
            items = [(k, wd.shifted(l, t)) for k, wd in items]
            for blk in self.engine.group_words(items):
                if blk.is_empty():
                    continue
                if blk.conf >= 60 and text_quality(blk.text, blk.conf) >= 0.5:
                    log.info("đọc bổ sung dòng bị bỏ sót: %r", blk.text[:60])
                    blk.lang = self.engine.combined_lang
                    found.append(blk)
        return found

    @staticmethod
    def _gap(a, b) -> float:
        dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
        dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
        return max(dx, dy)

    @staticmethod
    def _inter(a, b) -> int:
        return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))

    @staticmethod
    def _remove_words_in(blocks: List[Block], boxes: List[BBox]) -> List[Block]:
        for blk in blocks:
            for p in blk.paragraphs:
                for ln in p.lines:
                    ln.words = [wd for wd in ln.words
                                if not any(b[0] <= wd.cx <= b[2] and b[1] <= wd.cy <= b[3] for b in boxes)]
                p.lines = [ln for ln in p.lines if ln.words]
            blk.paragraphs = [p for p in blk.paragraphs if p.lines]
        return [b for b in blocks if b.paragraphs]

    # ------------------------------------------------------------------ bảng
    def _extract_tables(self, gray: np.ndarray, blocks: List[Block], grids: List[TableGrid]):
        def inside(word: Word, bbox, pad=2) -> bool:
            return bbox[0] - pad <= word.cx <= bbox[2] + pad and bbox[1] - pad <= word.cy <= bbox[3] + pad

        # 1) gom từ nằm trong bảng, loại chúng khỏi dòng văn bản
        table_words: List[List[Word]] = [[] for _ in grids]
        for blk in blocks:
            for p in blk.paragraphs:
                for ln in p.lines:
                    keep = []
                    for wd in ln.words:
                        hit = None
                        for gi, g in enumerate(grids):
                            if inside(wd, g.bbox):
                                hit = gi
                                break
                        if hit is None:
                            keep.append(wd)
                        else:
                            table_words[hit].append(wd)
                    ln.words = keep
                p.lines = [ln for ln in p.lines if ln.words]
            blk.paragraphs = [p for p in blk.paragraphs if p.lines]
        blocks = [b for b in blocks if b.paragraphs]

        # 2) tạo bảng, điền chữ từng ô
        tables: List[Table] = []
        for gi, g in enumerate(grids):
            words = table_words[gi]
            lang = choose_lang(" ".join(w.text for w in words), self.engine.langs) if words else self.engine.combined_lang
            tbl = Table(bbox=g.bbox, row_edges=list(g.row_edges), col_edges=list(g.col_edges))
            self.progress(f"Đọc bảng {gi + 1}/{len(grids)} ({g.n_rows}x{g.n_cols}) ...")
            for gc in g.cells:
                cell = TableCell(gc.row, gc.col, gc.row_span, gc.col_span, gc.bbox)
                cell_words = [w for w in words if inside(w, gc.bbox, pad=0)]
                fallback_lines = self._words_to_lines(cell_words)
                lines: List[str] = []
                align_words = cell_words
                if self.opts.reocr_cells:
                    cell_text = " ".join(w.text for w in cell_words)
                    cell_lang = choose_lang(cell_text, self.engine.langs) if len(cell_text) >= 3 else lang
                    lines, re_words = self._ocr_cell(gray, gc.bbox, cell_lang)
                    if re_words:
                        align_words = re_words
                if not lines or (fallback_lines and len(" ".join(lines)) < 0.5 * len(" ".join(fallback_lines))):
                    lines = fallback_lines
                    align_words = cell_words
                cell.lines = lines
                cell.alignment = self._cell_alignment(align_words, gc.bbox)
                tbl.word_boxes.extend(wd.bbox for wd in align_words)
                tbl.cells.append(cell)
            tables.append(tbl)
        return blocks, tables

    def _ocr_cell(self, gray: np.ndarray, bbox, lang: str):
        """OCR lại một ô bảng -> (các dòng chữ, các từ với toạ độ tuyệt đối trên trang)."""
        l, t, r, b = bbox
        inset = max(3, int((b - t) * 0.06))
        l, t, r, b = l + inset, t + inset, r - inset, b - inset
        if r - l < 8 or b - t < 8:
            return [], []
        crop = gray[t:b, l:r]
        # ô trống -> bỏ qua để tiết kiệm thời gian
        if (crop < 128).mean() < 0.002:
            return [], []
        # chữ nhỏ: phóng to 2x để Tesseract nhận dấu tiếng Việt / chữ Cyrillic tốt hơn
        scale = 2 if crop.shape[0] < 120 else 1
        if scale > 1:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        # thêm viền trắng giúp Tesseract nhận dạng tốt hơn
        pad = 10
        canvas = np.full((crop.shape[0] + 2 * pad, crop.shape[1] + 2 * pad), 255, dtype=np.uint8)
        canvas[pad:-pad, pad:-pad] = crop
        lines = self.engine.ocr_region_best(canvas, lang=lang, psm=6)
        texts = [ln.text for ln in lines if ln.text.strip()]
        words = []
        for ln in lines:
            for w in ln.words:
                words.append(Word(w.text, l + (w.left - pad) // scale, t + (w.top - pad) // scale,
                                  w.width // scale, w.height // scale, w.conf))
        return texts, words

    @staticmethod
    def _words_to_lines(words: List[Word]) -> List[str]:
        if not words:
            return []
        words = sorted(words, key=lambda w: (w.cy, w.left))
        lines: List[List[Word]] = []
        for wd in words:
            if lines and abs(wd.cy - lines[-1][-1].cy) < max(wd.height, lines[-1][-1].height) * 0.6:
                lines[-1].append(wd)
            else:
                lines.append([wd])
        return [" ".join(w.text for w in sorted(ln, key=lambda w: w.left)) for ln in lines]

    @staticmethod
    def _cell_alignment(words: List[Word], bbox) -> str:
        if not words:
            return "left"
        l = min(w.left for w in words)
        r = max(w.right for w in words)
        cl, cr = bbox[0], bbox[2]
        cw = max(1, cr - cl)
        left_gap, right_gap = l - cl, cr - r
        if abs(left_gap - right_gap) <= 0.08 * cw and left_gap > 0.08 * cw:
            return "center"
        if right_gap < left_gap * 0.5 and left_gap > 0.15 * cw:
            return "right"
        return "left"

    # ------------------------------------------------------------------ thứ tự đọc
    @staticmethod
    def _order_elements(blocks: List[Block], tables: List[Table], figures: Sequence[Figure] = ()) -> List[Element]:
        paras: List[Paragraph] = [p for b in blocks for p in b.paragraphs if not p.is_empty()]
        elements: List[Element] = []
        pending = sorted(list(tables) + list(figures), key=lambda t: t.top)
        for p in paras:
            while pending and pending[0].top < p.top + (p.median_line_height * 0.5):
                elements.append(pending.pop(0))
            elements.append(p)
        elements.extend(pending)
        return elements


def process_files(paths: Sequence[str], opts: Options) -> List[Page]:
    pipe = Pipeline(opts)
    pages: List[Page] = []
    for p in paths:
        pages.extend(pipe.process_file(p))
    return pages

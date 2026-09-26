"""Chuỗi xử lý: đọc ảnh -> tiền xử lý -> phát hiện bảng -> OCR -> bố cục -> Page."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from statistics import median
from typing import Callable, List, Optional, Sequence

import cv2
import numpy as np

from .document import Element, Line, Page, Paragraph, Table, TableCell, Word
from .engine import Block, TesseractEngine
from .lang import choose_lang
from .layout import build_page
from .preprocess import LoadedImage, load_inputs, preprocess
from .tables import TableGrid, detect_tables

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
            pages.append(self.process_image(li))
        return pages

    def process_array(self, img: np.ndarray, dpi: Optional[float] = None, source: str = "") -> Page:
        return self.process_image(LoadedImage(img, dpi, source, 0))

    # ------------------------------------------------------------------ một trang
    def process_image(self, li: LoadedImage) -> Page:
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
        if o.detect_tables:
            self.progress("Phát hiện bảng ...")
            grids = detect_tables(gray, text_height=text_h)
            if grids:
                blocks, tables = self._extract_tables(gray, blocks, grids)

        elements = self._order_elements(blocks, tables)
        page = build_page(w, h, dpi, elements, source=li.source, page_index=li.page_index)
        return page

    @staticmethod
    def _median_line_height(blocks: List[Block], default: float) -> float:
        hs = [ln.height for b in blocks for p in b.paragraphs for ln in p.lines if ln.words]
        return float(median(hs)) if hs else default

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
    def _order_elements(blocks: List[Block], tables: List[Table]) -> List[Element]:
        paras: List[Paragraph] = [p for b in blocks for p in b.paragraphs if not p.is_empty()]
        elements: List[Element] = []
        pending = sorted(tables, key=lambda t: t.top)
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

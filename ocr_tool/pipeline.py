"""Chuỗi xử lý: đọc ảnh -> tiền xử lý -> phát hiện bảng -> OCR -> bố cục -> Page."""
from __future__ import annotations

import difflib
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .document import BBox, Element, Figure, Line, Page, Paragraph, Table, TableCell, Word, union_bbox
from .engine import Block, TesseractEngine
from .lang import choose_lang, script_profile
from .layout import build_page
from .preprocess import LoadedImage, count_pages, iter_inputs, preprocess
from .figures import (CAPTION_RE, binarize_ink, crop_figure, detect_figures, detect_frames, detect_shaded_panels, merge_regions, table_is_figure,
                      text_quality)
from .ocr_cleanup import clean_ocr_text
from .spellfix import DocModel, SpellFixer, get_lexicon, is_vietnamese_text, line_context
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
    use_pdf_text: bool = True       # PDF có lớp chữ: đọc trực tiếp (kể cả mã TCVN3) thay vì OCR
    detect_figures: bool = True     # nhận diện hình vẽ/ảnh và giữ nguyên dạng ảnh
    workers: int = 0                # số tiến trình OCR song song cho PDF nhiều trang (0 = tự động)
    spellfix: bool = True           # sửa chính tả tiếng Việt sau OCR
    auto_rotate: bool = True        # trang scan bị xoay 90/180/270 độ: tự xoay lại cho đúng chiều
    progress: Optional[Callable[[str], None]] = None
    page_progress: Optional[Callable[[int, int], None]] = None  # (số trang xong, tổng số trang)
    cancel: Optional[Callable[[], bool]] = None                 # trả True khi người dùng bấm Dừng


class Cancelled(BaseException):
    """Người dùng dừng xử lý giữa chừng (BaseException: các khối `except Exception` không nuốt mất)."""


class Pipeline:
    def __init__(self, opts: Options):
        self.opts = opts
        user_progress = opts.progress or (lambda m: None)

        def progress(msg: str) -> None:
            self._check_cancel()  # mỗi bước nhỏ (khối, bảng, trang) đều xem người dùng có bấm Dừng không
            user_progress(msg)

        self.progress = progress
        self.engine = TesseractEngine(
            langs=opts.langs, tesseract_cmd=opts.tesseract_cmd, tessdata_dir=opts.tessdata_dir,
            refine_blocks=opts.refine_blocks, refine_lines=opts.refine_lines,
            line_conf_threshold=opts.line_conf_threshold, progress=self.progress)

    # ------------------------------------------------------------------ vào/ra
    def _check_cancel(self) -> None:
        if self.opts.cancel is not None and self.opts.cancel():
            raise Cancelled()

    def _page_done(self, done: int, total: int) -> None:
        if self.opts.page_progress is not None:
            try:
                self.opts.page_progress(done, total)
            except Exception:  # noqa: BLE001 - lỗi giao diện không được làm hỏng OCR
                log.debug("page_progress lỗi", exc_info=True)

    def process_file(self, path: str) -> List[Page]:
        from .preprocess import is_pdf

        self._check_cancel()
        n = count_pages(path)
        workers = self._n_workers(n)
        pages: List[Page] = []
        if workers > 1 and is_pdf(Path(path)):
            pages = self._process_pdf_parallel(path, n, workers)
        else:
            for li in iter_inputs(path, pdf_dpi=self.opts.pdf_dpi, use_pdf_text=self.opts.use_pdf_text):
                self._check_cancel()
                self.progress(f"Trang {li.page_index + 1}/{n} của {Path(li.source).name}")
                pages.append(self.process_image_safe(li))
                del li
                self._page_done(len(pages), n)
        self._check_cancel()
        strip_running_headers(pages)
        self.fix_spelling(pages)
        if pages and not any(p.text.strip() for p in pages) and \
                not any(isinstance(e, Figure) for p in pages for e in p.elements):
            self.progress(f"⚠ Không nhận ra chữ nào trong {Path(path).name} (trang trắng, ảnh quá mờ hoặc file hỏng)")
        return pages

    def process_image_safe(self, li: LoadedImage) -> Page:
        """Xử lý một trang; nếu lỗi bất ngờ thì giữ nguyên ảnh cả trang thay vì dừng cả tài liệu."""
        try:
            return self.process_image(li, fix_spelling=False)
        except (Cancelled, KeyboardInterrupt):
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("trang %d lỗi, giữ nguyên ảnh trang", li.page_index + 1)
            self.progress(f"Trang {li.page_index + 1}: lỗi xử lý ({e}) – giữ nguyên ảnh trang")
            return self._image_only_page(li)

    @staticmethod
    def _image_only_page(li: LoadedImage) -> Page:
        img = li.image
        h, w = img.shape[:2]
        return build_page(w, h, li.dpi, [Figure(bbox=(0, 0, w, h), image=img.copy())],
                          source=li.source, page_index=li.page_index)

    def _n_workers(self, n_pages: int) -> int:
        if n_pages < 3:
            return 1
        w = self.opts.workers or max(1, min(4, (os.cpu_count() or 2) - 1))
        return max(1, min(w, n_pages))

    def _process_pdf_parallel(self, path: str, n: int, workers: int) -> List[Page]:
        """OCR các trang PDF song song (mỗi tiến trình tự dựng ảnh trang của mình)."""
        import dataclasses
        import multiprocessing
        import shutil
        from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

        from concurrent.futures.process import BrokenProcessPool

        # hàm gọi lại (lambda của giao diện) không gửi được sang tiến trình con
        opts = dataclasses.replace(self.opts, progress=None, page_progress=None, cancel=None, workers=1)
        results: Dict[int, Page] = {}
        self.progress(f"OCR song song {workers} tiến trình, {n} trang ...")
        run_tmp = _new_run_tmpdir()
        ctx = multiprocessing.get_context()
        stop_evt = ctx.Event()  # báo tiến trình con dừng (chúng tự dọn ảnh tạm của tesseract)
        ex = ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_worker_init,
                                 initargs=(opts, run_tmp, stop_evt))
        futs: Dict = {}
        try:
            futs = {ex.submit(_worker_page, path, i): i for i in range(n)}
            pending = set(futs)
            while pending:
                # chờ từng nhịp ngắn: bấm Dừng có hiệu lực ngay, không phải đợi tiến trình con OCR xong cả trang
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                self._check_cancel()
                for fut in done:
                    i = futs[fut]
                    try:
                        results[i] = fut.result()
                    except BrokenProcessPool:
                        raise
                    except Exception as e:  # noqa: BLE001 - trang lỗi: làm lại tuần tự ở dưới
                        log.warning("trang %d lỗi trong tiến trình con: %s", i + 1, e)
                        continue
                    self.progress(f"Xong trang {len(results)}/{n} (trang {i + 1})")
                    self._page_done(len(results), n)
        except BrokenProcessPool as e:
            # tiến trình con chết (thiếu bộ nhớ, bị diệt...): làm nốt các trang còn lại tuần tự
            log.warning("OCR song song gián đoạn (%s) – xử lý tiếp tuần tự", e)
            self.progress("OCR song song bị gián đoạn – xử lý tiếp từng trang ...")
        except BaseException:
            # Dừng / Ctrl+C: tiến trình con dừng ngay sau lệnh tesseract đang chạy (tự xoá ảnh tạm); quá 2 giây
            # thì diệt cả cây tiến trình (kể cả tesseract con – nếu không nó chạy mồ côi và giữ file tạm)
            stop_evt.set()
            for f in futs:
                f.cancel()
            wait([f for f in futs if f.running()], timeout=2.0)
            _kill_pool(ex)
            raise
        finally:
            ex.shutdown(wait=True, cancel_futures=True)
            if run_tmp:
                import time as _time

                for _ in range(5):  # Windows: file vừa được tiến trình bị diệt nhả ra có thể chưa xoá được ngay
                    shutil.rmtree(run_tmp, ignore_errors=True)
                    if not os.path.exists(run_tmp):
                        break
                    _time.sleep(0.3)
        missing = [i for i in range(n) if i not in results]
        if missing:
            from .preprocess import render_pdf_page

            for i in missing:
                self._check_cancel()
                try:
                    li = render_pdf_page(path, i, self.opts.pdf_dpi, self.opts.use_pdf_text)
                except Exception as e:  # noqa: BLE001
                    log.warning("trang %d: không đọc được (%s) – để trang trắng", i + 1, e)
                    results[i] = build_page(2480, 3508, 300.0, [], source=path, page_index=i)
                    continue
                results[i] = self.process_image_safe(li)
                self._page_done(len(results), n)
        return [results[i] for i in range(n)]

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
                    if el.lang == "pdf":
                        continue  # chữ lấy từ lớp chữ PDF: đã chính xác
                    raw = [ln.text for ln in el.lines if ln.words]
                    k = -1
                    for ln in el.lines:
                        if not ln.words:
                            continue
                        k += 1
                        old = ln.text
                        new = clean_ocr_text(old)
                        if is_vietnamese_text(new):
                            left, right = line_context(raw, k)
                            new = fixer.fix_line(new, left=left, right=right)
                        if new != old:
                            b = ln.bbox
                            conf = ln.conf
                            ln.words = [Word(new, b[0], b[1], b[2] - b[0], b[3] - b[1], conf)]
                elif isinstance(el, Table) and not el.from_text_layer:
                    for c in el.cells:
                        cl = [clean_ocr_text(t) for t in c.lines]
                        c.lines = [fixer.fix_line(t) if is_vietnamese_text(t) else t for t in cl if t.strip()]
        for pg in pages:  # tiêu đề chạy đã chuyển vào header: "102 | PHẨN I: ..." -> chỉ sửa phần chữ
            if pg.header_text and not pg.header_text.isdigit():
                parts = pg.header_text.split(" | ")
                parts = [fixer.fix_line(clean_ocr_text(t)) if is_vietnamese_text(t) else t for t in parts]
                pg.header_text = " | ".join(parts)
        n = sum(fixer.changes.values())
        if n:
            log.info("đã sửa %d lỗi chính tả", n)
            self.progress(f"Đã sửa {n} lỗi chính tả")

    def process_array(self, img: np.ndarray, dpi: Optional[float] = None, source: str = "") -> Page:
        return self.process_image(LoadedImage(img, dpi, source, 0))

    # ------------------------------------------------------------------ một trang
    def process_image(self, li: LoadedImage, fix_spelling: bool = True, _rotated: bool = False) -> Page:
        o = self.opts
        text_layer = self._usable_text_layer(li) if o.use_pdf_text else None
        if text_layer is not None:
            # PDF có lớp chữ: ảnh trang dựng đúng toạ độ, không xoay / phóng
            pre = preprocess(li.image, do_deskew=False, do_denoise=False, do_binarize=False, upscale=False)
        else:
            pre = preprocess(li.image, do_deskew=o.deskew, do_denoise=o.denoise,
                             do_binarize=o.binarize, upscale=o.upscale)
        gray = pre.gray
        h, w = gray.shape[:2]
        dpi = li.dpi * pre.scale if li.dpi else None
        if pre.angle:
            log.info("đã xoay %.2f độ", pre.angle)

        if text_layer is not None:
            self.progress("Đọc lớp chữ có sẵn trong PDF (không cần OCR) ...")
            blocks = [Block(list(text_layer.paragraphs), lang="pdf")] if text_layer.paragraphs else []
        else:
            check_rotation = o.auto_rotate and not _rotated
            # kiểm tra hướng trang ngay sau lượt đọc đầu: trang xoay mà nhận dạng lại từng khối/dòng chữ rác
            # thì tốn gấp ~5 lần thời gian một trang thường
            blocks = self.engine.ocr_page(gray, psm=o.psm, refine=not check_rotation)
            if check_rotation:
                if _looks_misoriented(blocks):
                    k = self.engine.best_rotation(gray)
                    if k:
                        how = "lộn ngược" if k == 2 else "xoay ngang"
                        self.progress(f"Trang {li.page_index + 1}: ảnh bị {how} – tự xoay lại cho đúng chiều")
                        log.info("trang %d: xoay ảnh %d độ cho đúng chiều", li.page_index + 1, 90 * k)
                        rot = np.ascontiguousarray(np.rot90(li.image, k))
                        return self.process_image(LoadedImage(rot, li.dpi, li.source, li.page_index),
                                                  fix_spelling=fix_spelling, _rotated=True)
                blocks = self.engine.refine_page(gray, blocks)
        text_h = self._median_line_height(blocks, default=0.0)
        n_good = self._count_good_lines(blocks)
        ink_h = None
        if n_good < 8:
            # ít dòng đọc tin cậy (ảnh gần như chỉ có một bảng kẻ dày / hình): Tesseract hay trả về vài "dòng" rác
            # cao cả trăm px, tuỳ phiên bản dữ liệu ngôn ngữ (tessdata_best còn coi vài dòng rác là "tin cậy").
            # Lấy chiều cao các dòng tin cậy, đối chiếu với cỡ vết mực; nếu không ngưỡng dò bảng sai -> mất bảng
            # hoặc cả bảng thành hình
            cand = self._median_line_height(blocks, default=0.0, good_only=True) or text_h
            ink_h = _ink_text_height(gray)
            if ink_h and (not cand or cand > 2.5 * ink_h):
                cand = ink_h
            text_h = cand
        text_h = text_h or max(12.0, h / 90)
        log.debug("chiều cao dòng chữ %.1f px (%d dòng tin cậy, vết mực %s)", text_h, n_good,
                  f"{ink_h:.1f}" if ink_h else "-")

        tables: List[Table] = []
        grids: List[TableGrid] = []
        if o.detect_tables:
            self.progress("Phát hiện bảng ...")
            if text_layer is not None and text_layer.rules:
                # kẻ bảng vector trong PDF: dựng ảnh chỉ gồm các đường kẻ -> lưới bảng chính xác
                canvas = np.full_like(gray, 255)
                for (x0, y0, x1, y1) in text_layer.rules:
                    cv2.rectangle(canvas, (x0, y0), (max(x0, x1), max(y0, y1)), 0, thickness=-1)
                    cv2.rectangle(canvas, (x0 - 1, y0 - 1), (max(x0, x1) + 1, max(y0, y1) + 1), 0, thickness=2)
                grids = detect_tables(canvas, text_height=text_h)
            else:
                grids = detect_tables(gray, text_height=text_h)
            if grids:
                blocks, tables = self._extract_tables(gray, blocks, grids, from_text_layer=text_layer is not None)

        figures: List[Figure] = []
        if o.detect_figures:
            self.progress("Nhận diện hình vẽ / ảnh ...")
            tables, fig_boxes = self._find_figures(gray, blocks, tables, text_h,
                                                   extra_boxes=text_layer.image_boxes if text_layer else (),
                                                   color=pre.color if text_layer is None else None,
                                                   scale=pre.scale)
            if fig_boxes:
                fig_boxes = self._trim_captions(blocks, fig_boxes)
                blocks = self._remove_words_in(blocks, fig_boxes)
                figures = [Figure(bbox=b, image=crop_figure(pre.color, b)) for b in fig_boxes]
            # Tesseract hay bỏ sót dòng chữ nằm sát ảnh: đọc lại các vùng mực chưa được nhận dạng
            if text_layer is None:
                found = self._recover_missed_text(
                    gray, blocks, [f.bbox for f in figures], [t.bbox for t in tables] + [g.bbox for g in grids],
                    text_h)
                # nhãn ngắn sát mép hình = phần của hình bị cắt thiếu (viền hộp nhạt): nới hình ra, không giữ chữ
                for blk in list(found):
                    bb = blk.bbox
                    near = [f for f in figures if self._gap(bb, f.bbox) < text_h]
                    if near and len(blk.text.split()) <= 4:
                        f = near[0]
                        f.bbox = union_bbox([f.bbox, bb])
                        f.image = crop_figure(pre.color, f.bbox)
                        found.remove(blk)
                blocks = blocks + found

        if text_layer is None:
            blocks = self._drop_lone_glyphs(blocks, text_h, h)
        elements = self._order_elements(blocks, tables, figures)
        page = build_page(w, h, dpi, elements, source=li.source, page_index=li.page_index)
        if fix_spelling:
            self.fix_spelling([page])
        return page

    @staticmethod
    def _good_lines(blocks: List[Block]) -> list:
        lines = [ln for b in blocks for p in b.paragraphs for ln in p.lines if ln.words]
        return [ln for ln in lines if ln.conf >= 50 and text_quality(ln.text, ln.conf) >= 0.5]

    @classmethod
    def _count_good_lines(cls, blocks: List[Block]) -> int:
        return len(cls._good_lines(blocks))

    @classmethod
    def _median_line_height(cls, blocks: List[Block], default: float, good_only: bool = False) -> float:
        lines = cls._good_lines(blocks) if good_only else \
            [ln for b in blocks for p in b.paragraphs for ln in p.lines if ln.words]
        hs = [ln.height for ln in lines]
        return float(median(hs)) if hs else default

    # ------------------------------------------------------------------ hình vẽ / ảnh
    @staticmethod
    def _usable_text_layer(li: LoadedImage):
        tl = li.pdf_text
        if tl is None:
            return None
        if tl.n_chars < 15 and not tl.image_boxes:
            return None
        text = " ".join(p.text for p in tl.paragraphs)
        if not text.strip():
            return tl  # trang chỉ có ảnh
        q = text_quality(text)
        if q < 0.55:
            log.info("trang %d: lớp chữ PDF kém (%.2f) -> OCR", li.page_index + 1, q)
            return None
        return tl

    def _find_figures(self, gray: np.ndarray, blocks: List[Block], tables: List[Table], lh: float,
                      extra_boxes: Sequence[BBox] = (), color: Optional[np.ndarray] = None, scale: float = 1.0):
        h, w = gray.shape[:2]
        lines = [ln for b in blocks for p in b.paragraphs for ln in p.lines if ln.words]
        if lines:
            content_w = max(ln.bbox[2] for ln in lines) - min(ln.bbox[0] for ln in lines)
        else:
            content_w = w

        fig_boxes: List[BBox] = list(extra_boxes)
        if color is not None:
            fig_boxes += self._drop_noise(gray, blocks, lh)
            lines = [ln for b in blocks for p in b.paragraphs for ln in p.lines if ln.words]
        real_tables: List[Table] = []
        mask = grid_line_mask(gray, lh) if tables else None
        diagrams = self._diagram_tables(tables, content_w)
        for t in tables:
            cell_texts = [c.text for c in t.cells]
            if id(t) in diagrams:
                log.info("bảng %s là ô của sơ đồ -> hình", t.bbox)
                fig_boxes.append(t.bbox)
                continue
            grid_n = max(1, t.n_rows * t.n_cols)
            if not t.from_text_layer and t.n_rows * t.n_cols >= 6 and \
                    (len(t.cells) < 0.45 * grid_n or (t.n_rows >= 3 and t.n_cols >= 3 and self._has_empty_edge(t))):
                # lưới bị gộp quá nhiều: khung bao + nét vẽ bên trong, không phải bảng
                log.info("bảng %s bị loại (gộp ô %d/%d) – xử lý như khung/hình", t.bbox, len(t.cells), grid_n)
                self._restore_table_words(blocks, t)
                continue
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
                near_fig = any(self._gap(pb, fb) < 2 * lh for fb in fig_boxes)
                n_tok = len(p.text.split())
                # nhãn ngắn nằm sát mép trong vùng hình (chú thích của sơ đồ)
                label = n_tok <= 3 and len(p.lines) <= 2 and any(
                    self._gap(pb, fb) < 0.8 * lh and pb[1] >= fb[1] - lh and pb[3] <= fb[3] + lh for fb in fig_boxes)
                if (q < 0.4 and p.conf < 75) or (near_fig and q < 0.55 and n_tok <= 6) or label:
                    small = (pb[2] - pb[0]) < 2 * lh and (pb[3] - pb[1]) < 2 * lh
                    if not small or near_fig:
                        fig_boxes.append(pb)
        # hộp minh hoạ: khung viền/nền xám có sơ đồ hoặc chữ rác bên trong -> giữ nguyên cả hộp làm hình
        if color is not None:
            area = lambda b: max(1, (b[2] - b[0]) * (b[3] - b[1]))
            shaded = detect_shaded_panels(color, lh, scale=scale)
            for pb in detect_frames(gray, lh) + shaded:
                inside = [p for b in blocks for p in b.paragraphs
                          if not p.is_empty() and self._inter(p.bbox, pb) > 0.5 * area(p.bbox)]
                bad = [p for p in inside if text_quality(p.text, p.conf) < 0.5]
                garbage = len(bad) >= 2 and sum(len(p.text) for p in bad) >= 0.1 * max(1, sum(len(p.text) for p in inside))
                # chữ xếp nhiều cột trong hộp (ô công thức, nhãn sơ đồ) -> Word không dựng lại được bố cục
                multicol = [ln.bbox for p in inside for ln in p.lines if len(ln.words) >= 2 and
                            max(b.left - a.right for a, b in zip(ln.words, ln.words[1:])) > 1.5 * lh]
                drawing = pb in shaded and (
                    any(self._inter(t.bbox, pb) > 0.5 * area(t.bbox) for t in tables) or
                    any(self._inter(fb, pb) > 0.5 * area(fb) and area(fb) > 0.05 * area(pb) for fb in fig_boxes)
                    or len(multicol) >= 3)
                if garbage or drawing:
                    if drawing:
                        # đoạn văn thân bài nằm dưới phần sơ đồ giữ là chữ: thu hộp lại
                        draw = [t.bbox for t in tables if self._inter(t.bbox, pb) > 0.5 * area(t.bbox)] + \
                            [fb for fb in fig_boxes if self._inter(fb, pb) > 0.5 * area(fb)] + \
                            (multicol if len(multicol) >= 3 else [])
                        db = max(b[3] for b in draw)
                        dt = min(b[1] for b in draw)
                        # tiêu đề / đoạn văn nằm trên phần sơ đồ (chữ liền mạch, không chia cột) giữ là chữ
                        heads = [p.bbox for p in inside if p.bbox[3] < dt and
                                 text_quality(p.text, p.conf) >= 0.8 and p.conf >= 70 and
                                 not any(ln.bbox in multicol for ln in p.lines) and
                                 all(max([b.left - a.right for a, b in zip(ln.words, ln.words[1:])] or [0]) < 1.5 * lh
                                     for ln in p.lines)]
                        others_above = [p.bbox for p in inside if p.bbox[3] < dt and p.bbox not in heads]
                        if heads and not any(o[1] < max(h[3] for h in heads) for o in others_above):
                            pb = (pb[0], int(max(h[3] for h in heads) + 0.3 * lh), pb[2], pb[3])
                        body = [p.bbox for p in inside if len(p.lines) >= 3 and
                                (p.bbox[2] - p.bbox[0]) >= 0.6 * content_w and text_quality(p.text, p.conf) >= 0.8]
                        below = [b[1] for b in body if b[1] > db]
                        pb = (pb[0], pb[1], pb[2], int(min(below) - 0.3 * lh) if below else pb[3])
                        # viền phải/dưới của hộp thường nhạt, bị đứt -> mở rộng tới nội dung nằm cùng dải dọc
                        from .document import union_bbox as _ub
                        near = [p.bbox for b in blocks for p in b.paragraphs if not p.is_empty() and
                                p.bbox[1] >= pb[1] - lh and p.bbox[3] <= pb[3] + lh and
                                self._gap(p.bbox, pb) < 0.25 * content_w and len(p.text.split()) <= 25]
                        near += [fb for fb in fig_boxes if self._inter(fb, pb) > 0]
                        pb = _ub([pb] + near)
                    log.info("hộp minh hoạ %s -> hình", pb)
                    fig_boxes.append(pb)
        if not fig_boxes:
            return real_tables, []
        fig_boxes = merge_regions(fig_boxes, gap=1.5 * lh)
        # loại vùng hình quá nhỏ (nhiễu)
        fig_boxes = [fb for fb in fig_boxes if (fb[2] - fb[0]) >= 2 * lh and (fb[3] - fb[1]) >= 1.5 * lh]
        # hình chiếm phần lớn trang (bìa sách, ảnh toàn trang) mà chữ còn lại ít/kém -> giữ cả trang làm ảnh
        fig_area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in fig_boxes)
        if fig_area > 0.45 * w * h:
            rest = [ln for ln in lines if not any(fb[0] <= (ln.bbox[0] + ln.bbox[2]) / 2 <= fb[2] and
                                                fb[1] <= (ln.bbox[1] + ln.bbox[3]) / 2 <= fb[3] for fb in fig_boxes)]
            good = [ln for ln in rest if text_quality(ln.text, ln.conf) >= 0.6 and ln.conf >= 70]
            if sum(len(ln.text) for ln in good) < 150:
                from .document import union_bbox as _ub
                fig_boxes = [_ub(fig_boxes + [ln.bbox for ln in rest])] if rest else [_ub(fig_boxes)]
        # chú thích đặt cạnh hình (Word không đặt được chữ song song với ảnh, sẽ đẩy xuống dưới và tràn trang)
        if color is not None and fig_boxes:
            from .document import union_bbox as _ub
            for _ in range(2):
                grown = list(fig_boxes)
                for b in blocks:
                    for p in b.paragraphs:
                        if p.is_empty() or len(p.lines) > 4 or len(p.text.split()) > 25 or \
                                any(CAPTION_RE.match(ln.text) for ln in p.lines):
                            continue
                        pb = p.bbox
                        if (pb[2] - pb[0]) > 0.45 * content_w:
                            continue
                        for k, fb in enumerate(grown):
                            beside = pb[1] >= fb[1] - 0.5 * lh and pb[3] <= fb[3] + 0.5 * lh and \
                                (pb[0] >= fb[2] - lh or pb[2] <= fb[0] + lh) and self._gap(pb, fb) < 3 * lh
                            if beside:
                                grown[k] = _ub([fb, pb])
                                break
                if grown == fig_boxes:
                    break
                fig_boxes = merge_regions(grown, gap=1.5 * lh)
        # hàng nhãn của biểu đồ ngay sát hình (chữ cách nhau xa, thẳng cột với hình): thuộc hình
        if color is not None and fig_boxes:
            from statistics import median as _med
            for _ in range(3):
                changed = False
                for b in blocks:
                    for p in b.paragraphs:
                        rows = [ln for ln in p.lines if len(ln.words) >= 2]
                        if p.is_empty() or not rows or len(rows) < 0.5 * len(p.lines):
                            continue
                        spread = [_med([w2.left - w1.right for w1, w2 in zip(ln.words, ln.words[1:])]) for ln in rows]
                        if sum(g >= 0.8 * lh for g in spread) < 0.5 * len(rows):
                            continue
                        pb = p.bbox
                        for k, fb in enumerate(fig_boxes):
                            # hình có thể bị cắt thiếu cột cuối: cho hàng nhãn thò ra bên phải tới 25%
                            span = 0.05 * (fb[2] - fb[0])
                            if pb[0] >= fb[0] - span and pb[2] <= fb[2] + 0.25 * (fb[2] - fb[0]) \
                                    and self._gap(pb, fb) < lh \
                                    and not (fb[0] <= pb[0] and fb[1] <= pb[1] and pb[2] <= fb[2] and pb[3] <= fb[3]):
                                fig_boxes[k] = union_bbox([fb, pb])
                                changed = True
                                break
                if not changed:
                    break
        # bảng thật nằm trong hình -> thuộc hình
        kept = []
        for t in real_tables:
            ga = max(1, (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]))
            if any(self._inter(t.bbox, fb) > 0.5 * ga for fb in fig_boxes):
                continue
            kept.append(t)
        return kept, sorted(fig_boxes, key=lambda b: (b[1], b[0]))

    def _diagram_tables(self, tables: List[Table], content_w: int) -> set:
        """Các "bảng" thực ra là ô vẽ của sơ đồ: chồng lên nhau, nhiều bảng nhỏ ít ô trên cùng trang,
        hoặc ô vuông nhỏ gần như trống (biểu tượng)."""
        scan = [t for t in tables if not t.from_text_layer]
        out = set()
        for t in scan:
            ta = max(1, (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]))
            for o in scan:
                if o is not t and self._inter(t.bbox, o.bbox) > 0.1 * ta:
                    out.add(id(t))
        small = [t for t in scan if (t.bbox[2] - t.bbox[0]) < 0.5 * content_w and len(t.cells) <= 8]
        if len(small) >= 2:
            out.update(id(t) for t in small)
        for t in scan:
            empty = sum(1 for c in t.cells if not c.text.strip())
            if len(t.cells) <= 4 and empty >= 0.5 * len(t.cells) and (t.bbox[2] - t.bbox[0]) < 0.5 * content_w:
                out.add(id(t))
        return out

    @staticmethod
    def _drop_lone_glyphs(blocks: List[Block], lh: float, h: int) -> List[Block]:
        """Một hai ký tự đứng riêng giữa trang, cao/thấp bất thường hoặc tin cậy thấp: mũi tên, chấm, vết bẩn."""
        for b in blocks:
            keep = []
            for p in b.paragraphs:
                t = p.text.strip()
                if 0 < len(t) <= 2 and len(p.lines) == 1 and 0.1 * h < p.bbox[1] and p.bbox[3] < 0.9 * h:
                    ph = p.bbox[3] - p.bbox[1]
                    if ph > 1.3 * lh or ph < 0.4 * lh or p.conf < 80:
                        continue
                keep.append(p)
            b.paragraphs = keep
        return [b for b in blocks if b.paragraphs]

    @staticmethod
    def _stray_word(wd) -> bool:
        t = wd.text
        if wd.conf < 70 and t and all(c in ":;|!'`~¦" for c in t):
            return True
        return wd.conf < 40 and (len(t) <= 2 or not any(c.isalnum() for c in t))

    @staticmethod
    def _noise_line(ln) -> bool:
        if not ln.words:
            return False
        text = ln.text
        chars = [c for c in text if not c.isspace()]
        alnum = sum(c.isalnum() for c in chars)
        return ln.conf < 60 and (text_quality(text, ln.conf) < 0.5 or 1 - alnum / max(1, len(chars)) > 0.3)

    def _drop_noise(self, gray: np.ndarray, blocks: List[Block], lh: float) -> List[BBox]:
        """Chữ "ma" do lốm đốm nền xám / bóng viền khung / chữ đọc trùng hai lần.
        Đoạn tin cậy thấp và giống rác: có nét vẽ thật bên dưới -> trả về làm vùng hình, không thì bỏ."""
        h, w = gray.shape[:2]
        bw = binarize_ink(gray)
        figs: List[BBox] = []
        allp = [p for b in blocks for p in b.paragraphs if not p.is_empty()]
        # vệt viền khung đọc thành ký tự ở đầu/cuối dòng ("TÌ PBR...", ": : *Lợi...")
        for p in allp:
            for ln in p.lines:
                while len(ln.words) >= 3 and self._stray_word(ln.words[0]):
                    ln.words = ln.words[1:]
                while len(ln.words) >= 3 and self._stray_word(ln.words[-1]):
                    ln.words = ln.words[:-1]
        # dòng rác ở đầu/cuối một đoạn tốt (lốm đốm nền dính vào đoạn)
        for p in allp:
            while len(p.lines) >= 2 and self._noise_line(p.lines[0]):
                p.lines = p.lines[1:]
            while len(p.lines) >= 2 and self._noise_line(p.lines[-1]):
                p.lines = p.lines[:-1]
        drop = set()
        for p in allp:
            text = p.text
            if CAPTION_RE.match(text):
                continue
            pw, ph = p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]
            if (len(p.lines) >= 2 and pw < 1.5 * lh and max(len(ln.text) for ln in p.lines) <= 6) or \
                    (pw < 0.5 * lh and ph > 2 * lh):
                drop.add(id(p))  # dải "chữ" hẹp nhiều dòng (SR / ty / a): vệt nền xám hoặc viền hộp
                continue
            if len(text.strip()) <= 2 and len(p.lines) == 1 and p.conf < 80 and \
                    0.1 * h < p.bbox[1] and p.bbox[3] < 0.9 * h:
                drop.add(id(p))  # ký tự lẻ giữa trang: mũi tên, dấu chấm đọc thành "1", "4"
                continue
            if p.conf >= 65:
                continue
            chars = [c for c in text if not c.isspace()]
            alnum = sum(c.isalnum() for c in chars)
            sym = 1 - alnum / max(1, len(chars))
            q = text_quality(text, p.conf)
            if len(chars) > 80 or not (q < 0.7 or sym > 0.25 or alnum <= 3):
                continue
            drop.add(id(p))
            x0, y0, x1, y1 = p.bbox
            n, _, st, _ = cv2.connectedComponentsWithStats(bw[y0:y1, x0:x1], 8)
            if n > 1 and (st[1:, 2].max() > 2.5 * lh or st[1:, 3].max() > 2.5 * lh) \
                    and lh * 3 < y0 and y1 < h - lh * 3:
                figs.append(p.bbox)
        # đoạn đọc trùng (chữ có bóng/đậm): nằm gần trọn trong đoạn khác tin cậy hơn
        for p in allp:
            if id(p) in drop:
                continue
            pa = max(1, (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1]))
            tiny = sum(c.isalnum() for c in p.text) <= 4 and len(p.lines) == 1
            for o in allp:
                if o is p or id(o) in drop or (o.conf < p.conf and not tiny):
                    continue
                oa = (o.bbox[2] - o.bbox[0]) * (o.bbox[3] - o.bbox[1])
                if oa <= pa:
                    continue
                overlap = self._inter(p.bbox, o.bbox) / pa
                if (tiny and overlap >= 0.6) or (overlap >= 0.75 and difflib.SequenceMatcher(
                        None, p.text, o.text).ratio() * (len(p.text) + len(o.text)) >= 1.5 * len(p.text)):
                    drop.add(id(p))
                    break
        if drop:
            log.info("bỏ %d đoạn chữ nhiễu/đọc trùng", len(drop))
            for b in blocks:
                b.paragraphs = [p for p in b.paragraphs if id(p) not in drop]
        return figs

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
        para_boxes = [p.bbox for b in blocks for p in b.paragraphs if not p.is_empty()]
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
                bb = blk.bbox
                ba = max(1, (bb[2] - bb[0]) * (bb[3] - bb[1]))
                tiny = sum(c.isalnum() for c in blk.text) <= 4
                if any(self._inter(bb, pb) >= 0.6 * ba for pb in para_boxes):
                    continue  # đọc trùng dòng đã có
                if tiny and any(self._gap(bb, fb) < lh for fb in fig_boxes):
                    continue  # mảnh nhãn của hình
                chars = [c for c in blk.text if not c.isspace()]
                if (bb[2] - bb[0]) < 1.5 * lh and len(blk.text.splitlines()) >= 2:
                    continue  # dải chữ hẹp nhiều dòng: vệt nền / viền, không phải chữ
                if len(chars) <= 2 and 0.1 * h < bb[1] and bb[3] < 0.9 * h:
                    continue  # ký tự lẻ giữa trang
                if sum(not c.isalnum() for c in chars) > 0.3 * len(chars):
                    continue  # chủ yếu ký hiệu: vết bẩn, mũi tên
                if blk.conf >= 60 and text_quality(blk.text, blk.conf) >= 0.5:
                    log.info("đọc bổ sung dòng bị bỏ sót: %r", blk.text[:60])
                    blk.lang = self.engine.combined_lang
                    found.append(blk)
        return found

    @staticmethod
    def _has_empty_edge(t: Table) -> bool:
        """Cột/hàng mép toàn ô trống (chỉ trừ các ô trải hết bề ngang/dọc) = lề trong một khung, không phải bảng."""
        for col in (0, t.n_cols - 1):
            cs = [c for c in t.cells if c.col <= col < c.col + c.col_span and c.col_span < t.n_cols]
            if cs and all(not any(ch.isalnum() for ch in c.text) for c in cs):
                return True
        for row in (0, t.n_rows - 1):
            cs = [c for c in t.cells if c.row <= row < c.row + c.row_span and c.row_span < t.n_rows]
            if cs and all(not any(ch.isalnum() for ch in c.text) for c in cs):
                return True
        return False

    @staticmethod
    def _restore_table_words(blocks: List[Block], t: Table) -> None:
        """Trả chữ của 'bảng' bị loại về thành đoạn văn, giữ nguyên toạ độ từ đã OCR trong ô."""
        for c in sorted(t.cells, key=lambda c: (c.bbox[1], c.bbox[0])):
            if not c.text.strip():
                continue
            words = sorted(c.words, key=lambda w: (w.cy, w.left))
            lines: List[Line] = []
            for wd in words:
                if lines and abs(wd.cy - lines[-1].words[-1].cy) < 0.6 * max(wd.height, lines[-1].words[-1].height):
                    lines[-1].words.append(wd)
                else:
                    lines.append(Line([wd]))
            for ln in lines:
                ln.words.sort(key=lambda w: w.left)
            if len(c.text.strip()) <= 2 and len(lines) <= 1:
                continue  # mảnh nhiễu trong lề khung
            if lines:
                # thay chữ theo kết quả OCR lại của ô (chính xác hơn), giữ toạ độ dòng
                texts = [x for x in c.lines if x.strip()]
                if len(texts) == len(lines):
                    for ln, txt in zip(lines, texts):
                        b = ln.bbox
                        ln.words = [Word(txt, b[0], b[1], b[2] - b[0], b[3] - b[1], ln.conf)]
                blocks.append(Block([Paragraph(lines=lines)]))

    @staticmethod
    def _trim_captions(blocks: List[Block], boxes: List[BBox]) -> List[BBox]:
        """Dòng 'Hình 2.1. ...' nằm ở mép trên/dưới vùng hình: cắt vùng hình để chú thích vẫn là chữ."""
        caps = [ln.bbox for b in blocks for p in b.paragraphs for ln in p.lines
                if ln.words and CAPTION_RE.match(ln.text)]
        out = []
        for bx in boxes:
            l, t, r, btm = bx
            for cb in caps:
                if cb[2] <= l or cb[0] >= r or cb[3] <= t or cb[1] >= btm:
                    continue
                cy = (cb[1] + cb[3]) / 2
                if cy > t + 0.65 * (btm - t):
                    btm = min(btm, cb[1] - 2)
                elif cy < t + 0.35 * (btm - t):
                    t = max(t, cb[3] + 2)
            if btm - t > 10:
                out.append((l, t, r, btm))
        return out

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
                before = sum(len(ln.words) for ln in p.lines)
                for ln in p.lines:
                    ln.words = [wd for wd in ln.words
                                if not any(b[0] <= wd.cx <= b[2] and b[1] <= wd.cy <= b[3] for b in boxes)]
                p.lines = [ln for ln in p.lines if ln.words]
                after = sum(len(ln.words) for ln in p.lines)
                if after and after <= 3 and after <= 0.3 * before:
                    p.lines = []  # mẩu thừa của đoạn đã nằm gần hết trong hình (";  lại" ở mép hình)
            blk.paragraphs = [p for p in blk.paragraphs if p.lines]
        return [b for b in blocks if b.paragraphs]

    # ------------------------------------------------------------------ bảng
    def _extract_tables(self, gray: np.ndarray, blocks: List[Block], grids: List[TableGrid],
                        from_text_layer: bool = False):
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
            # ô ngắn / ô cả trang không đọc ra chữ (số thứ tự, số lượng...): mô hình tiếng Việt đọc "8" thành "ö",
            # "13" thành "15". Bảng không có tiếng Nga dùng mô hình tiếng Anh (đọc số chính xác nhất); chữ có dấu
            # vẫn được đọc lại bằng các mô hình người dùng chọn.
            short_lang = _table_languages(words, self.engine.langs, self.engine.combined_lang)[1]
            tbl = Table(bbox=g.bbox, row_edges=list(g.row_edges), col_edges=list(g.col_edges),
                        from_text_layer=from_text_layer)
            self.progress(f"Đọc bảng {gi + 1}/{len(grids)} ({g.n_rows}x{g.n_cols}) ...")
            for gc in g.cells:
                cell = TableCell(gc.row, gc.col, gc.row_span, gc.col_span, gc.bbox)
                cell_words = [w for w in words if inside(w, gc.bbox, pad=0)]
                fallback_lines = self._words_to_lines(cell_words)
                lines: List[str] = []
                align_words = cell_words
                re_words: List[Word] = []
                if self.opts.reocr_cells and not from_text_layer:
                    cell_text = _script_evidence(cell_words)  # rác đường kẻ ("СОНИ", "т") không quyết định ngôn ngữ
                    cell_lang = choose_lang(cell_text, self.engine.langs) if len(cell_text) >= 3 else short_lang
                    lines, re_words = self._ocr_cell(gray, gc.bbox, cell_lang)
                    if len(cell_text) < 3 and set(cell_lang.split("+")) < set(self.engine.langs) and \
                            any(ch.isalpha() for ch in " ".join(lines)):
                        # ô có chữ mà đọc cả trang không ra (ảnh chỉ có bảng): đọc thêm bằng các mô hình người dùng
                        # chọn, giữ bản có dấu tiếng Việt / chữ Nga ("Dat" -> "Đạt", "Ha" -> "Да")
                        alt_lines, alt_words = self._ocr_cell(gray, gc.bbox, self.engine.combined_lang)
                        if _prefer_reading(lines, re_words, alt_lines, alt_words):
                            lines, re_words = alt_lines, alt_words
                    if re_words:
                        align_words = re_words
                # chữ đọc cả trang dài hơn hẳn chữ đọc lại ô: ô đọc lại bị sót chữ -> dùng chữ cả trang,
                # trừ khi phần "dài hơn" chỉ là rác do đọc dính đường kẻ ("СОНИ" thay cho "8"): rác có độ
                # tin cậy thấp hơn hẳn chữ đọc lại
                if not lines or (fallback_lines and len(" ".join(lines)) < 0.5 * len(" ".join(fallback_lines))
                                 and _mean_conf(cell_words) >= _mean_conf(re_words) - 10
                                 and text_quality(" ".join(fallback_lines)) >= text_quality(" ".join(lines))):
                    lines = fallback_lines
                    align_words = cell_words
                cell.lines = lines
                cell.alignment = self._cell_alignment(align_words, gc.bbox)
                tbl.word_boxes.extend(wd.bbox for wd in align_words)
                cell.words = list(align_words)
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
        crop = _peel_ruling(gray[t:b, l:r].copy())
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
        if paras and all(p.lang == "pdf" for p in paras):
            paras.sort(key=lambda p: (p.top, p.bbox[0]))  # lớp chữ PDF không theo thứ tự đọc
        elements: List[Element] = []
        pending = sorted(list(tables) + list(figures), key=lambda t: t.top)
        for p in paras:
            while pending and pending[0].top < p.top + (p.median_line_height * 0.5):
                elements.append(pending.pop(0))
            elements.append(p)
        elements.extend(pending)
        return elements


def _ink_text_height(gray: np.ndarray) -> Optional[float]:
    """Chiều cao dòng chữ ước lượng từ các vết mực cỡ ký tự – dùng khi Tesseract không đọc được dòng nào tin cậy."""
    bw = binarize_ink(gray)
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    if n < 2:
        return None
    hh, ww = stats[1:, cv2.CC_STAT_HEIGHT], stats[1:, cv2.CC_STAT_WIDTH]
    ok = (hh >= 6) & (hh <= 0.1 * gray.shape[0]) & (ww <= 3 * hh)
    if ok.sum() < 10:
        return None
    return float(np.median(hh[ok])) * 1.3


def _script_evidence(words: Sequence[Word], min_conf: float = 70.0) -> str:
    """Chữ đủ tin cậy để đoán ngôn ngữ: từ có ít nhất 2 chữ cái, đọc với độ tin cậy cao. Đọc dính đường kẻ bảng
    hay ra ký tự Cyrillic lẻ ("Е im i т Е" với tessdata_best) – nếu dùng để chọn mô hình thì cả cột "Bu lông"
    bị đọc bằng mô hình tiếng Nga thành "Ви 16ng"."""
    return " ".join(w.text for w in words if w.conf >= min_conf and sum(ch.isalpha() for ch in w.text) >= 2)


def _table_languages(words: Sequence[Word], langs: Sequence[str], combined: str) -> Tuple[str, str]:
    """(ngôn ngữ của bảng, ngôn ngữ đọc ô ngắn / ô không có chữ đọc tin cậy)."""
    evidence = _script_evidence(words)
    table_lang = choose_lang(evidence, list(langs)) if evidence else combined
    has_rus = bool(evidence) and "rus" in table_lang.split("+")
    short_lang = "eng" if "eng" in langs and not has_rus else table_lang
    return table_lang, short_lang


def _prefer_reading(cur_lines: List[str], cur_words: Sequence[Word], alt_lines: List[str],
                    alt_words: Sequence[Word]) -> bool:
    if not alt_lines:
        return False
    pa, pb = script_profile(" ".join(cur_lines)), script_profile(" ".join(alt_lines))
    sa, sb = pa["viet"] + pa["cyr"], pb["viet"] + pb["cyr"]
    ca, cb = _mean_conf(cur_words), _mean_conf(alt_words)
    return (sb > sa and cb >= ca - 10) or (sb == sa and cb > ca + 5)


def _mean_conf(words: Sequence[Word]) -> float:
    return sum(w.conf for w in words) / len(words) if words else 0.0


def _peel_ruling(crop: np.ndarray) -> np.ndarray:
    """Xoá phần đường kẻ ô còn sót ở mép vùng cắt (kẻ dày hoặc lệch vài px so với mép ô tìm được).
    Một hàng/cột điểm ảnh gần mép mà tối gần hết bề rộng/chiều cao ô là đường kẻ, không phải chữ;
    để lại thì Tesseract đọc "8" thành "Bo", "13" thành "TM"."""
    h, w = crop.shape[:2]
    if h < 4 or w < 4:
        return crop
    dark = crop < 128
    band = max(3, int(0.12 * min(h, w)))
    rows = dark.mean(axis=1)
    cols = dark.mean(axis=0)
    top = [y for y in range(min(band, h)) if rows[y] > 0.6]
    bottom = [y for y in range(max(0, h - band), h) if rows[y] > 0.6]
    left = [x for x in range(min(band, w)) if cols[x] > 0.6]
    right = [x for x in range(max(0, w - band), w) if cols[x] > 0.6]
    if top:
        crop[:max(top) + 1] = 255
    if bottom:
        crop[min(bottom):] = 255
    if left:
        crop[:, :max(left) + 1] = 255
    if right:
        crop[:, min(right):] = 255
    return crop


def _kill_pool(ex) -> None:
    try:
        procs = list(getattr(ex, "_processes", {}).values())
    except Exception:  # noqa: BLE001
        procs = []
    for proc in procs:
        try:
            if proc.is_alive():
                _kill_tree(proc.pid)
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    for proc in procs:
        try:
            proc.join(timeout=3)  # chờ tiến trình thật sự thoát (Windows: nhả file tạm) trước khi dọn thư mục tạm
        except Exception:  # noqa: BLE001
            pass


def _kill_tree(pid: int) -> None:
    """Diệt các tiến trình con (tesseract) của một tiến trình OCR song song."""
    import signal
    import subprocess

    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10,
                       stdin=subprocess.DEVNULL, creationflags=0x08000000)
        return
    proc_dir = Path("/proc")
    if not proc_dir.is_dir():
        return
    for p in proc_dir.iterdir():
        if not p.name.isdigit():
            continue
        try:
            ppid = int((p / "stat").read_text().rsplit(")", 1)[1].split()[1])
            if ppid == pid:
                os.kill(int(p.name), signal.SIGKILL)
        except (OSError, ValueError, IndexError):
            continue


def _looks_misoriented(blocks: List[Block]) -> bool:
    """Trang ngược / nằm ngang: Tesseract vẫn "đọc" ra chữ nhưng độ tin cậy và tỉ lệ từ hợp lệ thấp
    (trang thường: tin cậy ~90+, ~95% từ hợp lệ; trang xoay 90/180 độ: ~60, ~60%)."""
    words = [w for b in blocks for p in b.paragraphs for ln in p.lines for w in ln.words if w.text.strip()]
    if len(words) < 8:
        return False
    mean_conf = sum(max(0.0, w.conf) for w in words) / len(words)
    q = text_quality(" ".join(w.text for w in words))
    return mean_conf < 80 and q < 0.8


_WORKER_PIPE: Optional["Pipeline"] = None


def _new_run_tmpdir() -> Optional[str]:
    """Thư mục tạm riêng cho một lượt OCR song song. Tiến trình con bị diệt khi bấm Dừng / đóng cửa sổ không kịp
    xoá ảnh tạm của pytesseract (tess_*_input.PNG cả trang, *.tsv): xoá cả thư mục sau lượt chạy và dọn các
    thư mục còn sót của lần trước (file đang bị tesseract.exe mồ côi giữ trên Windows)."""
    import shutil
    import tempfile
    import time

    root = Path(tempfile.gettempdir()) / "ocr2word_runs"
    try:
        root.mkdir(exist_ok=True)
        for old in root.iterdir():
            try:
                if time.time() - old.stat().st_mtime > 3600:
                    shutil.rmtree(old, ignore_errors=True)
            except OSError:
                pass
        return tempfile.mkdtemp(prefix="run_", dir=str(root))
    except OSError:
        return None


def _worker_init(opts: Options, tmpdir: Optional[str] = None, stop_evt=None) -> None:
    global _WORKER_PIPE
    if tmpdir and os.path.isdir(tmpdir):
        import tempfile

        tempfile.tempdir = tmpdir  # ảnh tạm của pytesseract nằm trong thư mục của lượt chạy
    if stop_evt is not None:
        import dataclasses

        opts = dataclasses.replace(opts, cancel=stop_evt.is_set)
    _WORKER_PIPE = Pipeline(opts)


def _worker_page(path: str, index: int) -> Page:
    from .preprocess import render_pdf_page
    assert _WORKER_PIPE is not None
    li = render_pdf_page(path, index, _WORKER_PIPE.opts.pdf_dpi, _WORKER_PIPE.opts.use_pdf_text)
    return _WORKER_PIPE.process_image_safe(li)


def _norm_key(text: str) -> str:
    import unicodedata as _ud
    t = _ud.normalize("NFD", text.lower())
    t = "".join(c for c in t if c.isalpha() and _ud.category(c) != "Mn")
    return t


def _edge_zone(pg: Page, b: BBox, frac: float = 0.10) -> Optional[str]:
    if b[3] < frac * pg.height_px:
        return "top"
    if b[1] > (1 - frac) * pg.height_px:
        return "bottom"
    return None


_NUM_RE = re.compile(r"(?<![\d.,])(\d{1,4})(?![\d.,%])")


def _extract_page_numbers(pages: List[Page]) -> Optional[int]:
    """Số trang in ở mép trang ("| 101", "4 BIZBOOKS [59", "170 PHẦN II: ...") nhận ra nhờ chạy đều
    theo thứ tự trang (số = chỉ số trang + hằng số). Bỏ khỏi thân trang, đưa vào header/footer."""
    cands = []  # (page, paragraph, line, word, number, zone)
    for i, pg in enumerate(pages):
        for el in pg.elements:
            if not isinstance(el, Paragraph) or el.is_empty() or el.lang == "pdf" or len(el.lines) > 2:
                continue
            zone = _edge_zone(pg, el.bbox)
            if zone is None:
                continue
            for ln in (el.lines[0], el.lines[-1]):
                for wd in ln.words:
                    for m in _NUM_RE.finditer(wd.text):
                        cands.append((i, el, ln, wd, int(m.group(1)), zone))
    if not cands:
        return None
    offsets = Counter(n - i for i, _, _, _, n, _ in cands)
    off, cnt = offsets.most_common(1)[0]
    pages_with = len({c[0] for c in cands})
    if cnt < max(3, 0.3 * pages_with):
        return None
    found: Dict[int, str] = {}
    for i, el, ln, wd, n, zone in cands:
        if n - i != off or i in found:
            continue
        pg = pages[i]
        found[i] = str(n)
        if zone == "top" and not pg.header_text:
            pg.header_text = str(n)
        elif zone == "bottom" and not pg.footer_text:
            pg.footer_text = str(n)
        # bỏ số trang và ký tự rác ngay sát nó ("| 101", "101 ]"); không đụng tới chữ khác của dòng
        # (trước đây mọi từ dạng "I:" đều bị coi là rác -> "PHẦN I: ..." mất "I:")
        k = next(j for j, w in enumerate(ln.words) if w is wd)
        drop = {k}
        for j in (k - 1, k + 1):
            if 0 <= j < len(ln.words):
                t = ln.words[j].text
                if re.fullmatch(r"[|\[\]!¦—–\-_.:;'`]+", t) or (t in ("I", "l") and len(ln.words) <= 2):
                    drop.add(j)
        ln.words = [w for j, w in enumerate(ln.words) if j not in drop]
        rest = " ".join(w.text for w in ln.words)
        if sum(c.isalpha() for c in rest) < 8 or len(rest.split()) <= 2:
            pg.elements = [e for e in pg.elements if e is not el]
    return off


def strip_running_headers(pages: List[Page]) -> None:
    """Tiêu đề chạy / chân trang lặp lại trên nhiều trang (tên sách, tên phần, hoa văn) -> bỏ khỏi thân
    trang, đưa vào header của Word (cùng số trang). Mảnh chữ rác ở mép trang cũng bị loại."""
    import difflib

    if len(pages) < 4:
        return
    page_off = _extract_page_numbers(pages)
    cands = []  # (page_idx, element, key, zone)
    for i, pg in enumerate(pages):
        for el in pg.elements:
            if not isinstance(el, Paragraph) or el.is_empty() or el.lang == "pdf":
                continue
            zone = _edge_zone(pg, el.bbox)
            if zone is None or len(el.lines) > 3 or len(el.text) > 90:
                continue
            cands.append((i, el, _norm_key(el.text), zone))
    # sách chia phần: mỗi tiêu đề phần chỉ lặp trên một đoạn sách -> ngưỡng không quá 6 trang
    need = max(3, min(int(0.15 * len(pages)), 6))
    remove: Dict[int, int] = {}  # id(el) -> page
    clusters: Dict[int, List] = {}
    for i, el, key, zone in cands:
        if len(key) < 4:
            continue
        hits = {i: el}
        for j, el2, key2, zone2 in cands:
            if j in hits or zone2 != zone or len(key2) < 4:
                continue
            if difflib.SequenceMatcher(None, key, key2).ratio() >= 0.6:
                hits[j] = el2
        if len(hits) >= need:
            remove[id(el)] = i
            clusters[id(el)] = list(hits.values())
    titles = {_norm_key(_canonical_header(members[0], members)) for members in clusters.values()} - {""}
    for i, pg in enumerate(pages):
        keep = []
        bands = [el.bbox for el in pg.elements if id(el) in remove]
        top_band = max((b[3] for b in bands if b[3] < 0.10 * pg.height_px), default=None)
        for el in pg.elements:
            if id(el) not in remove and isinstance(el, Paragraph) and len(el.lines) <= 2 and (any(
                    min(el.bbox[3], b[3]) - max(el.bbox[1], b[1]) > 0.5 * (el.bbox[3] - el.bbox[1]) for b in bands)
                    or (top_band is not None and el.bbox[3] <= top_band)):
                continue  # mảnh khác của cùng dòng tiêu đề chạy (OCR tách đôi) hoặc nằm phía trên nó
            if id(el) in remove:
                if el.bbox[1] < 0.5 * pg.height_px and not (pg.header_text and not pg.header_text.isdigit()):
                    title = _canonical_header(el, clusters[id(el)])
                    if title:
                        num = pg.header_text if pg.header_text.isdigit() else \
                            (str(i + page_off) if page_off is not None and not pg.footer_text else "")
                        pg.header_text = f"{num} | {title}" if num else title
                continue
            if isinstance(el, Paragraph) and el.lang != "pdf" and not el.is_empty():
                b = el.bbox
                edge = b[3] < 0.08 * pg.height_px or b[1] > 0.92 * pg.height_px
                if edge and len(el.text) < 40 and text_quality(el.text, el.conf) < 0.4:
                    continue  # mảnh rác ở mép trang (hoa văn, viền)
            keep.append(el)
        if titles:
            # dòng tiêu đề chạy OCR quá nát (lẫn hoa văn, dính 2–4 dòng) nên không vào cụm: vẫn chứa phần lớn
            # chữ của một tiêu đề chạy đã biết -> bỏ
            kept2 = []
            for el in keep:
                if isinstance(el, Paragraph) and el.lang != "pdf" and el.bbox[3] < 0.10 * pg.height_px \
                        and len(el.lines) <= 5 and _covers_title(_norm_key(el.text), titles):
                    continue
                kept2.append(el)
            keep = kept2
        if len(keep) != len(pg.elements):
            log.info("trang %d: bỏ %d dòng tiêu đề chạy / rác ở mép", i + 1, len(pg.elements) - len(keep))
            pg.elements = keep
            from .layout import _compute_margins
            _compute_margins(pg)


def _covers_title(key: str, titles) -> bool:
    """Phần lớn (theo bộ ba ký tự, không kể thứ tự) chữ của một tiêu đề chạy đã biết có trong `key`."""
    grams = {key[k:k + 3] for k in range(len(key) - 2)}
    for t in titles:
        tg = {t[k:k + 3] for k in range(len(t) - 2)}
        if len(tg) >= 8 and len(tg & grams) >= 0.55 * len(tg):
            return True
    return False


def _canonical_header(el: Paragraph, members: List[Paragraph]) -> str:
    """Bản đọc tốt nhất của một tiêu đề chạy: OCR mỗi trang sai một kiểu, nên bỏ phiếu từng từ giữa các
    bản có cùng số từ (nhóm đông nhất), rồi mới sửa chính tả."""
    clean = lambda t: re.sub(r"^[^\w(]+|[^\w?)!.]+$", "", " ".join(t.split()))  # noqa: E731
    texts = [clean(m.text) for m in members] or [clean(el.text)]
    by_len: Dict[int, List[List[str]]] = {}
    for t in texts:
        by_len.setdefault(len(t.split()), []).append(t.split())
    biggest = max(len(g) for g in by_len.values())
    # nhóm đủ đông; ưu tiên bản nhiều từ hơn (OCR hay đánh rơi "I:" chứ hiếm khi thêm từ)
    group = max((g for g in by_len.values() if len(g) >= 0.5 * biggest), key=lambda g: (len(g[0]), len(g)))
    if len(group) >= 3:
        best = " ".join(Counter(col).most_common(1)[0][0] for col in zip(*group))
    else:
        best = max(texts, key=lambda t: (texts.count(t), text_quality(t)))
    return best if text_quality(best) >= 0.5 else ""


def process_files(paths: Sequence[str], opts: Options) -> List[Page]:
    pipe = Pipeline(opts)
    pages: List[Page] = []
    for p in paths:
        pages.extend(pipe.process_file(p))
    return pages

"""Phát hiện bảng có đường kẻ bằng OpenCV và xây lưới ô (kể cả ô gộp)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .document import BBox

log = logging.getLogger(__name__)


@dataclass
class GridCell:
    row: int
    col: int
    row_span: int
    col_span: int
    bbox: BBox


@dataclass
class TableGrid:
    bbox: BBox
    row_edges: List[int]
    col_edges: List[int]
    cells: List[GridCell] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return len(self.row_edges) - 1

    @property
    def n_cols(self) -> int:
        return len(self.col_edges) - 1


def _cluster_positions(idx: np.ndarray, gap: int) -> List[int]:
    """Gom các chỉ số liên tiếp (cách nhau <= gap) thành một vị trí trung bình."""
    if idx.size == 0:
        return []
    groups: List[List[int]] = [[int(idx[0])]]
    for v in idx[1:]:
        v = int(v)
        if v - groups[-1][-1] <= gap:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [int(round(sum(g) / len(g))) for g in groups]


def _line_masks(gray: np.ndarray, min_h_len: int, min_v_len: int) -> Tuple[np.ndarray, np.ndarray]:
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (min_h_len, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_v_len))
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, h_kernel)
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, v_kernel)
    # nối các đoạn đứt
    horiz = cv2.dilate(horiz, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)))
    vert = cv2.dilate(vert, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5)))
    return horiz, vert


def detect_tables(gray: np.ndarray, text_height: float = 30.0) -> List[TableGrid]:
    """Tìm các bảng có kẻ khung. `text_height` là chiều cao dòng chữ trung bình (px)
    để đặt ngưỡng độ dài đường kẻ."""
    h, w = gray.shape[:2]
    min_h_len = int(max(text_height * 2.5, w * 0.03, 25))
    min_v_len = int(max(text_height * 1.2, h * 0.015, 25))
    horiz, vert = _line_masks(gray, min_h_len, min_v_len)
    mask = cv2.bitwise_or(horiz, vert)
    # nối các đường kẻ thành khối liên thông (khép các khe nhỏ tại giao điểm)
    joined = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    tables: List[TableGrid] = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw < min_h_len * 1.5 or ch < min_v_len * 1.5:
            continue
        grid = _build_grid(horiz, vert, (x, y, x + cw, y + ch), text_height)
        if grid is None:
            continue
        if grid.n_rows * grid.n_cols < 2:
            continue  # chỉ là khung bao quanh một đoạn văn
        tables.append(grid)
    tables.sort(key=lambda t: (t.bbox[1], t.bbox[0]))
    log.debug("detected %d table(s)", len(tables))
    return tables


def _build_grid(horiz: np.ndarray, vert: np.ndarray, bbox: BBox, text_height: float) -> Optional[TableGrid]:
    l, t, r, b = bbox
    hb = horiz[t:b, l:r]
    vb = vert[t:b, l:r]
    bw_, bh_ = r - l, b - t
    # vị trí các đường ngang: hàng có nhiều điểm ảnh kẻ
    row_cov = (hb > 0).sum(axis=1)
    col_cov = (vb > 0).sum(axis=0)
    row_idx = np.where(row_cov >= 0.25 * bw_)[0]
    col_idx = np.where(col_cov >= 0.25 * bh_)[0]
    gap = max(3, int(text_height * 0.4))
    ys = _cluster_positions(row_idx, gap)
    xs = _cluster_positions(col_idx, gap)
    if len(ys) < 2 or len(xs) < 2:
        return None
    # loại các đường quá sát nhau (kẻ đôi)
    ys = _dedupe(ys, max(6, int(text_height * 0.6)))
    xs = _dedupe(xs, max(6, int(text_height * 0.6)))
    if len(ys) < 2 or len(xs) < 2:
        return None
    # khung ngoài phải tồn tại ở cả 2 hướng: kiểm tra chiều rộng/độ cao bao phủ
    if (xs[-1] - xs[0]) < 0.5 * bw_ or (ys[-1] - ys[0]) < 0.5 * bh_:
        return None
    ys_abs = [y + t for y in ys]
    xs_abs = [x + l for x in xs]
    n_rows, n_cols = len(ys) - 1, len(xs) - 1

    tol = max(3, int(text_height * 0.15))
    # v_sep[r][c]: có đường dọc giữa cột c-1 và c tại hàng r (c = 1..n_cols-1)
    v_sep = np.ones((n_rows, n_cols + 1), dtype=bool)
    for c in range(1, n_cols):
        x = xs[c]
        band = vb[:, max(0, x - tol):min(bw_, x + tol + 1)].max(axis=1) > 0
        for rr in range(n_rows):
            y0, y1 = ys[rr] + tol, ys[rr + 1] - tol
            if y1 <= y0:
                v_sep[rr, c] = True
                continue
            v_sep[rr, c] = band[y0:y1].mean() >= 0.5
    h_sep = np.ones((n_rows + 1, n_cols), dtype=bool)
    for rr in range(1, n_rows):
        y = ys[rr]
        band = hb[max(0, y - tol):min(bh_, y + tol + 1), :].max(axis=0) > 0
        for c in range(n_cols):
            x0, x1 = xs[c] + tol, xs[c + 1] - tol
            if x1 <= x0:
                h_sep[rr, c] = True
                continue
            h_sep[rr, c] = band[x0:x1].mean() >= 0.5

    cells = _merge_cells(n_rows, n_cols, v_sep, h_sep, xs_abs, ys_abs)
    return TableGrid(bbox=(xs_abs[0], ys_abs[0], xs_abs[-1], ys_abs[-1]),
                     row_edges=ys_abs, col_edges=xs_abs, cells=cells)


def _dedupe(vals: List[int], min_gap: int) -> List[int]:
    out: List[int] = []
    for v in vals:
        if out and v - out[-1] < min_gap:
            out[-1] = (out[-1] + v) // 2
        else:
            out.append(v)
    return out


def _merge_cells(n_rows: int, n_cols: int, v_sep: np.ndarray, h_sep: np.ndarray,
                 xs: List[int], ys: List[int]) -> List[GridCell]:
    """Gộp các ô kề nhau không có đường kẻ ngăn cách thành ô lớn hình chữ nhật."""
    taken = np.zeros((n_rows, n_cols), dtype=bool)
    cells: List[GridCell] = []
    for r in range(n_rows):
        for c in range(n_cols):
            if taken[r, c]:
                continue
            # mở rộng sang phải
            cs = 1
            while c + cs < n_cols and not taken[r, c + cs] and not v_sep[r, c + cs]:
                cs += 1
            # mở rộng xuống dưới: toàn bộ biên ngang giữa hàng r+rs-1 và r+rs phải mở,
            # và các biên dọc trong hàng mới phải giống hàng đầu
            rs = 1
            while r + rs < n_rows:
                if any(h_sep[r + rs, c:c + cs]):
                    break
                if any(taken[r + rs, c:c + cs]):
                    break
                if any(v_sep[r + rs, c + 1:c + cs]):
                    break
                # biên ngoài trái/phải của hàng mới cũng phải là đường kẻ (hoặc mép bảng)
                left_ok = (c == 0) or v_sep[r + rs, c]
                right_ok = (c + cs == n_cols) or v_sep[r + rs, c + cs]
                if not (left_ok and right_ok):
                    break
                rs += 1
            taken[r:r + rs, c:c + cs] = True
            cells.append(GridCell(r, c, rs, cs, (xs[c], ys[r], xs[c + cs], ys[r + rs])))
    return cells

"""Mô hình dữ liệu trung gian giữa bước OCR và bước xuất Word."""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import List, Optional, Tuple, Union

BBox = Tuple[int, int, int, int]  # left, top, right, bottom


def union_bbox(boxes: List[BBox]) -> BBox:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


@dataclass
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float = 0.0

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def cx(self) -> float:
        return self.left + self.width / 2

    @property
    def cy(self) -> float:
        return self.top + self.height / 2

    @property
    def bbox(self) -> BBox:
        return (self.left, self.top, self.right, self.bottom)

    def shifted(self, dx: int, dy: int) -> "Word":
        return Word(self.text, self.left + dx, self.top + dy, self.width, self.height, self.conf)


@dataclass
class Line:
    words: List[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words).strip()

    @property
    def bbox(self) -> BBox:
        return union_bbox([w.bbox for w in self.words])

    @property
    def height(self) -> int:
        b = self.bbox
        return b[3] - b[1]

    @property
    def conf(self) -> float:
        confs = [w.conf for w in self.words if w.conf >= 0]
        return sum(confs) / len(confs) if confs else 0.0

    def is_empty(self) -> bool:
        return not self.text


@dataclass
class Paragraph:
    lines: List[Line] = field(default_factory=list)
    block_bbox: Optional[BBox] = None  # khối chứa (Tesseract block) dùng làm mốc canh lề
    # Thuộc tính định dạng, do layout.py tính ra
    alignment: str = "left"  # left | center | right | justify
    font_size_pt: float = 12.0
    bold: bool = False
    left_indent_in: float = 0.0
    first_line_indent_in: float = 0.0
    space_after_pt: float = 6.0
    line_spacing: float = 1.0
    lang: str = ""

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines if not ln.is_empty())

    @property
    def bbox(self) -> BBox:
        return union_bbox([ln.bbox for ln in self.lines if ln.words])

    @property
    def top(self) -> int:
        return self.bbox[1]

    @property
    def bottom(self) -> int:
        return self.bbox[3]

    @property
    def conf(self) -> float:
        confs = [ln.conf for ln in self.lines if ln.words]
        return sum(confs) / len(confs) if confs else 0.0

    @property
    def median_line_height(self) -> float:
        hs = [ln.height for ln in self.lines if ln.words]
        return float(median(hs)) if hs else 0.0

    def is_empty(self) -> bool:
        return not any(not ln.is_empty() for ln in self.lines)


@dataclass
class TableCell:
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    bbox: BBox = (0, 0, 0, 0)
    lines: List[str] = field(default_factory=list)
    alignment: str = "left"
    bold: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


@dataclass
class Table:
    bbox: BBox
    row_edges: List[int]  # toạ độ y của các đường kẻ ngang (n_rows + 1)
    col_edges: List[int]  # toạ độ x của các đường kẻ dọc (n_cols + 1)
    cells: List[TableCell] = field(default_factory=list)
    font_size_pt: float = 11.0
    space_after_pt: float = 6.0

    @property
    def n_rows(self) -> int:
        return len(self.row_edges) - 1

    @property
    def n_cols(self) -> int:
        return len(self.col_edges) - 1

    @property
    def top(self) -> int:
        return self.bbox[1]

    @property
    def bottom(self) -> int:
        return self.bbox[3]


Element = Union[Paragraph, Table]


@dataclass
class Page:
    width_px: int
    height_px: int
    dpi: float  # dpi hiệu dụng: px / inch trên khổ giấy xuất ra
    page_width_in: float
    page_height_in: float
    margin_left_in: float = 1.0
    margin_right_in: float = 1.0
    margin_top_in: float = 1.0
    margin_bottom_in: float = 1.0
    elements: List[Element] = field(default_factory=list)
    source: str = ""
    page_index: int = 0

    @property
    def landscape(self) -> bool:
        return self.page_width_in > self.page_height_in

    def px_to_in(self, px: float) -> float:
        return px / self.dpi

    def px_to_pt(self, px: float) -> float:
        return px * 72.0 / self.dpi

    @property
    def text(self) -> str:
        parts = []
        for el in self.elements:
            if isinstance(el, Paragraph):
                parts.append(el.text)
            else:
                for r in range(el.n_rows):
                    row_cells = [c for c in el.cells if c.row == r]
                    row_cells.sort(key=lambda c: c.col)
                    parts.append(" | ".join(c.text.replace("\n", " ") for c in row_cells))
        return "\n".join(parts)

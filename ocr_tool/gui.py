"""Giao diện đồ hoạ (Tkinter) cho Windows 10/11."""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from . import __version__
from .cli import describe_error
from .config import app_dir, find_tesseract, list_languages, resolve_tessdata
from .export_docx import check_writable_dir, export_docx, remember_output, unique_output, write_text
from .lang import LANG_MAP, LANG_NAMES
from .pipeline import Cancelled, Options, Pipeline
from .preprocess import IMAGE_EXTS, PDF_EXTS

FILETYPES = [
    ("Ảnh và PDF", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.gif *.pdf"),
    ("Ảnh", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.gif"),
    ("PDF", "*.pdf"),
    ("Tất cả", "*.*"),
]


class App(tk.Tk):
    def __init__(self, files: Optional[List[str]] = None) -> None:
        super().__init__()
        self.title(f"OCR2Word {__version__} - Ảnh → Word (Việt / Anh / Nga)")
        self.geometry("860x640")
        self.minsize(760, 560)
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_outputs: List[str] = []
        self._file_index = 0
        self._set_icon()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        self._detect_tesseract()
        for f in files or []:
            p = Path(f)
            if p.is_dir():
                self._add_dir(p)
            elif p.is_file():
                self._add_path(str(p))

    def _set_icon(self) -> None:
        base = Path(getattr(sys, "_MEIPASS", app_dir()))
        for cand in (base / "assets" / "icon.ico", app_dir() / "assets" / "icon.ico"):
            if cand.is_file():
                try:
                    if os.name == "nt":
                        self.iconbitmap(str(cand))
                    else:
                        self.iconphoto(True, tk.PhotoImage(file=str(cand.with_suffix(".png"))))
                except tk.TclError:
                    pass
                break

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        # Danh sách file
        lf = ttk.LabelFrame(self, text="1. File đầu vào (ảnh / PDF)")
        lf.pack(fill="both", expand=True, **pad)
        self.listbox = tk.Listbox(lf, selectmode="extended", height=8)
        self.listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(lf, command=self.listbox.yview)
        sb.pack(side="left", fill="y", pady=6)
        self.listbox.config(yscrollcommand=sb.set)
        btns = ttk.Frame(lf)
        btns.pack(side="left", fill="y", padx=6, pady=6)
        ttk.Button(btns, text="Thêm file...", command=self._add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="Thêm thư mục...", command=self._add_folder).pack(fill="x", pady=2)
        ttk.Button(btns, text="Xoá đã chọn", command=self._remove_selected).pack(fill="x", pady=2)
        ttk.Button(btns, text="Xoá hết", command=lambda: self.listbox.delete(0, "end")).pack(fill="x", pady=2)

        # Tuỳ chọn
        of = ttk.LabelFrame(self, text="2. Tuỳ chọn")
        of.pack(fill="x", **pad)
        row1 = ttk.Frame(of)
        row1.pack(fill="x", padx=6, pady=3)
        ttk.Label(row1, text="Ngôn ngữ:").pack(side="left")
        self.lang_vars = {}
        for code in ("vi", "en", "ru"):
            v = tk.BooleanVar(value=True)
            self.lang_vars[code] = v
            ttk.Checkbutton(row1, text=LANG_NAMES[code], variable=v).pack(side="left", padx=6)
        ttk.Label(row1, text="   Font Word:").pack(side="left")
        self.font_var = tk.StringVar(value="Times New Roman")
        ttk.Combobox(row1, textvariable=self.font_var, width=20,
                     values=["Times New Roman", "Arial", "Calibri", "Cambria", "Tahoma"]).pack(side="left", padx=4)

        row2 = ttk.Frame(of)
        row2.pack(fill="x", padx=6, pady=3)
        self.tables_var = tk.BooleanVar(value=True)
        self.deskew_var = tk.BooleanVar(value=True)
        self.denoise_var = tk.BooleanVar(value=False)
        self.refine_var = tk.BooleanVar(value=True)
        self.keep_lines_var = tk.BooleanVar(value=False)
        self.merge_var = tk.BooleanVar(value=False)
        self.txt_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Nhận dạng bảng", variable=self.tables_var).pack(side="left", padx=4)
        ttk.Checkbutton(row2, text="Khử nghiêng", variable=self.deskew_var).pack(side="left", padx=4)
        ttk.Checkbutton(row2, text="Khử nhiễu", variable=self.denoise_var).pack(side="left", padx=4)
        ttk.Checkbutton(row2, text="Tinh chỉnh theo ngôn ngữ (chính xác hơn)", variable=self.refine_var).pack(side="left", padx=4)
        row3 = ttk.Frame(of)
        row3.pack(fill="x", padx=6, pady=3)
        ttk.Checkbutton(row3, text="Giữ ngắt dòng như ảnh", variable=self.keep_lines_var).pack(side="left", padx=4)
        ttk.Checkbutton(row3, text="Gộp tất cả thành 1 file Word", variable=self.merge_var).pack(side="left", padx=4)
        ttk.Checkbutton(row3, text="Xuất thêm .txt", variable=self.txt_var).pack(side="left", padx=4)

        row3b = ttk.Frame(of)
        row3b.pack(fill="x", padx=6, pady=3)
        self.figures_var = tk.BooleanVar(value=True)
        self.spell_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3b, text="Nhận diện hình vẽ/ảnh (giữ nguyên ảnh)",
                        variable=self.figures_var).pack(side="left", padx=4)
        ttk.Checkbutton(row3b, text="Sửa chính tả tiếng Việt", variable=self.spell_var).pack(side="left", padx=4)
        self.pdftext_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3b, text="PDF có sẵn chữ: đọc trực tiếp (TCVN3/Unicode)",
                        variable=self.pdftext_var).pack(side="left", padx=4)
        ttk.Button(row3b, text="Từ điển của tôi...", command=self._open_user_dict).pack(side="left", padx=8)

        row4 = ttk.Frame(of)
        row4.pack(fill="x", padx=6, pady=3)
        ttk.Label(row4, text="Thư mục kết quả:").pack(side="left")
        self.out_var = tk.StringVar(value="")
        ttk.Entry(row4, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row4, text="Chọn...", command=self._choose_out).pack(side="left")
        ttk.Label(row4, text="(trống = cạnh file gốc)").pack(side="left", padx=4)

        row5 = ttk.Frame(of)
        row5.pack(fill="x", padx=6, pady=3)
        ttk.Label(row5, text="Tesseract:").pack(side="left")
        self.tess_var = tk.StringVar(value="")
        ttk.Entry(row5, textvariable=self.tess_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row5, text="Chọn...", command=self._choose_tesseract).pack(side="left")
        ttk.Button(row5, text="Kiểm tra", command=self._detect_tesseract).pack(side="left", padx=4)

        # Chạy
        rf = ttk.LabelFrame(self, text="3. Thực hiện")
        rf.pack(fill="both", expand=True, **pad)
        ctl = ttk.Frame(rf)
        ctl.pack(fill="x", padx=6, pady=4)
        self.start_btn = ttk.Button(ctl, text="▶  Bắt đầu chuyển đổi", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(ctl, text="Dừng", command=self._request_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(ctl, text="Mở thư mục kết quả", command=self._open_output, state="disabled")
        self.open_btn.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(ctl, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=6)
        self.log = tk.Text(rf, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=6, pady=(0, 6))

    # ------------------------------------------------------------------ handlers
    def _request_stop(self) -> None:
        self._stop.set()
        self.stop_btn.config(state="disabled")
        self._log("Đang dừng ...")

    def _add_files(self) -> None:
        for f in filedialog.askopenfilenames(title="Chọn ảnh hoặc PDF", filetypes=FILETYPES):
            self._add_path(f)

    def _add_folder(self) -> None:
        d = filedialog.askdirectory(title="Chọn thư mục chứa ảnh")
        if d:
            self._add_dir(Path(d))

    def _add_dir(self, d: Path) -> None:
        try:
            children = sorted(d.iterdir())
        except OSError as e:
            messagebox.showerror("Lỗi", f"Không đọc được thư mục:\n{d}\n{e}")
            return
        for child in children:
            if child.is_file() and child.suffix.lower() in IMAGE_EXTS | PDF_EXTS:
                self._add_path(str(child))

    def _add_path(self, f: str) -> None:
        if f not in self.listbox.get(0, "end"):
            self.listbox.insert("end", f)

    def _remove_selected(self) -> None:
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)

    def _choose_out(self) -> None:
        d = filedialog.askdirectory(title="Chọn thư mục kết quả")
        if d:
            self.out_var.set(d)

    def _choose_tesseract(self) -> None:
        f = filedialog.askopenfilename(title="Chọn tesseract.exe",
                                       filetypes=[("tesseract", "tesseract.exe tesseract"), ("Tất cả", "*.*")])
        if f:
            self.tess_var.set(f)
            self._detect_tesseract()

    def _detect_tesseract(self) -> None:
        cmd = self.tess_var.get().strip() or find_tesseract()
        if not cmd or not Path(cmd).is_file():
            self._log("⚠ Không tìm thấy Tesseract. Hãy cài Tesseract-OCR (xem README) hoặc chọn đường dẫn tesseract.exe.")
            return
        self.tess_var.set(cmd)
        td = resolve_tessdata(cmd, None, [LANG_MAP[c] for c in ("vi", "en", "ru")])
        langs = list_languages(cmd, str(td) if td else None)
        missing = [LANG_MAP[c] for c in ("vi", "en", "ru") if LANG_MAP[c] not in langs]
        self._log(f"Tesseract: {cmd}")
        self._log(f"Ngôn ngữ đã cài: {', '.join(langs) if langs else '(không đọc được)'}")
        if missing:
            self._log(f"⚠ Thiếu dữ liệu ngôn ngữ: {', '.join(missing)} - chạy setup_windows.bat để tải.")

    def _open_user_dict(self) -> None:
        from .spellfix import user_dict_path

        path = user_dict_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(
                    "# Từ điển của bạn cho OCR2Word (UTF-8). Mỗi dòng một mục:\n"
                    "#   sai => đúng     : thay thế cố định sau OCR (ví dụ: tr- ớc => trước)\n"
                    "#   từ / cụm từ     : thuật ngữ cần giữ nguyên và dùng làm ngữ cảnh (ví dụ: ống khuếch tán)\n",
                    encoding="utf-8")
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self._log(f"Từ điển: {path} (lưu file rồi chạy lại để áp dụng)")
            import ocr_tool.spellfix as _sf
            _sf._LEXICON = None  # nạp lại ở lần chạy sau
        except OSError as e:
            messagebox.showerror("Lỗi", str(e))

    def _log(self, msg: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _open_output(self) -> None:
        if not self._last_outputs:
            return
        folder = str(Path(self._last_outputs[-1]).parent)
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError as e:
            messagebox.showerror("Lỗi", str(e))

    # ------------------------------------------------------------------ chạy nền
    def _start(self) -> None:
        files = list(self.listbox.get(0, "end"))
        if not files:
            messagebox.showwarning("Thiếu file", "Hãy thêm ít nhất một ảnh hoặc PDF.")
            return
        langs = [c for c, v in self.lang_vars.items() if v.get()]
        if not langs:
            messagebox.showwarning("Thiếu ngôn ngữ", "Chọn ít nhất một ngôn ngữ.")
            return
        cmd = self.tess_var.get().strip() or find_tesseract()
        if not cmd or not Path(cmd).is_file():
            messagebox.showerror("Thiếu Tesseract", "Không tìm thấy Tesseract. Cài đặt rồi thử lại.")
            return
        out_dir = self.out_var.get().strip() or None
        # nơi lưu kết quả phải ghi được (thư mục chỉ đọc, ổ CD, thư mục hệ thống...): báo ngay, không để OCR
        # xong cả lô mới biết không lưu được
        dests = {Path(out_dir)} if out_dir else {Path(f).parent for f in files}
        for d in sorted(dests, key=str):
            err = check_writable_dir(d)
            if err:
                messagebox.showerror("Thư mục kết quả", err)
                return
        opts = Options(
            langs=langs, tesseract_cmd=cmd,
            deskew=self.deskew_var.get(), denoise=self.denoise_var.get(),
            detect_tables=self.tables_var.get(), refine_blocks=self.refine_var.get(),
            refine_lines=self.refine_var.get(),
            detect_figures=self.figures_var.get(), spellfix=self.spell_var.get(),
            use_pdf_text=self.pdftext_var.get(),
            progress=lambda m: self._queue.put(("log", "   " + m)),
            page_progress=lambda done, total: self._queue.put(("page", (done, total))),
            cancel=self._stop.is_set,
        )
        self._stop.clear()
        self._last_outputs = []
        self._file_index = 0
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.open_btn.config(state="disabled")
        self.progress.config(maximum=len(files) * 100, value=0)
        self._worker = threading.Thread(
            target=self._work, args=(files, opts, out_dir, self.font_var.get(), self.keep_lines_var.get(),
                                     self.merge_var.get(), self.txt_var.get()), daemon=True)
        self._worker.start()

    def _work(self, files, opts, out_dir, font, keep_lines, merge, txt) -> None:
        failed = []
        used: set = set()
        try:
            pipe = Pipeline(opts)
            all_pages = []
            for i, f in enumerate(files):
                if self._stop.is_set():
                    raise Cancelled()
                self._queue.put(("file", i))
                self._queue.put(("log", f"Đang xử lý: {f}"))
                try:
                    pages = pipe.process_file(f)
                    if merge:
                        all_pages.extend(pages)
                    else:
                        planned = Path(out_dir) / (Path(f).stem + ".docx") if out_dir else Path(f).with_suffix(".docx")
                        target = unique_output(planned, used)  # a\scan.png + b\scan.png: không ghi đè nhau
                        written = export_docx(pages, str(target), font_name=font, keep_line_breaks=keep_lines)
                        remember_output(written, used)
                        if Path(written) != target:
                            self._queue.put(("log", f"   ! {target.name} đang mở/bị khoá – lưu thành {Path(written).name}"))
                        if txt:
                            write_text(pages, str(Path(written).with_suffix(".txt")))
                        self._queue.put(("done_file", written))
                except Exception as e:  # noqa: BLE001 - một file lỗi không làm dừng các file còn lại
                    logging.exception("Lỗi xử lý %s", f)
                    failed.append((Path(f).name, describe_error(e)))
                    self._queue.put(("log", f"✖ Lỗi {Path(f).name}: {describe_error(e)}"))
                self._queue.put(("progress", (i + 1) * 100))
            if merge and all_pages:
                first = Path(files[0])
                target = (Path(out_dir) if out_dir else first.parent) / (first.stem + "_merged.docx")
                written = export_docx(all_pages, str(target), font_name=font, keep_line_breaks=keep_lines)
                if txt:
                    write_text(all_pages, str(Path(written).with_suffix(".txt")))
                self._queue.put(("done_file", written))
            self._queue.put(("finished", failed))
        except Cancelled:
            self._queue.put(("cancelled", None))
        except Exception as e:  # noqa: BLE001
            logging.exception("Lỗi xử lý")
            self._queue.put(("error", describe_error(e)))

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "file":
                    self._file_index = payload
                    self.progress.config(value=payload * 100)
                elif kind == "page":
                    done, total = payload
                    if total:
                        self.progress.config(value=self._file_index * 100 + int(100 * done / total))
                elif kind == "progress":
                    self.progress.config(value=payload)
                elif kind == "done_file":
                    self._last_outputs.append(payload)
                    self._log(f"✔ Đã tạo: {payload}")
                elif kind == "finished":
                    failed = payload or []
                    if failed:
                        self._log(f"Hoàn tất – {len(failed)} file lỗi.")
                        messagebox.showwarning(
                            "Có file lỗi",
                            "Các file sau không chuyển được:\n\n" + "\n".join(f"• {n}: {m}" for n, m in failed[:10]))
                    else:
                        self._log("Hoàn tất.")
                    self._reset_buttons()
                elif kind == "cancelled":
                    self._log("Đã dừng theo yêu cầu.")
                    self._reset_buttons()
                elif kind == "error":
                    self._log(f"✖ Lỗi: {payload}")
                    messagebox.showerror("Lỗi", payload)
                    self._reset_buttons()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _reset_buttons(self) -> None:
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.open_btn.config(state="normal" if self._last_outputs else "disabled")

    def _on_close(self) -> None:
        """Đóng cửa sổ khi đang chạy: hỏi lại, dừng xử lý (kể cả các tiến trình OCR song song) rồi mới thoát,
        tránh để chương trình chạy ngầm sau khi cửa sổ đã đóng."""
        if self._worker is not None and self._worker.is_alive():
            if not messagebox.askyesno("Đang chuyển đổi", "Đang chuyển đổi. Dừng lại và thoát?"):
                return
            self._stop.set()
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="disabled")
            self._log("Đang dừng ...")
            deadline = time.time() + 30

            def wait_worker() -> None:
                if self._worker is not None and self._worker.is_alive() and time.time() < deadline:
                    self.after(200, wait_worker)
                else:
                    self.destroy()

            wait_worker()
            return
        self.destroy()


def _log_file() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OCR2Word"
    else:
        base = Path.home() / ".ocr2word"
    base.mkdir(parents=True, exist_ok=True)
    return base / "ocr2word.log"


def run_gui(files: Optional[List[str]] = None) -> None:
    handlers: list = []
    try:
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(_log_file(), maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:  # bản .exe không console: sys.stderr = None
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("PIL").setLevel(logging.WARNING)
    app = App(files)
    app.mainloop()


if __name__ == "__main__":  # pragma: no cover
    run_gui()

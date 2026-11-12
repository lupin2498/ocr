"""Điểm vào: không có tham số -> mở giao diện đồ hoạ; có tham số -> chạy dòng lệnh."""
import sys


def main() -> int:
    args = sys.argv[1:]
    # OCR2Word.exe (không có cửa sổ console, sys.stdout = None) nhận file khi người dùng kéo-thả ảnh vào biểu
    # tượng hoặc chọn "Open with": mở giao diện với các file đó thay vì chạy ngầm bản dòng lệnh không ai thấy
    if args and sys.stdout is not None:
        from .cli import main as cli_main
        return cli_main()
    from .gui import run_gui
    run_gui(args)
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())

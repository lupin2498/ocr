"""Điểm vào: không có tham số -> mở giao diện đồ hoạ; có tham số -> chạy dòng lệnh."""
import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import main as cli_main
        return cli_main()
    from .gui import run_gui
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())

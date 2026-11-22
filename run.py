"""EAP GUI 启动入口：python run.py"""
import sys

from PySide6.QtWidgets import QApplication

from gui.app import create_window


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("EAP Pipeline GUI")
    win = create_window()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

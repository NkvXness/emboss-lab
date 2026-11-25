import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("EMBOSS LAB")
    app.setStyle("Fusion")
    app.setStyleSheet(Path(__file__).with_name("theme.qss").read_text(encoding="utf-8"))
    window = MainWindow()
    window.show()
    return app.exec()

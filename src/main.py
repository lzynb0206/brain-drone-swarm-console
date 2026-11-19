"""Application bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from src import APP_NAME, __version__
from src.app import CommandConsoleWindow


def _load_stylesheet(app: QApplication) -> None:
    stylesheet_path = Path(__file__).parent / "resources" / "styles.qss"
    app.setStyleSheet(stylesheet_path.read_text(encoding="utf-8"))


def _set_ui_font(app: QApplication) -> None:
    preferred_fonts = (
        "PingFang SC",
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "Arial",
    )
    available = set(QFontDatabase.families())
    family = next((name for name in preferred_fonts if name in available), "Arial")
    app.setFont(QFont(family, 10))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Brain Control Lab")

    _set_ui_font(app)
    _load_stylesheet(app)

    window = CommandConsoleWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

_APP_NAME = "TwitterDownloader"


def ensure_app_metadata(app: QtWidgets.QApplication) -> None:
    if QtCore.QSysInfo.productType() == "windows":
        try:
            QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
                QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
        except AttributeError:  # pragma: no cover - older PySide6 versions
            pass
    app.setApplicationName(_APP_NAME)
    app.setOrganizationName(_APP_NAME)


def icon_from_theme(name: str) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(64, 64)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    background = QtGui.QColor("#1f2937")
    foreground = QtGui.QColor("#e5e7eb")
    painter.setBrush(background)
    painter.setPen(QtCore.Qt.NoPen)
    painter.drawRoundedRect(0, 0, 64, 64, 12, 12)
    painter.setPen(foreground)
    font = QtGui.QFont("Segoe UI", 22, QtGui.QFont.Bold)
    painter.setFont(font)
    text = (name or "D")[:1].upper() or "D"
    painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, text)
    painter.end()
    return QtGui.QIcon(pixmap)


def themed_stylesheet() -> str:
    return """
    QMainWindow {
        background-color: #0f1115;
    }
    QWidget {
        color: #e5e7eb;
        font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
        font-size: 14px;
    }
    QLineEdit, QTextEdit, QPlainTextEdit {
        background: #1f2937;
        border: 1px solid #374151;
        border-radius: 8px;
        padding: 8px 10px;
        selection-background-color: #2563eb;
    }
    QPushButton {
        background: #2563eb;
        border: none;
        border-radius: 8px;
        padding: 10px 16px;
        font-weight: 600;
    }
    QPushButton:hover {
        background: #1d4ed8;
    }
    QPushButton:disabled {
        background: #374151;
        color: #9ca3af;
    }
    QProgressBar {
        border: 1px solid #374151;
        border-radius: 6px;
        text-align: center;
        background: #1f2937;
        height: 14px;
    }
    QProgressBar::chunk {
        background: #2563eb;
        border-radius: 6px;
    }
    QListWidget {
        background: #1f2937;
        border: 1px solid #374151;
        border-radius: 8px;
    }
    QListWidget::item {
        padding: 8px;
        border-radius: 6px;
    }
    QListWidget::item:selected {
        background: #2563eb;
    }
    QFrame#card {
        background: #161b22;
        border: 1px solid #27272a;
        border-radius: 12px;
    }
    QLabel#caption {
        color: #9ca3af;
        font-size: 12px;
    }
    QLabel#status {
        color: #9ca3af;
        font-size: 12px;
    }
    """

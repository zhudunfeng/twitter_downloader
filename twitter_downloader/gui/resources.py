from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

_APP_NAME = "TwitterDownloader"


def ensure_app_metadata(app: QtWidgets.QApplication) -> None:
    """设置跨入口一致的应用名称和高分屏行为。"""
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
    """生成无需网络或外部资源的本地应用图标。"""
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


def navigation_icon(name: str) -> QtGui.QIcon:
    """绘制深色背景上清晰一致的导航线条图标。"""
    pixmap = QtGui.QPixmap(96, 96)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.scale(4, 4)
    pen = QtGui.QPen(QtGui.QColor("#c8ddeb"), 1.8)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    painter.setPen(pen)
    if name == "library":
        roof = QtGui.QPainterPath()
        roof.moveTo(3, 10)
        roof.lineTo(12, 3)
        roof.lineTo(21, 10)
        painter.drawPath(roof)
        outline = QtGui.QPainterPath()
        outline.moveTo(5, 9)
        outline.lineTo(5, 21)
        outline.lineTo(10, 21)
        outline.lineTo(10, 15)
        outline.lineTo(14, 15)
        outline.lineTo(14, 21)
        outline.lineTo(19, 21)
        outline.lineTo(19, 9)
        painter.drawPath(outline)
    elif name == "download":
        painter.drawLine(12, 3, 12, 15)
        painter.drawLine(7, 10, 12, 15)
        painter.drawLine(17, 10, 12, 15)
        tray = QtGui.QPainterPath()
        tray.moveTo(4, 16)
        tray.lineTo(4, 21)
        tray.lineTo(20, 21)
        tray.lineTo(20, 16)
        painter.drawPath(tray)
    else:
        for y, x in ((6, 9), (12, 16), (18, 7)):
            painter.drawLine(3, y, 21, y)
            painter.setBrush(QtGui.QColor("#0d1117"))
            painter.drawEllipse(QtCore.QPointF(x, y), 2.3, 2.3)
    painter.end()
    return QtGui.QIcon(pixmap)


def themed_stylesheet() -> str:
    """返回与深色推文列表一致的控件及键盘焦点样式。"""
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
    QDialog, QMenu { background: #10151c; }
    QWidget { font-size: 14px; }
    QMainWindow { background: #0f1115; }
    QPushButton { background: #1d9bf0; color: #ffffff; padding: 10px 15px; border-radius: 18px; }
    QPushButton:hover { background: #1689d6; }
    QPushButton:focus, QToolButton:focus, QComboBox:focus, QDateEdit:focus, QLineEdit:focus, QTextEdit:focus, QListView:focus {
        border: 1px solid #70c3ff;
    }
    QLineEdit, QTextEdit, QPlainTextEdit {
        background: #17212b; border: 1px solid #2c3a47; selection-background-color: #17679a;
    }
    QFrame#navigation { background: #0d1117; border-right: 1px solid #29313b; }
    QLabel#brand { font-size: 21px; font-weight: 700; padding: 0 5px; }
    QPushButton#navItem { background: transparent; color: #c8d0db; text-align: left; padding: 13px 10px; border-radius: 22px; font-size: 16px; }
    QPushButton#navItem:hover { background: #19212b; }
    QPushButton#navItem:checked { color: #eff5fc; background: #172b3e; font-weight: 700; }
    QPushButton#secondary { background: #19232e; color: #d9e3ef; padding: 8px 13px; border: 1px solid #304151; border-radius: 16px; }
    QPushButton#secondary:hover { background: #26384a; }
    QFrame#timelineColumn { border-right: 1px solid #29313b; }
    QWidget#timelineHeader, QWidget#timelineTabs { background: #0f1115; }
    QWidget#timelineTabs { border-bottom: 1px solid #29313b; }
    QLabel#pageTitle { font-size: 24px; font-weight: 700; }
    QLabel#sectionTitle { font-size: 17px; font-weight: 650; }
    QLabel#emptyTitle { font-size: 26px; font-weight: 700; }
    QLabel#caption, QLabel#status { color: #95a5b6; font-size: 12px; }
    QLabel#warning { color: #e9b46e; padding: 8px; }
    QLabel#libraryFeedback { color: #9db1c5; font-size: 12px; padding: 9px 18px; background: #14202b; }
    QPushButton#feedTab { color: #98a8b9; background: transparent; border-radius: 0; border-bottom: 3px solid transparent; padding: 13px 6px; }
    QPushButton#feedTab:hover { background: #18212b; }
    QPushButton#feedTab:checked { color: #edf5fd; border-bottom: 3px solid #1d9bf0; }
    QPushButton#newWorks { color: #78c8ff; background: #112b3e; border-radius: 0; padding: 11px; }
    QFrame#filterRail { background: #101720; border-left: 1px solid #29313b; }
    QComboBox, QDateEdit { background: #17212b; border: 1px solid #304151; border-radius: 7px; padding: 8px; min-height: 19px; }
    QComboBox QAbstractItemView { background: #17212b; color: #e5e7eb; selection-background-color: #234864; }
    QCheckBox { spacing: 8px; }
    QFrame#videoPlayer { background: #000000; border: 1px solid #304151; }
    QWidget#playerControls { background: #101b26; }
    QWidget#playerControls QToolButton { background: transparent; color: #e5e7eb; padding: 5px; border-radius: 5px; }
    QWidget#playerControls QToolButton:hover { background: #304151; }
    QWidget#playerControls QComboBox { padding: 2px; min-height: 20px; border: none; }
    QLabel#playerMessage { background: #132333; color: #e6edf5; padding: 9px; }
    QSlider::groove:horizontal { background: #344556; height: 4px; border-radius: 2px; }
    QSlider::sub-page:horizontal { background: #1d9bf0; border-radius: 2px; }
    QSlider::handle:horizontal { width: 12px; margin: -4px 0; background: #e8f4ff; border-radius: 6px; }
    QScrollBar:vertical { width: 9px; background: #0f1115; margin: 0; }
    QScrollBar::handle:vertical { background: #344556; border-radius: 4px; min-height: 30px; }
    QScrollBar::handle:vertical:hover { background: #52708d; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QMenu { border: 1px solid #304151; padding: 5px; }
    QMenu::item { padding: 9px 24px; }
    QMenu::item:selected { background: #213548; }
    QStatusBar { background: #0d1117; border-top: 1px solid #29313b; padding: 3px 10px; }
    """

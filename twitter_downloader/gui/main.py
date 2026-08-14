from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from twitter_downloader.core import download_one
from twitter_downloader.utils import is_tweet_url

from .config import load_config, save_config
from .resources import ensure_app_metadata, icon_from_theme, themed_stylesheet


APP_NAME = "TwitterDownloader"


class Worker(QtCore.QThread):
    started_signal = QtCore.Signal(str)
    progress_signal = QtCore.Signal(float, str)
    log_signal = QtCore.Signal(str)
    finished_signal = QtCore.Signal(str)
    error_signal = QtCore.Signal(str)

    def __init__(
        self,
        url: str,
        output_dir: str,
        cookies: Optional[str],
        verbose: bool,
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.url = url
        self.output_dir = output_dir
        self.cookies = cookies
        self.verbose = verbose

    def run(self) -> None:
        try:
            download_one(
                self.url,
                self.output_dir,
                cookies=self.cookies,
                verbose=self.verbose,
                progress=self,
            )
        except Exception as exc:  # pragma: no cover - GUI error surface
            # The core callback already emitted the user-facing error.
            if not str(exc):
                self.error_signal.emit(exc.__class__.__name__)

    def on_start(self, url: str) -> None:
        self.started_signal.emit(url)

    def on_progress(self, percent: float, status: str) -> None:
        self.progress_signal.emit(percent, status)

    def on_log(self, message: str) -> None:
        self.log_signal.emit(message)

    def on_done(self, path: str) -> None:
        self.finished_signal.emit(path)

    def on_error(self, url: str, error: str) -> None:
        self.error_signal.emit(error)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        ensure_app_metadata(QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv))
        self.setWindowTitle(QtWidgets.QApplication.applicationName() or APP_NAME)
        self.setWindowIcon(icon_from_theme("D"))
        self._apply_geometry()
        self._build_ui()
        self._load_initial_values()
        self._apply_theme()
        self._worker: Optional[Worker] = None
        self._history: list[dict[str, str]] = []

    def _apply_geometry(self) -> None:
        cfg = load_config()
        geom = cfg.get("window_geometry") or {}
        width = int(geom.get("width") or 900)
        height = int(geom.get("height") or 520)
        self.resize(max(width, 640), max(height, 420))

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(16)

        card = QtWidgets.QFrame()
        card.setObjectName("card")
        form = QtWidgets.QFormLayout(card)
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignLeft)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.url_input = QtWidgets.QLineEdit()
        self.url_input.setPlaceholderText("https://x.com/<user>/status/<id>")
        form.addRow("Tweet URL", self.url_input)

        option_row = QtWidgets.QHBoxLayout()
        self.output_edit = QtWidgets.QLineEdit("downloads")
        self.output_button = QtWidgets.QPushButton("Browse")
        self.output_button.clicked.connect(self._choose_output_dir)
        option_row.addWidget(self.output_edit)
        option_row.addWidget(self.output_button)
        form.addRow("Output directory", option_row)

        cookie_row = QtWidgets.QHBoxLayout()
        self.cookie_edit = QtWidgets.QLineEdit()
        self.cookie_button = QtWidgets.QPushButton("Browse")
        self.cookie_button.clicked.connect(self._choose_cookie_file)
        cookie_row.addWidget(self.cookie_edit)
        cookie_row.addWidget(self.cookie_button)
        form.addRow("Cookies", cookie_row)

        self.verbose_check = QtWidgets.QCheckBox("Show verbose log")
        form.addRow("", self.verbose_check)

        root.addWidget(card)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        self.download_button = QtWidgets.QPushButton("Download")
        self.download_button.clicked.connect(self._start_download)
        button_row.addWidget(self.download_button)
        root.addLayout(button_row)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        root.addWidget(self.progress)

        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        root.addWidget(self.log_view, stretch=1)

        history_row = QtWidgets.QHBoxLayout()
        history_row.addWidget(QtWidgets.QLabel("History"))
        self.history_list = QtWidgets.QListWidget()
        self.history_list.itemDoubleClicked.connect(self._open_history_path)
        history_container = QtWidgets.QWidget()
        history_layout = QtWidgets.QVBoxLayout(history_container)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.addWidget(self.history_list)
        history_row.addWidget(history_container, stretch=1)
        root.addLayout(history_row, stretch=1)

        self.status = QtWidgets.QLabel("Ready")
        self.status.setObjectName("status")
        self.statusBar().addWidget(self.status)

    def _load_initial_values(self) -> None:
        cfg = load_config()
        self.output_edit.setText(cfg.get("output_dir") or "downloads")
        self.cookie_edit.setText(cfg.get("cookies_path") or "")
        geom = cfg.get("window_geometry") or {}
        self.resize(int(geom.get("width") or 900), int(geom.get("height") or 520))

    def _apply_theme(self) -> None:
        self.setStyleSheet(themed_stylesheet())

    def _choose_output_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def _choose_cookie_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select cookie file", str(Path.home()), "Text files (*.txt);;All files (*.*)")
        if path:
            self.cookie_edit.setText(path)

    def _start_download(self) -> None:
        url = self.url_input.text().strip()
        if not is_tweet_url(url):
            QtWidgets.QMessageBox.critical(self, "Invalid URL", "Please enter a valid Twitter/X tweet URL.")
            return
        output_dir = self.output_edit.text().strip() or "downloads"
        cookies = self.cookie_edit.text().strip() or None
        verbose = self.verbose_check.isChecked()
        self._set_downloading_state(True)
        self._append_log(f"Starting: {url}")
        self.progress.setValue(0)
        self._worker = Worker(url, output_dir, cookies, verbose)
        self._worker.started_signal.connect(lambda u: self.status.setText(f"Downloading: {u}"))
        self._worker.progress_signal.connect(self._on_progress)
        self._worker.log_signal.connect(self._append_log)
        self._worker.finished_signal.connect(self._on_finished)
        self._worker.error_signal.connect(self._on_error)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_progress(self, percent: float, status: str) -> None:
        self.progress.setValue(int(max(0.0, min(percent, 100.0))))
        if status:
            self._append_log(f"[progress] {status}")

    def _on_finished(self, path: str) -> None:
        self._append_log(f"Finished: {path}\n")
        item = QtWidgets.QListWidgetItem(Path(path).name)
        item.setData(QtCore.Qt.UserRole, path)
        item.setToolTip(path)
        self.history_list.addItem(item)
        self._append_history_record(path, "succeeded")
        self._set_downloading_state(False)
        self.status.setText("Ready")

    def _on_error(self, message: str) -> None:
        url = self.url_input.text().strip()
        self._append_log(f"Error: {message}\n")
        item = QtWidgets.QListWidgetItem(f"Failed: {url}")
        item.setToolTip(message)
        self.history_list.addItem(item)
        self._append_history_record(url, f"failed: {message}")
        self._set_downloading_state(False)
        self.status.setText("Error")

    def _append_log(self, text: str) -> None:
        self.log_view.append(text)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _append_history_record(self, url: str, outcome: str) -> None:
        self._history.append({"url": url, "outcome": outcome, "at": QtCore.QDateTime.currentDateTime().toString(QtCore.Qt.ISODate)})

    def _open_history_path(self, item: QtWidgets.QListWidgetItem) -> None:
        path = item.data(QtCore.Qt.UserRole)
        if not path:
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Failed to open", f"Could not open the target directory.\n{exc}")

    def _set_downloading_state(self, busy: bool) -> None:
        self.download_button.setEnabled(not busy)
        self.url_input.setEnabled(not busy)
        self.output_edit.setEnabled(not busy)
        self.output_button.setEnabled(not busy)
        self.cookie_edit.setEnabled(not busy)
        self.cookie_button.setEnabled(not busy)
        self.verbose_check.setEnabled(not busy)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        cfg = load_config()
        cfg["window_geometry"] = {
            "width": self.width(),
            "height": self.height(),
        }
        cfg["output_dir"] = self.output_edit.text().strip() or "downloads"
        cfg["cookies_path"] = self.cookie_edit.text().strip()
        save_config(cfg)
        super().closeEvent(event)


def run() -> int:
    app = QtWidgets.QApplication(sys.argv)
    ensure_app_metadata(app)
    app.setStyleSheet(themed_stylesheet())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())

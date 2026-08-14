from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from twitter_downloader.gui.main import MainWindow, Worker


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_worker_exposes_progress_handler_callbacks(self) -> None:
        worker = Worker("https://x.com/alice/status/123", "downloads", None, False)
        events: list[tuple[str, str]] = []
        worker.started_signal.connect(lambda url: events.append(("start", url)))
        worker.progress_signal.connect(lambda percent, status: events.append(("progress", f"{percent}:{status}")))
        worker.log_signal.connect(lambda message: events.append(("log", message)))
        worker.finished_signal.connect(lambda path: events.append(("done", path)))
        worker.error_signal.connect(lambda error: events.append(("error", error)))

        worker.on_start("https://x.com/alice/status/123")
        worker.on_progress(50.0, "downloading")
        worker.on_log("[debug] extracting")
        worker.on_done("downloads/alice/00000000/123")
        worker.on_error("https://x.com/alice/status/123", "private post")

        self.assertFalse(hasattr(worker, "error"))
        self.assertEqual(
            events,
            [
                ("start", "https://x.com/alice/status/123"),
                ("progress", "50.0:downloading"),
                ("log", "[debug] extracting"),
                ("done", "downloads/alice/00000000/123"),
                ("error", "private post"),
            ],
        )

    def test_failed_task_is_added_to_visible_history(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.url_input.setText("https://x.com/alice/status/123")

        window._on_error("private post")

        self.assertEqual(window.history_list.count(), 1)
        self.assertEqual(window.history_list.item(0).text(), "Failed: https://x.com/alice/status/123")
        self.assertEqual(window._history[-1]["outcome"], "failed: private post")

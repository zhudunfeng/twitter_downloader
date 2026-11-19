"""下载器桌面窗口：离线作品时间线、下载任务与用户设置。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from twitter_downloader.core import download_one
from twitter_downloader.utils import is_tweet_url

from .config import (
    APP_DATA_DIR,
    CACHE_DIR,
    DATA_DIR_ENV,
    DEFAULT_DATA_DIR,
    DEFAULT_OUTPUT_DIR,
    LIBRARY_DB_PATH,
    PERSONAL_DB_PATH,
    SOFTWARE_DIR,
    STORAGE_CONFIG_PATH,
    StorageError,
    configure_data_directory,
    configured_data_directory,
    load_config,
    prepare_storage,
    resolve_output_directory,
    save_config,
)
from .library_page import LibraryPage
from .resources import (
    ensure_app_metadata,
    icon_from_theme,
    navigation_icon,
    themed_stylesheet,
)

logger = logging.getLogger(__name__)
APP_NAME = "TwitterDownloader"  # 保持既有应用名与配置目录兼容。


class Worker(QtCore.QThread):
    """把共用下载核心转接为 Qt 信号，下载不阻塞阅读。"""

    started_signal = QtCore.Signal(str)  # 当前下载链接。
    progress_signal = QtCore.Signal(float, str)  # 百分比与阶段说明。
    log_signal = QtCore.Signal(str)  # 核心下载日志。
    finished_signal = QtCore.Signal(str)  # 完整落盘的作品目录。
    error_signal = QtCore.Signal(str)  # 用户可见的失败原因。

    def __init__(
        self,
        url: str,
        output_dir: str,
        cookies: str | None,
        verbose: bool,
        parent: QtCore.QObject | None = None,
    ) -> None:
        """固定本次下载配置，避免界面变更影响活动任务。"""
        super().__init__(parent)
        self.url = url  # 任务提交时的推文地址。
        self.output_dir = output_dir  # 任务提交时的保存根目录。
        self.cookies = cookies  # 可选登录态来源。
        self.verbose = verbose  # 是否输出详细调试信息。
        self._reported_error = False  # 核心是否已发送错误回调。

    def run(self) -> None:
        """执行下载，核心未回调的异常也会发送到界面。"""
        logger.info("开始下载推文：%s", self.url)
        try:
            download_one(
                self.url,
                self.output_dir,
                cookies=self.cookies,
                verbose=self.verbose,
                progress=self,
            )
        except Exception as exc:
            logger.exception("下载线程执行失败：%s", self.url)
            if not self._reported_error:
                self.error_signal.emit(str(exc) or exc.__class__.__name__)

    def on_start(self, url: str) -> None:
        """转发核心开始事件。"""
        self.started_signal.emit(url)

    def on_progress(self, percent: float, status: str) -> None:
        """转发下载进度和业务阶段。"""
        self.progress_signal.emit(percent, status)

    def on_log(self, message: str) -> None:
        """转发核心日志。"""
        self.log_signal.emit(message)

    def on_done(self, path: str) -> None:
        """通知内容已完整发布，此时线程可能仍在收尾。"""
        self.finished_signal.emit(path)

    def on_error(self, url: str, error: str) -> None:
        """标记已回调，防止异常处理重复生成失败记录。"""
        self._reported_error = True
        self.error_signal.emit(error)


class MainWindow(QtWidgets.QMainWindow):
    """以作品阅读为首页，保留兼容的下载控件与回调。"""

    def __init__(self) -> None:
        """建立轻量外壳，实际显示后才启动作品库线程。"""
        super().__init__()
        ensure_app_metadata(QtWidgets.QApplication.instance())
        self._config = load_config()  # 兼容旧键名的用户设置快照。
        self._worker: Worker | None = None  # 持有到实际线程结束的下载工作。
        self._library: LibraryPage | None = None  # 延迟创建的作品库页。
        self._navigation_index = 0  # 当前导航路由；前三项复用同一个作品库页面。
        self._history: list[dict[str, str]] = []  # 当前会话下载记录。
        self._pending_directories: list[
            tuple[str, str]
        ] = []  # 页面初始化前完成的目录。
        self._library_roots = [
            root
            for root in self._config.get("library_roots", [])
            if isinstance(root, str) and root.strip()
        ]  # 用户授权的作品位置。
        self.setWindowTitle("Twitter / X · 本地作品库")
        self.setWindowIcon(icon_from_theme("X"))
        self.setMinimumSize(700, 500)
        self._build_ui()
        self._load_initial_values()
        self.setStyleSheet(themed_stylesheet())

    def _build_ui(self) -> None:
        """建立五个导航入口，作品库、喜欢和收藏共用同一阅读页面。"""
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.navigation = QtWidgets.QFrame()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(184)
        nav = QtWidgets.QVBoxLayout(self.navigation)
        nav.setContentsMargins(16, 28, 16, 18)
        nav.setSpacing(12)
        self.brand = QtWidgets.QLabel("X  本地作品")
        self.brand.setObjectName("brand")
        nav.addWidget(self.brand)
        nav.addSpacing(22)
        self.nav_buttons: list[QtWidgets.QPushButton] = []  # 页面顺序对应的导航控件。
        self._nav_labels = (
            "作品库",
            "喜欢",
            "收藏",
            "下载",
            "设置",
        )  # 宽屏标签与无障碍名称。
        icons = ("library", "like", "bookmark", "download", "settings")
        group = QtWidgets.QButtonGroup(self)
        for index, (label, icon) in enumerate(zip(self._nav_labels, icons)):
            button = QtWidgets.QPushButton(label)
            button.setObjectName("navItem")
            button.setCheckable(True)
            button.setIcon(navigation_icon(icon))
            button.setIconSize(QtCore.QSize(21, 21))
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.clicked.connect(
                lambda checked=False, page=index: self._switch_page(page)
            )
            group.addButton(button)
            nav.addWidget(button)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)
        nav.addStretch(1)
        self.nav_note = QtWidgets.QLabel("已下载，即可离线重温")
        self.nav_note.setObjectName("caption")
        self.nav_note.setWordWrap(True)
        nav.addWidget(self.nav_note)
        root.addWidget(self.navigation)
        self.pages = QtWidgets.QStackedWidget()
        self.library_host = QtWidgets.QWidget()
        self.library_layout = QtWidgets.QVBoxLayout(self.library_host)
        self.library_layout.setContentsMargins(0, 0, 0, 0)
        self.loading_label = QtWidgets.QLabel("正在准备本地作品库…")
        self.loading_label.setAlignment(QtCore.Qt.AlignCenter)
        self.library_layout.addWidget(self.loading_label)
        self.pages.addWidget(self.library_host)
        self.pages.addWidget(self._build_downloads())
        self.pages.addWidget(self._build_settings())
        root.addWidget(self.pages, 1)
        self.status = QtWidgets.QLabel("就绪 · 离线作品库")
        self.status.setObjectName("status")
        self.statusBar().addWidget(self.status, 1)

    def _build_downloads(self) -> QtWidgets.QWidget:
        """保留下载表单、进度、有界日志及可直接阅读的历史。"""
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(30, 26, 30, 22)
        layout.setSpacing(16)
        title = QtWidgets.QLabel("下载推文")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        hint = QtWidgets.QLabel("粘贴 Twitter / X 推文链接，下载完成后自动加入作品库。")
        hint.setObjectName("caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QtWidgets.QHBoxLayout()
        self.url_input = QtWidgets.QLineEdit()
        self.url_input.setPlaceholderText("https://x.com/<user>/status/<id>")
        self.url_input.setAccessibleName("推文下载链接")
        self.url_input.returnPressed.connect(self._start_download)
        row.addWidget(self.url_input, 1)
        self.download_button = QtWidgets.QPushButton("开始下载")
        self.download_button.clicked.connect(self._start_download)
        row.addWidget(self.download_button)
        layout.addLayout(row)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setAccessibleName("下载进度")
        layout.addWidget(self.progress)
        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(2000)
        self.log_view.setAccessibleName("下载日志")
        layout.addWidget(self.log_view, 2)
        title = QtWidgets.QLabel("本次下载 · 双击打开作品")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.history_list = QtWidgets.QListWidget()
        self.history_list.setAccessibleName("下载历史")
        self.history_list.itemDoubleClicked.connect(self._open_history_path)
        layout.addWidget(self.history_list, 1)
        return page

    def _build_settings(self) -> QtWidgets.QWidget:
        """配置下载与数据位置，通过滚动容纳紧凑窗口中的路径说明。"""
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        page = QtWidgets.QWidget()
        scroll.setWidget(page)
        # 滚动容器默认填充浅色调色板；透明继承主窗口，保持既有深色阅读主题。
        page.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(30, 26, 30, 24)
        layout.setSpacing(18)
        title = QtWidgets.QLabel("设置")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        form = QtWidgets.QFormLayout(card)
        form.setContentsMargins(22, 22, 22, 22)
        form.setVerticalSpacing(18)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setAccessibleName("下载保存根目录")
        self.output_button = QtWidgets.QPushButton("浏览")
        self.output_button.setObjectName("secondary")
        self.output_button.clicked.connect(self._choose_output_dir)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.output_edit, 1)
        row.addWidget(self.output_button)
        form.addRow("下载目录", row)
        hint = QtWidgets.QLabel(
            "按账号 / 日期 / 推文 ID 保存。已有目录可在作品库中导入，原文件保持原位。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("caption")
        form.addRow("", hint)
        self.cookie_edit = QtWidgets.QLineEdit()
        self.cookie_edit.setPlaceholderText("可选：cookies.txt 或 browser=chrome")
        self.cookie_edit.setAccessibleName("下载登录态来源")
        self.cookie_button = QtWidgets.QPushButton("浏览")
        self.cookie_button.setObjectName("secondary")
        self.cookie_button.clicked.connect(self._choose_cookie_file)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.cookie_edit, 1)
        row.addWidget(self.cookie_button)
        form.addRow("Cookies", row)
        hint = QtWidgets.QLabel(
            "仅下载需要登录的内容时使用；阅读本地作品无需登录或联网。"
        )
        hint.setObjectName("caption")
        hint.setWordWrap(True)
        form.addRow("", hint)
        self.verbose_check = QtWidgets.QCheckBox("显示详细下载日志")
        form.addRow("日志", self.verbose_check)
        self.data_dir_edit = QtWidgets.QLineEdit()
        self.data_dir_edit.setAccessibleName("软件数据目录，重启后生效")
        self.data_dir_edit.setPlaceholderText(str(DEFAULT_DATA_DIR))
        self.data_dir_button = QtWidgets.QPushButton("浏览")
        self.data_dir_button.setObjectName("secondary")
        self.data_dir_button.clicked.connect(self._choose_data_dir)
        self.data_dir_reset = QtWidgets.QPushButton("恢复默认")
        self.data_dir_reset.setObjectName("secondary")
        self.data_dir_reset.clicked.connect(self._reset_data_dir)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.data_dir_edit, 1)
        row.addWidget(self.data_dir_button)
        row.addWidget(self.data_dir_reset)
        form.addRow("数据目录", row)
        self.data_dir_hint = QtWidgets.QLabel()
        self.data_dir_hint.setTextFormat(QtCore.Qt.PlainText)
        self.data_dir_hint.setWordWrap(True)
        self.data_dir_hint.setObjectName("caption")
        self.data_dir_hint.setAccessibleName("数据目录生效状态")
        form.addRow("", self.data_dir_hint)
        layout.addWidget(card)
        note = QtWidgets.QLabel(
            f"当前数据目录：{APP_DATA_DIR}\n作品索引：{LIBRARY_DB_PATH}"
            f"\n喜欢与收藏：{PERSONAL_DB_PATH}\n预览缓存：{CACHE_DIR}"
            "\n数据目录还包含设置和日志。索引与缓存可重建，喜欢与收藏数据请保留。"
            "\n空目录首次启用时复制原有数据，旧文件保留；已有软件数据的目录直接使用，不合并或覆盖。"
            "\n原始下载文件不会随数据目录切换而移动。"
        )
        note.setTextFormat(QtCore.Qt.PlainText)
        note.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        note.setWordWrap(True)
        note.setObjectName("caption")
        layout.addWidget(note)
        self.settings_save = QtWidgets.QPushButton("保存设置")
        self.settings_save.clicked.connect(self._save_settings)
        layout.addWidget(self.settings_save, 0, QtCore.Qt.AlignLeft)
        layout.addStretch(1)
        return scroll

    def _load_initial_values(self) -> None:
        """恢复下载设置与经过有效范围检查的窗口大小。"""
        self.output_edit.setText(
            str(
                resolve_output_directory(
                    self._config.get("output_dir") or DEFAULT_OUTPUT_DIR
                )
            )
        )
        self.cookie_edit.setText(str(self._config.get("cookies_path") or ""))
        self.data_dir_edit.setText(str(configured_data_directory()))
        self._refresh_storage_hint()
        geometry = self._config.get("window_geometry") or {}
        try:
            width, height = (
                int(geometry.get("width", 1220)),
                int(geometry.get("height", 820)),
            )
        except (TypeError, ValueError):
            width, height = 1220, 820
        screen = self.screen().availableGeometry()
        self.resize(
            max(700, min(width, screen.width())), max(500, min(height, screen.height()))
        )

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        """实际显示窗口后才创建多媒体与索引线程。"""
        super().showEvent(event)
        if self._library is None:
            QtCore.QTimer.singleShot(0, self._ensure_library)

    def _ensure_library(self) -> None:
        """按当前下载目录与显式导入目录建立作品库。"""
        if self._library is not None:
            return
        roots = list(
            dict.fromkeys(
                [
                    *self._library_roots,
                    str(
                        resolve_output_directory(
                            self.output_edit.text().strip() or DEFAULT_OUTPUT_DIR
                        )
                    ),
                ]
            )
        )
        self._library = LibraryPage(
            roots,
            LIBRARY_DB_PATH,
            CACHE_DIR,
            self.library_host,
            personal_path=PERSONAL_DB_PATH,
        )
        self._library.roots_changed.connect(self._roots_changed)
        self._library.status_changed.connect(self._library_status)
        self._library.download_requested.connect(self._show_downloads)
        self.loading_label.hide()
        self.library_layout.addWidget(self._library)
        if self._navigation_index < 3:
            self._library.set_scope(
                ("all", "liked", "bookmarked")[self._navigation_index]
            )
        for path, root in self._pending_directories:
            self._library.index_directory(path, root)
        self._pending_directories.clear()

    def _switch_page(self, index: int) -> None:
        """将导航路由映射到共用作品页或下载设置页，并同步筛选范围。"""
        if not 0 <= index < len(self.nav_buttons):
            return
        self._navigation_index = index
        if self._library is not None:
            self._library.pause_playback()
        if index < 3:
            self._ensure_library()
            if self._library is not None:
                self._library.set_scope(("all", "liked", "bookmarked")[index])
            self.pages.setCurrentWidget(self.library_host)
        else:
            self.pages.setCurrentIndex(index - 2)
        self.nav_buttons[index].setChecked(True)
        if index == 3:
            self.url_input.setFocus()

    def _show_downloads(self) -> None:
        """响应作品库空状态中的下载入口。"""
        self._switch_page(3)

    def _library_status(self, message: str) -> None:
        """无下载活动时显示目录同步状态。"""
        if self._worker is None:
            self.status.setText(message)

    def _roots_changed(self, roots: list[str]) -> None:
        """保存导入位置，重启后继续发现这些作品。"""
        self._library_roots = roots[:]
        self._persist_config()

    def _persist_config(self) -> bool:
        """原子保存设置，失败时保留本会话内容。"""
        try:
            self._config.update(
                output_dir=str(
                    resolve_output_directory(
                        self.output_edit.text().strip() or DEFAULT_OUTPUT_DIR
                    )
                ),
                cookies_path=self.cookie_edit.text().strip(),
                library_roots=self._library_roots,
                window_geometry={"width": self.width(), "height": self.height()},
            )
            save_config(self._config)
            return True
        except (OSError, StorageError) as exc:
            logger.warning("设置保存失败：%s", exc)
            self.status.setText(f"设置暂未保存：{exc}")
            return False

    def _save_settings(self) -> None:
        """保存当前设置并登记下次启动的数据目录，运行期不切换数据库。"""
        if self._persist_config():
            try:
                target = configured_data_directory()
                requested = self.data_dir_edit.text().strip()
                # 环境覆盖时保留只读显示；常规设置只在用户提交新路径时改引导文件。
                if not self._storage_overridden() and requested != str(target):
                    target = configure_data_directory(requested)
                self.data_dir_edit.setText(str(target))
            except (OSError, ValueError, StorageError) as exc:
                logger.warning("数据目录设置失败，当前数据位置保持不变：%s", exc)
                self.data_dir_hint.setText(f"数据目录未保存：{exc}")
                self.status.setText("下载等设置已保存，数据目录未更新，请检查路径")
                return
            if self._library is not None:
                self._library.add_root(
                    str(
                        resolve_output_directory(
                            self.output_edit.text().strip() or DEFAULT_OUTPUT_DIR
                        )
                    )
                )
            self._refresh_storage_hint()
            pending = target != APP_DATA_DIR
            self.status.setText(
                "设置已保存；数据目录将在重启后切换" if pending else "设置已保存"
            )

    def _storage_overridden(self) -> bool:
        """检查数据根目录是否由环境变量覆盖，避免保存一个不会生效的路径。"""
        import os

        return DATA_DIR_ENV in os.environ

    def _refresh_storage_hint(self) -> None:
        """显示实际目录与重启边界，并解释环境变量的优先级。"""
        overridden = self._storage_overridden()
        for widget in (self.data_dir_edit, self.data_dir_button, self.data_dir_reset):
            widget.setEnabled(not overridden)
        if overridden:
            self.data_dir_hint.setText(
                f"当前由环境变量 {DATA_DIR_ENV} 指定。修改或移除此变量后重启，才能使用软件内设置。"
            )
        else:
            target = configured_data_directory()
            self.data_dir_hint.setText(
                f"已保存，下次启动使用：{target}。当前仍使用：{APP_DATA_DIR}。"
                if target != APP_DATA_DIR
                else "默认位于软件目录/data；可输入绝对路径或相对软件目录的路径。保存并重启后生效。"
            )

    def _choose_data_dir(self) -> None:
        """选择应用配置、数据库、日志和预览缓存的统一存放根目录。"""
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择软件数据目录（重启后生效）", self.data_dir_edit.text()
        )
        if path:
            self.data_dir_edit.setText(path)

    def _reset_data_dir(self) -> None:
        """仅把输入恢复为软件旁的默认目录，保存前不改变任何文件。"""
        self.data_dir_edit.setText(str(DEFAULT_DATA_DIR))

    def _choose_output_dir(self) -> None:
        """选择后续下载的保存根目录。"""
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择下载目录", self.output_edit.text()
        )
        if path:
            self.output_edit.setText(path)

    def _choose_cookie_file(self) -> None:
        """选择可选 Netscape 格式 cookie 文件。"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 Cookie 文件",
            str(SOFTWARE_DIR),
            "文本文件 (*.txt);;所有文件 (*.*)",
        )
        if path:
            self.cookie_edit.setText(path)

    def _start_download(self) -> None:
        """校验地址后启动单一下载，允许同时浏览作品库。"""
        if self._worker is not None:
            return
        url = self.url_input.text().strip()
        if not is_tweet_url(url):
            QtWidgets.QMessageBox.warning(
                self, "链接无效", "请输入有效的 Twitter / X 推文链接。"
            )
            return
        try:
            output = str(
                resolve_output_directory(
                    self.output_edit.text().strip() or DEFAULT_OUTPUT_DIR
                )
            )
        except StorageError as exc:
            logger.warning("下载目录无效，未启动任务：%s", exc)
            self.status.setText(f"下载目录不可用，请在设置中修改：{exc}")
            return
        self._persist_config()
        self._set_downloading_state(True)
        self._append_log(f"开始下载：{url}")
        self.status.setText("正在下载，可切换到作品库继续阅读")
        self.progress.setValue(0)
        worker = Worker(
            url,
            output,
            self.cookie_edit.text().strip() or None,
            self.verbose_check.isChecked(),
            self,
        )
        self._worker = worker
        worker.progress_signal.connect(self._on_progress)
        worker.log_signal.connect(self._append_log)
        worker.finished_signal.connect(self._on_finished)
        worker.error_signal.connect(self._on_error)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _worker_finished(self) -> None:
        """实际线程退出后才允许下次下载并释放对象。"""
        worker = self._worker
        self._worker = None
        self._set_downloading_state(False)
        if worker is not None:
            worker.deleteLater()

    def _on_progress(self, percent: float, status: str) -> None:
        """更新下载进度并显示可继续阅读的提示。"""
        self.progress.setValue(int(max(0.0, min(percent, 100.0))))
        self.status.setText(f"下载中 {self.progress.value()}% · 可继续浏览作品库")
        if status:
            self._append_log(f"[进度] {status}")

    def _on_finished(self, path: str) -> None:
        """记录完成目录，并只增量导入新作品。"""
        logger.info("推文下载完成：%s", path)
        self._append_log(f"下载完成：{path}")
        item = QtWidgets.QListWidgetItem(Path(path).name)
        item.setData(QtCore.Qt.UserRole, path)
        root = (
            self._worker.output_dir
            if self._worker is not None
            else self.output_edit.text().strip() or "downloads"
        )
        item.setData(QtCore.Qt.UserRole + 1, root)
        item.setToolTip(path)
        self.history_list.addItem(item)
        self._append_history_record(path, "succeeded")
        if self._library is not None:
            self._library.index_directory(path, root)
        else:
            self._pending_directories.append((path, root))
        self.progress.setValue(100)
        self.status.setText("下载完成 · 已加入本地作品库")
        if self._worker is None:
            self._set_downloading_state(False)

    def _on_error(self, message: str) -> None:
        """记录一次失败，保留原有失败历史文本契约。"""
        url = (
            self._worker.url
            if self._worker is not None
            else self.url_input.text().strip()
        )
        logger.warning("推文下载失败：%s", message)
        self._append_log(f"下载失败：{message}")
        item = QtWidgets.QListWidgetItem(f"Failed: {url}")
        item.setToolTip(message)
        self.history_list.addItem(item)
        self._append_history_record(url, f"failed: {message}")
        self.status.setText("下载失败，详细原因见下载日志")
        if self._worker is None:
            self._set_downloading_state(False)

    def _append_log(self, text: str) -> None:
        """按纯文本追加日志，避免下载输出被误当作 HTML。"""
        self.log_view.moveCursor(QtGui.QTextCursor.End)
        self.log_view.insertPlainText(text + "\n")
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def _append_history_record(self, url: str, outcome: str) -> None:
        """保存本会话的下载结果与时间。"""
        self._history.append(
            {
                "url": url,
                "outcome": outcome,
                "at": QtCore.QDateTime.currentDateTime().toString(QtCore.Qt.ISODate),
            }
        )

    def _open_history_path(self, item: QtWidgets.QListWidgetItem) -> None:
        """直接异步打开历史作品的应用内阅读视图。"""
        path = item.data(QtCore.Qt.UserRole)
        if path:
            self._ensure_library()
            self._switch_page(0)
            if self._library is not None:
                self._library.open_directory(
                    str(path),
                    str(
                        item.data(QtCore.Qt.UserRole + 1)
                        or self.output_edit.text()
                        or DEFAULT_OUTPUT_DIR
                    ),
                )

    def _set_downloading_state(self, busy: bool) -> None:
        """锁定本次下载配置，作品库导航仍可使用。"""
        for widget in (
            self.download_button,
            self.url_input,
            self.output_edit,
            self.output_button,
            self.cookie_edit,
            self.cookie_button,
            self.verbose_check,
        ):
            widget.setEnabled(not busy)
        self.download_button.setText("正在下载…" if busy else "开始下载")

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """紧凑窗口收起导航文字，为推文留出阅读宽度。"""
        super().resizeEvent(event)
        if hasattr(self, "navigation"):
            compact = self.width() < 1080
            self.navigation.setFixedWidth(76 if compact else 184)
            self.brand.setText("X" if compact else "X  本地作品")
            self.nav_note.setVisible(not compact)
            for button, label in zip(self.nav_buttons, self._nav_labels):
                button.setText("" if compact else label)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """下载未结束时阻止销毁线程，否则安全关闭作品库。"""
        if self._worker is not None and self._worker.isRunning():
            self.status.setText("下载尚未结束，请完成后关闭；可继续浏览作品库")
            event.ignore()
            return
        self._persist_config()
        if self._library is not None and not self._library.shutdown():
            self.status.setText("喜欢或收藏尚未完成保存，请重试后关闭")
            event.ignore()
            return
        super().closeEvent(event)


def run() -> int:
    """先准备可配置的便携数据目录，再启动日志和桌面窗口。"""
    from logging.handlers import MemoryHandler, RotatingFileHandler

    app = QtWidgets.QApplication(sys.argv)
    ensure_app_metadata(app)
    app.setStyle("Fusion")
    app.setStyleSheet(themed_stylesheet())
    business_logger = logging.getLogger("twitter_downloader")
    business_logger.setLevel(logging.INFO)
    startup_log = MemoryHandler(capacity=128)  # 迁移阶段先缓存日志，成功后写入新位置。
    business_logger.addHandler(startup_log)
    try:
        # 在任何日志、数据库或缓存创建之前完成迁移，不能把空库误当作有效目标。
        prepare_storage()
        handler = RotatingFileHandler(
            APP_DATA_DIR / "desktop.log",
            maxBytes=2 * 1024 * 1024,  # 每个日志文件最多保留 2 MiB。
            backupCount=2,  # 额外保留两个轮转日志文件。
            encoding="utf-8",  # 中文业务日志统一使用 UTF-8。
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        business_logger.addHandler(handler)
        startup_log.setTarget(handler)
        startup_log.flush()
        business_logger.removeHandler(startup_log)
        logger.info("软件数据目录已启用：%s；预览缓存：%s", APP_DATA_DIR, CACHE_DIR)
    except (OSError, StorageError) as exc:
        logger.error("软件数据目录初始化失败：%s", exc)
        QtWidgets.QMessageBox.critical(
            None,
            "软件数据目录不可用",
            f"{exc}\n\n请检查目录读写权限，或修改引导文件：{STORAGE_CONFIG_PATH}"
            f"\n也可通过环境变量 {DATA_DIR_ENV} 指定可写目录后重新启动。"
            "\n本次没有回退到 C 盘用户目录。",
        )
        return 1
    finally:
        business_logger.removeHandler(startup_log)
        startup_log.close()
    try:
        window = MainWindow()
    except (OSError, ValueError, StorageError) as exc:
        logger.error("软件配置加载失败：%s", exc)
        QtWidgets.QMessageBox.critical(None, "软件配置不可用", str(exc))
        business_logger.removeHandler(handler)
        handler.close()
        return 1
    window.show()
    try:
        return app.exec()
    finally:
        business_logger.removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    sys.exit(run())

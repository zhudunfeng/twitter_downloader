"""作品库页面：后台索引、筛选搜索、推文流及唯一媒体播放器。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from twitter_downloader.library.models import LibraryQuery, LibraryWork
from twitter_downloader.library.reader import read_work
from twitter_downloader.library.repository import LibraryRepository
from twitter_downloader.library.scanner import LibraryScanner

from .image_viewer import ImageViewer
from .thumbnail_service import ThumbnailService
from .timeline_view import TimelineView
from .tweet_detail import TweetDetail
from .video_player import VideoPlayer

logger = logging.getLogger(__name__)
PAGE_SIZE = 80  # 每次从目录索引读取的推文数量，避免一次渲染整个作品库。
SEARCH_DELAY_MS = 200  # 输入防抖时间，单位毫秒。


class _LibraryJob(QtCore.QThread):
    """每个后台作业独享 SQLite 连接，禁止跨线程使用数据库对象。"""

    result = QtCore.Signal(object)  # 向界面返回普通结果记录。
    failure = QtCore.Signal(str)  # 可展示的中文错误说明。
    progress = QtCore.Signal(int, str)  # 扫描数量和当前路径。

    def __init__(
        self,
        action: str,
        db_path: Path,
        parent: QtCore.QObject,
        *,
        roots: list[str] | None = None,
        query: LibraryQuery | None = None,
        offset: int = 0,
        limit: int = PAGE_SIZE,
        generation: int = 0,
        directory: str = "",
        root: str = "",
        preserve_anchor: bool = True,
    ) -> None:
        """记录作业配置；分页限制、版本号和保留锚点均由页面显式传入。"""
        super().__init__(parent)
        self.action = action  # scan、query 或 upsert 作业类型。
        self.db_path = db_path  # 目录索引数据库路径。
        self.roots = roots or []  # 需要扫描的已授权根目录。
        self.query = query or LibraryQuery()  # 当前不可变筛选条件。
        self.offset = offset  # 本批数据的起始偏移。
        self.limit = limit  # 本批数据的最大数量。
        self.generation = generation  # 防止旧查询覆盖新筛选的版本号。
        self.directory = directory  # 单个下载完成目录。
        self.root = root  # 下载目录所属根目录。
        self.preserve_anchor = preserve_anchor  # 查询替换时是否保留阅读位置。
        self._last_progress: float = 0  # 扫描进度节流时间。

    def run(self) -> None:
        """执行扫描或查询，确保连接在同一工作线程内创建和关闭。"""
        repository: LibraryRepository | None = None
        try:
            repository = LibraryRepository(self.db_path)
            payload: dict[str, Any] = {
                "action": self.action,
                "generation": self.generation,
            }
            if self.action == "scan":
                payload["processed"] = LibraryScanner(repository).scan(
                    self.roots, self._report_progress, self.isInterruptionRequested
                )
            elif self.action in ("upsert", "open"):
                work = read_work(self.directory, self.root)
                repository.upsert(work)
                payload["work"] = work
            else:
                payload.update(
                    works=repository.query(
                        self.query, offset=self.offset, limit=self.limit
                    ),
                    total=repository.count(self.query),
                    authors=repository.authors() if self.offset == 0 else None,
                    offset=self.offset,
                    preserve_anchor=self.preserve_anchor,
                )
            if not self.isInterruptionRequested():
                self.result.emit(payload)
        except Exception as exc:
            logger.exception("作品库后台作业失败：%s", self.action)
            self.failure.emit(f"作品库操作未完成：{exc}")
        finally:
            if repository is not None:
                repository.close()

    def _report_progress(self, count: int, path: str) -> None:
        """限制跨线程进度消息频率，避免大目录挤满事件队列。"""
        now = time.monotonic()
        if now - self._last_progress >= 0.1:
            self._last_progress = now
            self.progress.emit(count, path)


class LibraryPage(QtWidgets.QWidget):
    """整合离线推文阅读与有界后台工作的作品库。"""

    roots_changed = QtCore.Signal(list)  # 已授权作品根目录变更。
    status_changed = QtCore.Signal(str)  # 供主窗口显示的业务状态。
    download_requested = QtCore.Signal()  # 切换到下载页面的请求。

    def __init__(
        self,
        roots: list[str],
        db_path: Path,
        cache_dir: Path,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """建立轻量界面，并延迟启动索引读取与历史作品扫描。"""
        super().__init__(parent)
        self.roots = list(
            dict.fromkeys(str(Path(root).resolve()) for root in roots)
        )  # 去重后的作品位置。
        self.db_path = db_path  # 可随时重建的目录索引。
        self._jobs: list[_LibraryJob] = []  # 页面拥有的后台线程。
        self._closing: bool = False  # 阻止退出过程创建新工作。
        self._query_busy: bool = False  # 查询串行化，避免输入洪泛。
        self._pending_query: dict[str, Any] | None = None  # 只保留最后一次待查询请求。
        self._generation: int = 0  # 当前筛选版本。
        self._total: int = 0  # 当前筛选下的作品总数。
        self._kind: str = ""  # 当前媒体类型筛选。
        self._active_media: tuple[str, int] | None = (
            None  # 唯一活动视频的作品键和序号。
        )
        self._dialogs: list[QtWidgets.QDialog] = []  # 保持媒体/详情窗口引用。
        self._filters_dialog: QtWidgets.QDialog | None = (
            None  # 小窗口下复用右侧筛选的宿主。
        )
        self._scan_again = False  # 扫描期间新增根目录后补做一轮同步。
        self._video_dialog: QtWidgets.QDialog | None = (
            None  # 拼图过小时复用唯一播放器的独立宿主。
        )
        self.thumbnails = ThumbnailService(cache_dir, self)
        self._directory_busy = False  # 单目录导入和历史打开共用一个后台槽位。
        self._pending_open: tuple[str, str] | None = (
            None  # 快速点击历史时只保留最后一次请求。
        )
        self._pending_upserts: dict[
            str, str
        ] = {}  # 下载完成目录按路径合并，避免线程无限增长。
        self._build_ui()
        self.search_timer = QtCore.QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(SEARCH_DELAY_MS)
        self.search_timer.timeout.connect(self._filters_changed)
        self.search.textChanged.connect(self._schedule_search)
        QtCore.QTimer.singleShot(0, self._initial_load)

    def _build_ui(self) -> None:
        """使用中间单列推文流与可折叠筛选侧栏搭建内容页面。"""
        self.columns = QtWidgets.QHBoxLayout(self)
        self.columns.setContentsMargins(0, 0, 0, 0)
        self.columns.setSpacing(0)
        center = QtWidgets.QFrame()
        center.setObjectName("timelineColumn")
        center.setMaximumWidth(700)
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        header = QtWidgets.QWidget()
        header.setObjectName("timelineHeader")
        top = QtWidgets.QHBoxLayout(header)
        top.setContentsMargins(22, 18, 18, 14)
        titles = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("作品库")
        title.setObjectName("pageTitle")
        titles.addWidget(title)
        self.count_label = QtWidgets.QLabel("本地推文 · 离线阅读")
        self.count_label.setObjectName("caption")
        titles.addWidget(self.count_label)
        top.addLayout(titles, 1)
        self.filter_button = QtWidgets.QPushButton("筛选")
        self.filter_button.setObjectName("secondary")
        self.filter_button.clicked.connect(self._show_filters)
        top.addWidget(self.filter_button)
        refresh = QtWidgets.QPushButton("刷新")
        refresh.setObjectName("secondary")
        refresh.clicked.connect(self.refresh)
        top.addWidget(refresh)
        center_layout.addWidget(header)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("搜索正文、作者或推文 ID")
        self.search.setAccessibleName("搜索本地作品")
        self.search.setClearButtonEnabled(True)
        search_box = QtWidgets.QWidget()
        search_layout = QtWidgets.QHBoxLayout(search_box)
        search_layout.setContentsMargins(18, 0, 18, 12)
        search_layout.addWidget(self.search)
        center_layout.addWidget(search_box)
        tabs_widget = QtWidgets.QWidget()
        tabs_widget.setObjectName("timelineTabs")
        tabs = QtWidgets.QHBoxLayout(tabs_widget)
        tabs.setContentsMargins(12, 0, 12, 0)
        tabs.setSpacing(0)
        self.kind_buttons: dict[
            str, QtWidgets.QPushButton
        ] = {}  # 分类与按钮的稳定映射。
        group = QtWidgets.QButtonGroup(self)
        for kind, label in (
            ("", "全部"),
            ("image", "图片"),
            ("video", "视频"),
            ("text", "纯文字"),
            ("article", "文章"),
        ):
            button = QtWidgets.QPushButton(label)
            button.setObjectName("feedTab")
            button.setCheckable(True)
            button.setChecked(not kind)
            button.clicked.connect(
                lambda checked=False, value=kind: self._set_kind(value)
            )
            group.addButton(button)
            self.kind_buttons[kind] = button
            tabs.addWidget(button)
        center_layout.addWidget(tabs_widget)
        self.new_banner = QtWidgets.QPushButton("有新作品，点击查看")
        self.new_banner.setObjectName("newWorks")
        self.new_banner.clicked.connect(self._show_new)
        self.new_banner.hide()
        center_layout.addWidget(self.new_banner)
        self.feedback = QtWidgets.QLabel()
        self.feedback.setTextFormat(QtCore.Qt.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName("libraryFeedback")
        self.feedback.hide()
        center_layout.addWidget(self.feedback)
        self.content = QtWidgets.QStackedWidget()
        self.view = TimelineView(self.thumbnails)
        self.view.setAccessibleName("本地推文时间线")
        self.view.open_detail.connect(self._open_detail)
        self.view.open_media.connect(self._open_media)
        self.view.menu_requested.connect(self._show_menu)
        self.view.visible_changed.connect(self._position_player)
        self.view.near_end.connect(self._load_more)
        self.content.addWidget(self.view)
        self.empty = QtWidgets.QWidget()
        empty_layout = QtWidgets.QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(30, 40, 30, 40)
        empty_layout.addStretch(1)
        self.empty_title = QtWidgets.QLabel("你的收藏，从这里开始")
        self.empty_title.setObjectName("emptyTitle")
        self.empty_title.setWordWrap(True)
        empty_layout.addWidget(self.empty_title)
        self.empty_help = QtWidgets.QLabel(
            "导入已有下载目录，或下载第一条推文。\n文字、图片和视频将在这里一起呈现。"
        )
        self.empty_help.setObjectName("caption")
        self.empty_help.setWordWrap(True)
        empty_layout.addWidget(self.empty_help)
        import_button = QtWidgets.QPushButton("导入本地作品")
        import_button.clicked.connect(self._choose_root)
        empty_layout.addWidget(import_button, 0, QtCore.Qt.AlignLeft)
        download = QtWidgets.QPushButton("去下载推文")
        download.setObjectName("secondary")
        download.clicked.connect(self.download_requested.emit)
        empty_layout.addWidget(download, 0, QtCore.Qt.AlignLeft)
        empty_layout.addStretch(2)
        self.content.addWidget(self.empty)
        center_layout.addWidget(self.content, 1)
        # 时间线先占满可用宽度至其上限，尾部伸展仅接收宽屏剩余空间。
        self.columns.addWidget(center, 10000)
        self.player = VideoPlayer(self.view.viewport())
        self.player.close_requested.connect(self._close_player)
        self.player.placement_changed.connect(self._position_player)
        self.right_rail = QtWidgets.QFrame()
        self.right_rail.setObjectName("filterRail")
        self.right_rail.setFixedWidth(260)
        rail = QtWidgets.QVBoxLayout(self.right_rail)
        rail.setContentsMargins(22, 24, 22, 22)
        rail.setSpacing(14)
        rail_title = QtWidgets.QLabel("发现你的收藏")
        rail_title.setObjectName("sectionTitle")
        rail.addWidget(rail_title)
        hint = QtWidgets.QLabel("按作者、时间或类型，\n找到想重温的那条推文。")
        hint.setObjectName("caption")
        rail.addWidget(hint)
        rail.addWidget(QtWidgets.QLabel("作者"))
        self.author = QtWidgets.QComboBox()
        self.author.addItem("所有作者", "")
        self.author.setAccessibleName("按作者筛选")
        self.author.currentIndexChanged.connect(self._filters_changed)
        rail.addWidget(self.author)
        rail.addWidget(QtWidgets.QLabel("排序"))
        self.sort = QtWidgets.QComboBox()
        self.sort.addItem("最新发布优先", "newest")
        self.sort.addItem("最早发布优先", "oldest")
        self.sort.setAccessibleName("作品排序")
        self.sort.currentIndexChanged.connect(self._filters_changed)
        rail.addWidget(self.sort)
        self.use_dates = QtWidgets.QCheckBox("按发布日期筛选")
        self.use_dates.toggled.connect(self._date_filter_changed)
        rail.addWidget(self.use_dates)
        self.since = QtWidgets.QDateEdit(QtCore.QDate.currentDate().addYears(-1))
        self.until = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        for widget, name in ((self.since, "起始日期"), (self.until, "结束日期")):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
            widget.setAccessibleName(name)
            widget.setToolTip(name)
            widget.setEnabled(False)
            widget.dateChanged.connect(self._filters_changed)
            rail.addWidget(widget)
        reset = QtWidgets.QPushButton("重置筛选")
        reset.setObjectName("secondary")
        reset.clicked.connect(self._reset_filters)
        rail.addWidget(reset)
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        rail.addWidget(line)
        root_title = QtWidgets.QLabel("作品位置")
        root_title.setObjectName("sectionTitle")
        rail.addWidget(root_title)
        self.roots_label = QtWidgets.QLabel()
        self.roots_label.setTextFormat(QtCore.Qt.PlainText)
        self.roots_label.setWordWrap(True)
        self.roots_label.setObjectName("caption")
        self._update_roots_label()
        rail.addWidget(self.roots_label)
        add = QtWidgets.QPushButton("添加下载目录")
        add.setObjectName("secondary")
        add.clicked.connect(self._choose_root)
        rail.addWidget(add)
        self.cancel_scan = QtWidgets.QPushButton("停止扫描")
        self.cancel_scan.setObjectName("secondary")
        self.cancel_scan.clicked.connect(self._cancel_scan)
        self.cancel_scan.hide()
        rail.addWidget(self.cancel_scan)
        rail.addStretch(1)
        offline = QtWidgets.QLabel("仅浏览本地文件\n互动数据为下载时的记录")
        offline.setObjectName("caption")
        rail.addWidget(offline)
        self.columns.addWidget(self.right_rail)
        self.columns.addStretch(1)

    def _initial_load(self) -> None:
        """先显示持久索引，再在后台同步原始目录。"""
        if not self._closing:
            self._reload()
            self.refresh()

    def _query(self) -> LibraryQuery:
        """把界面选择规范化为数据层不可变查询。"""
        return LibraryQuery(
            search=self.search.text().strip(),
            author=str(self.author.currentData() or ""),
            kind=self._kind,
            since=self.since.date().toString("yyyy-MM-dd")
            if self.use_dates.isChecked()
            else "",
            until=self.until.date().toString("yyyy-MM-dd")
            if self.use_dates.isChecked()
            else "",
            sort=str(self.sort.currentData() or "newest"),
        )

    def _start_job(self, job: _LibraryJob) -> None:
        """统一保留线程引用并连接失败、结果与清理入口。"""
        self._jobs.append(job)
        job.result.connect(self._job_result)
        job.failure.connect(self._show_error)
        job.progress.connect(self._scan_progress)
        job.finished.connect(lambda current=job: self._job_finished(current))
        job.start()

    def _request_query(self, offset: int, limit: int, preserve_anchor: bool) -> None:
        """串行化索引读取，只保留最新待处理搜索，丢弃旧版本结果。"""
        if self._closing:
            return
        params = {
            "query": self._query(),
            "offset": offset,
            "limit": limit,
            "generation": self._generation,
            "preserve_anchor": preserve_anchor,
        }
        if self._query_busy:
            self._pending_query = params
            return
        self._query_busy = True
        self._start_job(_LibraryJob("query", self.db_path, self, **params))

    def _reload(self, preserve_anchor: bool = True) -> None:
        """重新读取已加载区间，在数据仍存在时保持可见推文锚点。"""
        self._generation += 1
        count = (
            max(PAGE_SIZE, self.view.timeline_model.rowCount())
            if preserve_anchor
            else PAGE_SIZE
        )
        self._request_query(0, count, preserve_anchor)

    def _load_more(self) -> None:
        """接近列表底部时增量追加下一页，避免一次载入全库。"""
        count = self.view.timeline_model.rowCount()
        if not self._query_busy and count < self._total:
            self._request_query(count, PAGE_SIZE, True)

    def _job_result(self, payload: dict[str, Any]) -> None:
        """处理数据快照，保护当前阅读位置和较新的查询结果。"""
        if self._closing:
            return
        action = payload["action"]
        if action == "scan":
            self.feedback.setText(f"目录同步完成，已检查 {payload['processed']} 条作品")
            self.feedback.show()
            self.status_changed.emit(self.feedback.text())
            self._reload()
        elif action in ("upsert", "open"):
            if action == "open":
                self._open_detail(payload["work"])
            if self.view.verticalScrollBar().value() <= 24:
                self._reload()
            else:
                self.new_banner.show()
        elif payload["generation"] == self._generation:
            self._total = payload["total"]
            if payload["offset"]:
                self.view.append_works(payload["works"])
            else:
                self.view.set_works(
                    payload["works"], preserve_anchor=payload["preserve_anchor"]
                )
            self.count_label.setText(f"{self._total:,} 条作品 · 本地离线阅读")
            self.content.setCurrentWidget(self.view if self._total else self.empty)
            filtered = bool(
                self.search.text()
                or self._kind
                or self.author.currentData()
                or self.use_dates.isChecked()
            )
            self.empty_title.setText(
                "没有找到匹配的作品" if filtered else "你的收藏，从这里开始"
            )
            self.empty_help.setText(
                "试试其他关键词，或重置右侧筛选条件。"
                if filtered
                else "导入已有下载目录，或下载第一条推文。\n文字、图片和视频将在这里一起呈现。"
            )
            if payload.get("authors") is not None:
                self._update_authors(payload["authors"])
            self._position_player()

    def _job_finished(self, job: _LibraryJob) -> None:
        """线程完成后安全释放对象，并启动最后一次待查询请求。"""
        if job in self._jobs:
            self._jobs.remove(job)
        if job.action == "scan":
            self.cancel_scan.hide()
            if job.isInterruptionRequested() and not self._closing:
                self.feedback.setText("扫描已停止，已导入作品仍可浏览")
                self._reload()
            if self._scan_again and not self._closing:
                self._scan_again = False
                self.refresh()
        if job.action == "query":
            self._query_busy = False
            if self._pending_query is not None and not self._closing:
                params = self._pending_query
                self._pending_query = None
                self._query_busy = True
                self._start_job(_LibraryJob("query", self.db_path, self, **params))
        if job.action in ("upsert", "open"):
            self._directory_busy = False
            if not self._closing:
                if self._pending_open is not None:
                    path, root = self._pending_open
                    self._pending_open = None
                    self._start_directory_job("open", path, root)
                elif self._pending_upserts:
                    path = next(iter(self._pending_upserts))
                    self._start_directory_job(
                        "upsert", path, self._pending_upserts.pop(path)
                    )
        job.deleteLater()

    def _start_directory_job(self, action: str, path: str, root: str) -> None:
        """串行读取单目录，合并连续历史选择和重复下载通知。"""
        if self._closing:
            return
        if self._directory_busy:
            if action == "open":
                self._pending_open = (path, root)
            else:
                self._pending_upserts[path] = root
            return
        self._directory_busy = True
        self._start_job(
            _LibraryJob(action, self.db_path, self, directory=path, root=root)
        )

    def refresh(self) -> None:
        """后台增量扫描全部作品根目录，保留可用的现有索引。"""
        if self._closing or any(job.action == "scan" for job in self._jobs):
            return
        logger.info("开始同步本地作品目录，共 %d 个位置", len(self.roots))
        self.feedback.setText("正在后台同步作品目录，可以继续浏览…")
        self.feedback.show()
        self.cancel_scan.show()
        self._start_job(_LibraryJob("scan", self.db_path, self, roots=self.roots[:]))

    def index_directory(self, path: str, root: str) -> None:
        """下载成功后只更新完成目录，避免重新扫描整个作品库。"""
        normalized = str(Path(root).resolve())
        if normalized not in self.roots:
            self.roots.append(normalized)
            self._update_roots_label()
            self.roots_changed.emit(self.roots[:])
        if not self._closing:
            self._start_directory_job("upsert", path, normalized)

    def add_root(self, path: str) -> None:
        """添加用户显式选择的下载根目录并持久通知主窗口。"""
        normalized = str(Path(path).resolve())
        if normalized not in self.roots:
            self.roots.append(normalized)
            self.roots_changed.emit(self.roots[:])
            self._update_roots_label()
            logger.info("添加本地作品位置：%s", normalized)
        if any(job.action == "scan" for job in self._jobs):
            self._scan_again = True
        else:
            self.refresh()

    def open_directory(self, path: str, root: str) -> None:
        """在后台读取下载历史目录，完成后打开可选择正文的详情。"""
        if not self._closing:
            self._start_directory_job("open", path, root)

    def _choose_root(self) -> None:
        """通过系统对话框选择下载根目录或单个作品目录。"""
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "导入已有下载目录", self.roots[0] if self.roots else ""
        )
        if path:
            self.add_root(path)

    def _update_roots_label(self) -> None:
        """显示可识别的位置摘要，完整目录通过提示查看。"""
        self.roots_label.setText(
            "\n".join(Path(root).name or root for root in self.roots)
            or "尚未添加作品目录"
        )
        self.roots_label.setToolTip("\n".join(self.roots))

    def _update_authors(self, authors: list[tuple[str, int]]) -> None:
        """更新作者选择而不触发递归查询。"""
        selected = self.author.currentData()
        blocker = QtCore.QSignalBlocker(self.author)
        self.author.clear()
        self.author.addItem("所有作者", "")
        for username, count in authors:
            self.author.addItem(f"@{username}  ·  {count}", username)
        index = self.author.findData(selected)
        if index >= 0:
            self.author.setCurrentIndex(index)
        elif selected:
            self.author.addItem(f"@{selected}  ·  0", selected)
            self.author.setCurrentIndex(self.author.count() - 1)
        del blocker

    def _schedule_search(self, text: str) -> None:
        """在用户暂停输入后才触发索引搜索。"""
        self.search_timer.start()

    def _filters_changed(self, *args: object) -> None:
        """筛选变更时停止旧媒体并从新结果顶部开始阅读。"""
        self._close_player()
        self.new_banner.hide()
        self._reload(False)

    def _set_kind(self, kind: str) -> None:
        """选择单一媒体类型，混合推文可匹配其包含的类型。"""
        self._kind = kind
        self._filters_changed()

    def _date_filter_changed(self, enabled: bool) -> None:
        """启停日期范围控件并刷新当前结果。"""
        self.since.setEnabled(enabled)
        self.until.setEnabled(enabled)
        self._filters_changed()

    def _reset_filters(self) -> None:
        """一次性清空搜索与条件，回到全部作品。"""
        widgets = (self.search, self.author, self.sort, self.use_dates)
        blockers = [QtCore.QSignalBlocker(widget) for widget in widgets]
        self.search.clear()
        self.author.setCurrentIndex(0)
        self.sort.setCurrentIndex(0)
        self.use_dates.setChecked(False)
        self.since.setEnabled(False)
        self.until.setEnabled(False)
        self._kind = ""
        self.kind_buttons[""].setChecked(True)
        del blockers
        self._filters_changed()

    def _show_new(self) -> None:
        """用户主动点击新作品提示后刷新并回到推文流顶部。"""
        self.new_banner.hide()
        self._close_player()
        self._reload(False)

    def _scan_progress(self, count: int, path: str) -> None:
        """显示已扫描数量而不改变当前阅读内容。"""
        self.feedback.setText(f"正在同步作品目录 · 已检查 {count:,} 条")
        self.feedback.setToolTip(path)

    def _cancel_scan(self) -> None:
        """请求安全中断扫描，已写入的目录记录仍然保留。"""
        for job in self._jobs:
            if job.action == "scan":
                job.requestInterruption()
        self.feedback.setText("正在停止扫描，已导入的作品会保留")

    def _show_error(self, message: str) -> None:
        """使用页面内提示展示可恢复错误，避免弹窗打断阅读。"""
        if not self._closing:
            self.feedback.setText(message)
            self.feedback.show()
            self.status_changed.emit(message)

    def _open_detail(self, work: LibraryWork) -> None:
        """打开可选择文字的详情，保持时间线本身的阅读锚点。"""
        self.pause_playback()
        dialog = TweetDetail(work, self)
        dialog.media_requested.connect(self._open_media)
        self._show_dialog(dialog)

    def _show_dialog(self, dialog: QtWidgets.QDialog) -> None:
        """保留非模态窗口引用，并在关闭时恢复列表焦点。"""
        self._dialogs.append(dialog)
        dialog.finished.connect(
            lambda result, current=dialog: self._dialog_finished(current)
        )
        dialog.show()

    def _dialog_finished(self, dialog: QtWidgets.QDialog) -> None:
        """撤下已关闭预览的引用，不重新加载或滚动推文列表。"""
        if dialog in self._dialogs:
            self._dialogs.remove(dialog)
        self.view.setFocus()

    def _open_media(self, work: LibraryWork, index: int) -> None:
        """根据媒体类型打开大图或挂载唯一的内嵌视频组件。"""
        if not 0 <= index < len(work.media):
            return
        media = work.media[index]
        if media.media_type == "image":
            self.pause_playback()
            self._show_dialog(ImageViewer(work.media, index, self))
            return
        self._close_player()
        self._active_media = (work.key, index)
        row = self.view.timeline_model.row_for_key(work.key)
        if row >= 0:
            self.view.scrollTo(
                self.view.timeline_model.index(row, 0),
                QtWidgets.QAbstractItemView.EnsureVisible,
            )
        self.player.play_path(media.path)
        rect = self.view.media_rect(*self._active_media)
        viewport = self.view.viewport().rect()
        # 混合拼图和额外媒体可能没有足够大的内嵌区域，仍复用同一个播放器。
        if (
            rect.width() < 480
            or rect.height() < 190
            or rect.height() > viewport.height()
        ):
            self._show_video_window()
        elif not viewport.contains(rect):
            delta = (
                rect.bottom() - viewport.bottom()
                if rect.bottom() > viewport.bottom()
                else rect.top()
            )
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.value() + delta)
        self._position_player()
        self.player.setFocus()

    def _show_video_window(self) -> None:
        """在小拼图或短视口下给唯一播放器提供完整可操作空间。"""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("本地视频 · 关闭返回推文流")
        dialog.resize(900, 620)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.player)
        self._video_dialog = dialog
        dialog.finished.connect(self._video_window_closed)
        dialog.show()
        self.player.show()

    def _video_window_closed(self, result: int) -> None:
        """关闭视频宿主后把同一个播放器移回时间线并停止解码。"""
        dialog = self._video_dialog
        self._video_dialog = None
        self._active_media = None
        self.player.stop()
        self.player.setParent(self.view.viewport())
        self.player.hide()
        if dialog is not None:
            dialog.deleteLater()
        self.view.setFocus()

    def _position_player(self) -> None:
        """跟踪活动媒体区域，仅在其完整可见时显示真实视频表面。"""
        if (
            self._active_media is None
            or self.player.is_fullscreen()
            or self._video_dialog is not None
        ):
            return
        rect = self.view.media_rect(*self._active_media)
        viewport = self.view.viewport().rect()
        if rect.isEmpty() or not viewport.contains(rect) or not self.isVisible():
            self.player.pause()
            self.player.hide()
            return
        self.player.setGeometry(rect)
        self.player.show()
        self.player.raise_()

    def _close_player(self) -> None:
        """关闭活动媒体并恢复其静态封面。"""
        self._active_media = None
        if self._video_dialog is not None:
            self._video_dialog.close()
        self.player.stop()
        self.view.viewport().update()

    def pause_playback(self) -> None:
        """页面切换或打开预览前暂停当前视频。"""
        self.player.pause()

    def _show_menu(self, work: LibraryWork, position: QtCore.QPoint) -> None:
        """展示与当前作品关联的显式复制、读取及系统打开操作。"""
        menu = QtWidgets.QMenu(self)
        details = menu.addAction("阅读完整正文")
        copy = menu.addAction("复制正文")
        link = menu.addAction("复制原帖链接")
        original = menu.addAction("在浏览器中打开原帖")
        original.setEnabled(QtCore.QUrl(work.url).scheme() in ("http", "https"))
        folder = menu.addAction("打开所在文件夹")
        action = menu.exec(position)
        if action == details:
            self._open_detail(work)
        elif action == copy:
            QtWidgets.QApplication.clipboard().setText(
                "\n\n".join((work.text, *work.article_blocks)).strip()
            )
        elif action == link:
            QtWidgets.QApplication.clipboard().setText(work.url)
        elif action == original:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(work.url))
        elif action == folder:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(work.directory))

    def _show_filters(self) -> None:
        """紧凑窗口中用同一组控件弹出筛选，避免两套状态不一致。"""
        if self._filters_dialog is not None:
            self._filters_dialog.raise_()
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("筛选本地作品")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self.right_rail)
        self.right_rail.show()
        close = QtWidgets.QPushButton("完成")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        self._filters_dialog = dialog
        dialog.finished.connect(self._restore_filters)
        dialog.show()

    def _restore_filters(self, result: int) -> None:
        """筛选弹窗关闭后把侧栏放回页面，按当前宽度决定显隐。"""
        dialog = self._filters_dialog
        self._filters_dialog = None
        self.columns.insertWidget(1, self.right_rail)
        self._update_responsive()
        if dialog is not None:
            dialog.deleteLater()

    def _update_responsive(self) -> None:
        """根据内容区宽度折叠辅助侧栏，确保正文始终有阅读空间。"""
        if self._filters_dialog is None:
            wide = self.width() >= 920
            self.right_rail.setVisible(wide)
            self.filter_button.setVisible(not wide)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """窗口变化时更新辅助布局及活动视频位置。"""
        super().resizeEvent(event)
        if hasattr(self, "right_rail"):
            self._update_responsive()
            QtCore.QTimer.singleShot(0, self._position_player)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        """页面隐藏时强制暂停，避免后台继续发声。"""
        self.pause_playback()
        super().hideEvent(event)

    def shutdown(self) -> bool:
        """停止并等待页面拥有的有界工作，防止运行中销毁线程。"""
        self._closing = True
        self.search_timer.stop()
        self._pending_query = None
        self._pending_open = None
        self._pending_upserts.clear()
        self._close_player()
        if self._filters_dialog is not None:
            self._filters_dialog.close()
        for dialog in self._dialogs[:]:
            dialog.close()
        for job in self._jobs:
            job.requestInterruption()
        for job in self._jobs:
            job.wait()
        self.thumbnails.close()
        return True

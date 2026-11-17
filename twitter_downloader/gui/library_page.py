"""作品库页面：后台索引、筛选搜索、推文流及唯一媒体播放器。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from twitter_downloader.library.models import (
    BookmarkFolder,
    LibraryQuery,
    LibraryWork,
    PersonalWorkState,
)
from twitter_downloader.library.personal_state import DEFAULT_FOLDER_ID
from twitter_downloader.library.reader import read_work
from twitter_downloader.library.repository import LibraryRepository
from twitter_downloader.library.scanner import LibraryScanner

from .bookmark_folders import BookmarkFolderManager, BookmarkFolderPicker
from .image_viewer import ImageViewer
from .personal_state_controller import PersonalStateController
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
        personal_path: Path | None = None,
        reconcile: bool = False,
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
        self.personal_path = personal_path  # 已初始化的独立个人数据库，仅供查询附加。
        self.reconcile = reconcile  # 个人列表变更后采用按键合并，避免重置整个模型。
        self._last_progress: float = 0  # 扫描进度节流时间。

    def run(self) -> None:
        """执行扫描或查询，确保连接在同一工作线程内创建和关闭。"""
        repository: LibraryRepository | None = None
        try:
            repository = LibraryRepository(self.db_path, self.personal_path)
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
                if (
                    self.action == "open"
                    and self.personal_path is not None
                    and not repository.personal_error
                ):
                    payload["states"] = repository.personal_states([work.key])
            else:
                works = repository.query(
                    self.query, offset=self.offset, limit=self.limit
                )
                payload.update(
                    works=works,
                    total=repository.count(self.query),
                    states=repository.personal_states([work.key for work in works])
                    if self.personal_path is not None and not repository.personal_error
                    else {},
                    personal_error=repository.personal_error,
                    authors=repository.authors() if self.offset == 0 else None,
                    offset=self.offset,
                    preserve_anchor=self.preserve_anchor,
                    reconcile=self.reconcile,
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
        *,
        personal_path: Path | None = None,
    ) -> None:
        """建立轻量界面，并延迟启动索引读取与历史作品扫描。"""
        super().__init__(parent)
        self.roots = list(
            dict.fromkeys(str(Path(root).resolve()) for root in roots)
        )  # 去重后的作品位置。
        self.db_path = db_path  # 可随时重建的目录索引。
        self.personal_path = personal_path or db_path.with_name(
            "personal.sqlite3"
        )  # 持久个人数据，与索引独立。
        self._scope = "all"  # 当前作品范围：全部、喜欢或收藏。
        self._folder_id = ""  # 当前收藏夹；空值表示全部收藏。
        self._last_folder_id = ""  # 在导航间切换时保留最后查看的收藏夹。
        self._scope_sessions: dict[
            tuple[str, str], tuple[LibraryQuery, tuple[str, int] | None, int]
        ] = {}  # 各列表的会话筛选、像素锚点和已加载区间。
        self._scope_anchor: tuple[str, int] | None = (
            None  # 等待当前范围查询完成后恢复的锚点。
        )
        self._picker_saves: dict[
            str, BookmarkFolderPicker
        ] = {}  # 等待归属事务确认的窗口。
        self._folder_create_target: BookmarkFolderPicker | None = (
            None  # 本次创建收藏夹的选择窗口。
        )
        self._jobs: list[_LibraryJob] = []  # 页面拥有的后台线程。
        self._closing: bool = False  # 阻止退出过程创建新工作。
        self._query_busy: bool = False  # 查询串行化，避免输入洪泛。
        self._pending_query: dict[str, Any] | None = None  # 只保留最后一次待查询请求。
        self._generation: int = 0  # 当前筛选版本。
        self._personal_query_failed = False  # 读取错误独立于写入失败，重试时重新查询。
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
        self.personal_timer = QtCore.QTimer(self)
        self.personal_timer.setSingleShot(True)
        self.personal_timer.setInterval(
            40
        )  # 合并连续确认的个人动作，只重查一次当前范围。
        self.personal_timer.timeout.connect(self._reconcile_personal)
        self.personal = PersonalStateController(self.personal_path, self)
        self.personal.ready.connect(self._personal_ready)
        self.personal.state_changed.connect(self._personal_state_changed)
        self.personal.folders_changed.connect(self._folders_changed)
        self.personal.operation_finished.connect(self._personal_operation_finished)
        self.personal.operation_failed.connect(self._personal_operation_failed)
        self.personal.failed.connect(self._personal_failed)
        self.personal.busy_changed.connect(self._personal_busy_changed)
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
        self.title = QtWidgets.QLabel("作品库")
        self.title.setObjectName("pageTitle")
        titles.addWidget(self.title)
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
        self.folder_bar = QtWidgets.QWidget()
        folder_layout = QtWidgets.QHBoxLayout(self.folder_bar)
        folder_layout.setContentsMargins(18, 0, 18, 12)
        self.folder_selector = QtWidgets.QComboBox()
        self.folder_selector.setAccessibleName("查看收藏夹")
        self.folder_selector.addItem("全部收藏", "")
        self.folder_selector.currentIndexChanged.connect(self._folder_selected)
        folder_layout.addWidget(self.folder_selector, 1)
        self.manage_folders = QtWidgets.QPushButton("管理收藏夹")
        self.manage_folders.setObjectName("secondary")
        self.manage_folders.clicked.connect(self._open_folder_manager)
        folder_layout.addWidget(self.manage_folders)
        self.folder_bar.hide()
        center_layout.addWidget(self.folder_bar)
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
        self.personal_retry = QtWidgets.QPushButton("重试个人数据")
        self.personal_retry.setObjectName("secondary")
        self.personal_retry.clicked.connect(self._retry_personal)
        self.personal_retry.hide()
        center_layout.addWidget(self.personal_retry)
        self.content = QtWidgets.QStackedWidget()
        self.view = TimelineView(self.thumbnails)
        self.view.setObjectName("tweetTimeline")
        self.view.setAccessibleName("本地推文时间线")
        self.view.open_detail.connect(self._open_detail)
        self.view.open_media.connect(self._open_media)
        self.view.menu_requested.connect(self._show_menu)
        self.view.like_requested.connect(self._toggle_like)
        self.view.bookmark_requested.connect(self._open_bookmark_picker)
        self.view.visible_changed.connect(self._position_player)
        self.view.near_end.connect(self._load_more)
        self.content.addWidget(self.view)
        self.empty = QtWidgets.QWidget()
        empty_layout = QtWidgets.QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(30, 40, 30, 40)
        empty_layout.addStretch(1)
        self.empty_title = QtWidgets.QLabel("你的作品库，从这里开始")
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
        rail_title = QtWidgets.QLabel("重温本地作品")
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
        offline = QtWidgets.QLabel("喜欢与收藏仅保存在本地\n原帖计数为下载时的记录")
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
            scope=self._scope,
            folder_id=self._folder_id,
        )

    def set_scope(self, scope: str, folder_id: str | None = None) -> None:
        """在同一阅读器内切换作品范围，恢复各列表的筛选和阅读位置。"""
        if scope not in ("all", "liked", "bookmarked"):
            raise ValueError("未知的作品列表范围")
        target = (
            (folder_id if folder_id is not None else self._last_folder_id)
            if scope == "bookmarked"
            else ""
        )
        if (scope, target) == (self._scope, self._folder_id):
            return
        self.search_timer.stop()
        self.personal_timer.stop()
        self._scope_sessions[(self._scope, self._folder_id)] = (
            self._query(),
            self.view.capture_anchor(),
            max(PAGE_SIZE, self.view.timeline_model.rowCount()),
        )
        self.pause_playback()
        self._scope, self._folder_id = scope, target
        if scope == "bookmarked":
            self._last_folder_id = target
        session = self._scope_sessions.get((scope, target))
        query = (
            session[0]
            if session
            else LibraryQuery(
                scope=scope,
                folder_id=target,
                sort="newest" if scope == "all" else "recent",
            )
        )
        widgets = (
            self.search,
            self.author,
            self.sort,
            self.use_dates,
            self.since,
            self.until,
            self.folder_selector,
        )
        blockers = [QtCore.QSignalBlocker(widget) for widget in widgets]
        self.search.setText(query.search)
        author_index = self.author.findData(query.author)
        if author_index < 0:
            self.author.addItem(f"@{query.author}", query.author)
            author_index = self.author.count() - 1
        self.author.setCurrentIndex(author_index)
        self.sort.clear()
        if scope != "all":
            label = (
                "最近喜欢优先"
                if scope == "liked"
                else ("最近加入优先" if target else "最近收藏优先")
            )
            self.sort.addItem(label, "recent")
        self.sort.addItem("最新发布优先", "newest")
        self.sort.addItem("最早发布优先", "oldest")
        self.sort.setCurrentIndex(max(0, self.sort.findData(query.sort)))
        self._kind = query.kind
        self.kind_buttons[query.kind].setChecked(True)
        self.use_dates.setChecked(bool(query.since or query.until))
        for edit, value in ((self.since, query.since), (self.until, query.until)):
            if value:
                edit.setDate(QtCore.QDate.fromString(value, "yyyy-MM-dd"))
            edit.setEnabled(self.use_dates.isChecked())
        self.folder_selector.setCurrentIndex(
            max(0, self.folder_selector.findData(target))
        )
        del blockers
        self.title.setText(
            {"all": "作品库", "liked": "喜欢", "bookmarked": "收藏"}[scope]
        )
        self.view.setAccessibleName(f"{self.title.text()}推文时间线")
        self.folder_bar.setVisible(scope == "bookmarked")
        self.new_banner.hide()
        self._scope_anchor = session[1] if session else None
        self._generation += 1
        self._pending_query = None
        self._request_query(0, session[2] if session else PAGE_SIZE, False)

    def _folder_selected(self, index: int) -> None:
        """把收藏夹选择映射为独立的查询范围。"""
        if index >= 0:
            self.set_scope(
                "bookmarked", str(self.folder_selector.itemData(index) or "")
            )

    def _update_empty_state(self) -> None:
        """按照当前范围显示数量和空态，避免把喜欢与普通作品库混淆。"""
        self.count_label.setText(f"{self._total:,} 条作品 · 本地离线阅读")
        self.content.setCurrentWidget(self.view if self._total else self.empty)
        filtered = bool(
            self.search.text()
            or self._kind
            or self.author.currentData()
            or self.use_dates.isChecked()
        )
        title, help_text = {
            "all": (
                "你的作品库，从这里开始",
                "导入已有下载目录，或下载第一条推文。\n文字、图片和视频将在这里一起呈现。",
            ),
            "liked": ("还没有喜欢的作品", "在推文下方点击喜欢，即可在这里重温。"),
            "bookmarked": (
                "这个收藏夹还是空的" if self._folder_id else "还没有收藏的作品",
                "点击推文下方的收藏，可将作品加入一个或多个收藏夹。",
            ),
        }[self._scope]
        self.empty_title.setText("没有找到匹配的作品" if filtered else title)
        self.empty_help.setText(
            "试试其他关键词，或重置筛选条件。" if filtered else help_text
        )

    def _show_personal_unavailable(self) -> None:
        """个人数据库失败时明确显示不可用，不能冒充零条收藏。"""
        # 丢弃报错前已经发出的快照，避免在途成功结果把错误态重新填成旧列表。
        self._generation += 1
        self._pending_query = None
        self._personal_query_failed = True
        self.personal_timer.stop()
        self.view.set_works([], False)
        self.count_label.setText("个人数据暂不可用")
        self.content.setCurrentWidget(self.empty)
        self.empty_title.setText("暂时无法读取喜欢与收藏")
        self.empty_help.setText(
            "个人记录已保留。请查看错误提示并重试；仍可在作品库阅读本地文件。"
        )
        self._sync_personal_retry()
        self._position_player()

    def _personal_ready(self, folders: tuple[BookmarkFolder, ...]) -> None:
        """个人存储准备完成后接入筛选与当前页状态。"""
        if self._closing:
            return
        self._sync_personal_retry()
        self._folders_changed(folders)
        self._reload(True, reconcile=True)

    def _personal_state_changed(self, state: PersonalWorkState) -> None:
        """同步时间线和所有打开的详情，不重新解码媒体或重置模型。"""
        if self._closing:
            return
        pending = self.personal.is_pending(state.work_key)
        self.view.set_personal_state(state, pending)
        for dialog in self._dialogs:
            if isinstance(dialog, TweetDetail) and dialog.work.key == state.work_key:
                dialog.set_personal_state(state, pending)

    def _matches_personal_scope(self, state: PersonalWorkState) -> bool:
        """判断确认后的状态是否仍属于当前个人列表。"""
        if self._scope == "liked":
            return state.liked
        if self._scope == "bookmarked":
            return (
                self._folder_id in state.folder_ids
                if self._folder_id
                else state.bookmarked
            )
        return True

    def _personal_operation_finished(self, action: str, result: object) -> None:
        """事务确认后关闭保存窗口并按键校正当前个人列表的分页。"""
        if self._closing:
            return
        self._sync_personal_retry()
        if action == "set_folders" and isinstance(result, PersonalWorkState):
            dialog = self._picker_saves.pop(result.work_key, None)
            if dialog is not None and dialog in self._dialogs:
                dialog.accept()
        if action == "create_folder" and isinstance(result, BookmarkFolder):
            dialog = self._folder_create_target
            self._folder_create_target = None
            if dialog is not None and dialog in self._dialogs:
                dialog.set_folders(self.personal.folders, select_id=result.id)
        if self._scope != "all":
            # 提前使在途查询失效，防止已取消的作品被旧分页快照重新加入。
            self._generation += 1
            self._pending_query = None
            states = (
                [result]
                if isinstance(result, PersonalWorkState)
                else list(result.values())
                if isinstance(result, dict)
                else []
            )
            for state in states:
                if (
                    isinstance(state, PersonalWorkState)
                    and not self._matches_personal_scope(
                        self.personal.state_for(state.work_key)
                    )
                    and self.view.remove_work(state.work_key)
                ):
                    self._total = max(0, self._total - 1)
            self._update_empty_state()
            self._position_player()
            self.personal_timer.start()

    def _reconcile_personal(self) -> None:
        """从当前已加载区间重读并差分合并，修正移除造成的 OFFSET 位移。"""
        if not self._closing and self._scope != "all":
            self._reload(True, reconcile=True)

    def _folders_changed(self, folders: tuple[BookmarkFolder, ...]) -> None:
        """同步收藏夹标签和所有打开的选择器，删除当前夹时返回全部收藏。"""
        if self._closing:
            return
        ids = {folder.id for folder in folders}
        blocker = QtCore.QSignalBlocker(self.folder_selector)
        self.folder_selector.clear()
        self.folder_selector.addItem("全部收藏", "")
        for folder in folders:
            self.folder_selector.addItem(folder.name, folder.id)
        self.folder_selector.setCurrentIndex(
            max(0, self.folder_selector.findData(self._folder_id))
        )
        del blocker
        for dialog in self._dialogs:
            if isinstance(dialog, (BookmarkFolderPicker, BookmarkFolderManager)):
                dialog.set_folders(folders)
                dialog.set_busy(self.personal.busy)
        if self._last_folder_id and self._last_folder_id not in ids:
            self._last_folder_id = ""
        if self._folder_id and self._folder_id not in ids:
            self.set_scope("bookmarked", "")

    def _personal_busy_changed(self, busy: bool) -> None:
        """异步保存期间锁定收藏夹表单，避免同一窗口重复提交。"""
        self._sync_personal_retry()
        for dialog in self._dialogs:
            if isinstance(dialog, (BookmarkFolderPicker, BookmarkFolderManager)):
                dialog.set_busy(busy)

    def _personal_operation_failed(self, action: str, key: str) -> None:
        """只清除失败请求关联，保留其他已接受事务的表单确认关系。"""
        if action == "set_folders":
            self._picker_saves.pop(key, None)
        elif action == "create_folder":
            self._folder_create_target = None

    def _sync_personal_retry(self) -> None:
        """区分重开数据库、重试保存和重读列表，并在写入期间禁用重试。"""
        write_failed = self.personal.has_failed_operation and self.personal.available
        self.personal_retry.setText(
            "重试未保存操作" if write_failed else "重试个人数据"
        )
        self.personal_retry.setVisible(
            write_failed
            or self._personal_query_failed
            or (not self.personal.available and bool(self.personal.last_error))
        )
        self.personal_retry.setEnabled(not self.personal.busy and not self._closing)

    def _personal_failed(self, message: str) -> None:
        """显示可重试的中文错误，并让表单保留未保存的选择。"""
        self._show_error(message)
        for dialog in self._dialogs:
            if isinstance(dialog, (BookmarkFolderPicker, BookmarkFolderManager)):
                dialog.show_error(message, busy=self.personal.busy)
        self._sync_personal_retry()
        if not self.personal.available and self._scope != "all":
            self._show_personal_unavailable()

    def _retry_personal(self) -> None:
        """重试失败写入或初始化；只有读取失败时重新查询当前范围。"""
        if self.personal.busy or self._closing:
            return
        accepted = self.personal.retry()
        if not accepted and self.personal.available:
            self._reload(True, reconcile=True)
        self._sync_personal_retry()

    def _toggle_like(self, work: LibraryWork) -> None:
        """提交本地喜欢的目标状态，原帖点赞数保持下载时快照。"""
        if not self.personal.available:
            self._personal_failed("喜欢与收藏暂不可用，请重试个人数据。")
            return
        state = self.personal.state_for(work.key)
        self.personal.set_liked(work.key, not state.liked)

    def _open_bookmark_picker(self, work: LibraryWork) -> None:
        """打开多收藏夹选择器，同一作品只保留一个整理窗口。"""
        if not self.personal.available:
            self._personal_failed("喜欢与收藏暂不可用，请重试个人数据。")
            return
        for dialog in self._dialogs:
            if (
                isinstance(dialog, BookmarkFolderPicker)
                and dialog.property("work_key") == work.key
            ):
                dialog.raise_()
                dialog.activateWindow()
                return
        state = self.personal.state_for(work.key)
        dialog = BookmarkFolderPicker(
            self.personal.folders,
            state.folder_ids or (DEFAULT_FOLDER_ID,),
            self,
            collected=state.bookmarked,
        )
        dialog.setProperty("work_key", work.key)
        dialog.saved.connect(
            lambda ids, current=dialog: self._save_picker(work.key, current, ids)
        )
        dialog.cancel_all_requested.connect(
            lambda: self._save_picker(work.key, dialog, ())
        )
        dialog.create_requested.connect(
            lambda name, current=dialog: self._create_folder_from_picker(current, name)
        )
        dialog.set_busy(self.personal.busy)
        self._show_dialog(dialog)

    def _save_picker(
        self, key: str, dialog: BookmarkFolderPicker, ids: tuple[str, ...]
    ) -> None:
        """在一次事务中替换完整归属集合，确认后才关闭选择器。"""
        dialog.set_busy(True)
        if self.personal.set_folders(key, ids):
            self._picker_saves[key] = dialog
        else:
            dialog.set_busy(self.personal.busy)

    def _create_folder_from_picker(
        self, dialog: BookmarkFolderPicker, name: str
    ) -> None:
        """创建后仅在发起请求的选择器中勾选新收藏夹。"""
        dialog.set_busy(True)
        if self.personal.create_folder(name):
            self._folder_create_target = dialog
        else:
            dialog.set_busy(self.personal.busy)

    def _open_folder_manager(self) -> None:
        """复用后台控制器管理收藏夹，不在界面线程执行数据库操作。"""
        if not self.personal.available:
            self._personal_failed("收藏夹暂不可用，请重试个人数据。")
            return
        for dialog in self._dialogs:
            if isinstance(dialog, BookmarkFolderManager):
                dialog.raise_()
                return
        dialog = BookmarkFolderManager(self.personal.folders, self)
        dialog.create_requested.connect(self.personal.create_folder)
        dialog.rename_requested.connect(self.personal.rename_folder)
        dialog.delete_requested.connect(self.personal.delete_folder)
        dialog.set_busy(self.personal.busy)
        self._show_dialog(dialog)

    def _job_failed(self, job: _LibraryJob, message: str) -> None:
        """忽略过期查询错误，当前个人范围失败时展示明确错误态。"""
        if job.action == "query":
            if job.generation != self._generation:
                return
            if self._scope != "all":
                self._show_personal_unavailable()
        self._show_error(message)

    def _start_job(self, job: _LibraryJob) -> None:
        """统一保留线程引用并连接失败、结果与清理入口。"""
        self._jobs.append(job)
        job.result.connect(self._job_result)
        job.failure.connect(
            lambda message, current=job: self._job_failed(current, message)
        )
        job.progress.connect(self._scan_progress)
        job.finished.connect(lambda current=job: self._job_finished(current))
        job.start()

    def _request_query(
        self, offset: int, limit: int, preserve_anchor: bool, reconcile: bool = False
    ) -> None:
        """串行化索引读取，只保留最新待处理搜索，丢弃旧版本结果。"""
        if self._closing:
            return
        if self._scope != "all" and not self.personal.available:
            self._show_personal_unavailable()
            return
        params = {
            "query": self._query(),
            "offset": offset,
            "limit": limit,
            "generation": self._generation,
            "preserve_anchor": preserve_anchor,
            "personal_path": self.personal_path if self.personal.available else None,
            "reconcile": reconcile,
        }
        if self._query_busy:
            self._pending_query = params
            return
        self._query_busy = True
        self._start_job(_LibraryJob("query", self.db_path, self, **params))

    def _reload(self, preserve_anchor: bool = True, *, reconcile: bool = False) -> None:
        """重新读取已加载区间，在数据仍存在时保持可见推文锚点。"""
        self._generation += 1
        count = (
            max(PAGE_SIZE, self.view.timeline_model.rowCount())
            if preserve_anchor
            else PAGE_SIZE
        )
        self._request_query(0, count, preserve_anchor, reconcile)

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
                self.personal.hydrate(payload.get("states", {}))
                self._open_detail(payload["work"])
            if self.view.verticalScrollBar().value() <= 24:
                self._reload()
            else:
                self.new_banner.show()
        elif payload["generation"] == self._generation:
            self._total = payload["total"]
            self.personal.hydrate(payload.get("states", {}))
            if payload["offset"]:
                self.view.append_works(payload["works"])
            elif payload.get("reconcile"):
                self.view.sync_works(
                    payload["works"], preserve_anchor=payload["preserve_anchor"]
                )
            else:
                self.view.set_works(
                    payload["works"], preserve_anchor=payload["preserve_anchor"]
                )
            for work in payload["works"]:
                self.view.set_personal_state(
                    self.personal.state_for(work.key),
                    self.personal.is_pending(work.key),
                )
            if self._scope_anchor is not None:
                self.view.restore_anchor(self._scope_anchor)
                self._scope_anchor = None
            self._update_empty_state()
            if payload.get("personal_error"):
                self._personal_query_failed = True
                self._show_error(f"个人数据读取失败：{payload['personal_error']}")
            else:
                self._personal_query_failed = False
            self._sync_personal_retry()
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
            _LibraryJob(
                action,
                self.db_path,
                self,
                directory=path,
                root=root,
                personal_path=self.personal_path if self.personal.available else None,
            )
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
        dialog.like_requested.connect(self._toggle_like)
        dialog.bookmark_requested.connect(self._open_bookmark_picker)
        dialog.set_personal_state(
            self.personal.state_for(work.key), self.personal.is_pending(work.key)
        )
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
        if self._active_media is None:
            return
        # 所属作品移除或页面隐藏时，无论视频处于内嵌、弹窗还是全屏均必须暂停。
        if (
            self.view.timeline_model.row_for_key(self._active_media[0]) < 0
            or not self.isVisible()
        ):
            self.player.pause()
            if not self.player.is_fullscreen() and self._video_dialog is None:
                self.player.hide()
            return
        if self.player.is_fullscreen() or self._video_dialog is not None:
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
        state = self.personal.state_for(work.key)
        menu.addSeparator()
        like = menu.addAction("取消喜欢" if state.liked else "喜欢这条作品")
        like.setEnabled(self.personal.available)
        bookmark = menu.addAction("整理收藏夹" if state.bookmarked else "加入收藏夹")
        bookmark.setEnabled(self.personal.available)
        remove = None
        if self._folder_id and self._folder_id in state.folder_ids:
            remove = menu.addAction(f"从【{self.folder_selector.currentText()}】移除")
        cancel_all = menu.addAction("取消全部收藏") if state.bookmarked else None
        menu.addSeparator()
        copy = menu.addAction("复制正文")
        link = menu.addAction("复制原帖链接")
        original = menu.addAction("在浏览器中打开原帖")
        original.setEnabled(QtCore.QUrl(work.url).scheme() in ("http", "https"))
        folder = menu.addAction("打开所在文件夹")
        action = menu.exec(position)
        if action == details:
            self._open_detail(work)
        elif action == like:
            self._toggle_like(work)
        elif action == bookmark:
            self._open_bookmark_picker(work)
        elif remove is not None and action == remove:
            self.personal.set_folders(
                work.key,
                tuple(key for key in state.folder_ids if key != self._folder_id),
            )
        elif cancel_all is not None and action == cancel_all:
            self.personal.set_folders(work.key, ())
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
        self.personal_timer.stop()
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
        if not self.personal.shutdown():
            self._closing = False
            self._sync_personal_retry()
            self._show_error(
                "部分个人操作未保存，请重试后关闭。作品文件与已确认记录已保留。"
            )
            return False
        self.thumbnails.close()
        return True

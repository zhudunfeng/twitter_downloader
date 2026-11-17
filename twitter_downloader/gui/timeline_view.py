"""支持变高推文、分页信号及像素阅读锚点的单列列表。"""

from __future__ import annotations

import logging

from PySide6 import QtCore, QtGui, QtWidgets

from twitter_downloader.library.models import LibraryWork

from .thumbnail_service import ThumbnailService
from .timeline_model import TimelineModel
from .tweet_delegate import TweetDelegate

logger = logging.getLogger(__name__)
LAYOUT_BATCH_SIZE = 48  # 每次界面布局处理的行数，减少大列表首次布局阻塞。
FETCH_MARGIN = 650  # 距离列表底端的预取距离，单位为逻辑像素。


class TimelineView(QtWidgets.QListView):
    """以模型和委托绘制推文，并为唯一视频覆盖层提供精确区域。"""

    open_detail = QtCore.Signal(object)  # 以当前作品打开可选择、复制的详情。
    open_media = QtCore.Signal(object, int)  # 作品及原始媒体序号。
    menu_requested = QtCore.Signal(object, QtCore.QPoint)  # 作品及菜单的全局位置。
    visible_changed = QtCore.Signal()  # 滚动、换宽或布局后通知活动视频定位。
    near_end = QtCore.Signal()  # 视口接近当前已加载数据的尾端。

    def __init__(
        self, thumbnails: ThumbnailService, parent: QtWidgets.QWidget | None = None
    ) -> None:
        """建立有界批量布局、逐像素滚动及共用的推文委托。"""
        super().__init__(parent)
        self.timeline_model = TimelineModel(self)  # 当前查询的内存分页模型。
        self.tweet_delegate = TweetDelegate(thumbnails, self)  # 不创建行控件的绘制器。
        self._pressed_key = ""  # 鼠标按下时的稳定作品主键，防止刷新后误激活。
        self._pressed_position = QtCore.QPoint()  # 用于区分拖动与普通单击。
        self._pending_anchor: tuple[str, int] | None = (
            None  # 待布局后恢复的作品和像素偏移。
        )
        self._restore_attempts = 0  # 批量布局期间的有界锚点重试次数。
        self._restore_timer = QtCore.QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self._apply_pending_anchor)
        self._fetch_timer = QtCore.QTimer(self)
        self._fetch_timer.setSingleShot(True)
        self._fetch_timer.timeout.connect(self._check_near_end)
        self.setModel(self.timeline_model)
        self.setItemDelegate(self.tweet_delegate)
        self.setViewMode(QtWidgets.QListView.ViewMode.ListMode)
        self.setFlow(QtWidgets.QListView.Flow.TopToBottom)
        self.setWrapping(False)
        self.setUniformItemSizes(False)
        self.setLayoutMode(QtWidgets.QListView.LayoutMode.Batched)
        self.setBatchSize(LAYOUT_BATCH_SIZE)
        self.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setSpacing(0)
        self.setMouseTracking(True)
        self.setWordWrap(False)
        self.setAccessibleName("本地推文时间线")
        self.setAccessibleDescription(
            "上下键选择推文；回车阅读详情；空格打开第一项媒体；菜单键显示更多操作。"
        )
        self.viewport().setAutoFillBackground(True)
        palette = self.viewport().palette()
        palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#0f1115"))
        self.viewport().setPalette(palette)
        self.verticalScrollBar().rangeChanged.connect(self._scroll_range_changed)
        thumbnails.ready.connect(self._thumbnail_ready)
        self.tweet_delegate.set_viewport_width(self.viewport().width())

    def set_works(self, works: list[LibraryWork], preserve_anchor: bool = True) -> None:
        """替换查询结果，在作品仍存在时保持焦点与可见像素位置。"""
        anchor = self.capture_anchor() if preserve_anchor else None
        current = self.timeline_model.work_at(self.currentIndex().row())
        current_key = current.key if current is not None else ""
        self._restore_timer.stop()
        self._pending_anchor = None
        self.tweet_delegate.invalidate_layouts()
        self.timeline_model.set_works(works)
        self.tweet_delegate.set_viewport_width(self.viewport().width())
        self.doItemsLayout()
        row = self.timeline_model.row_for_key(current_key)
        if row >= 0:
            self.setCurrentIndex(self.timeline_model.index(row, 0))
        if anchor is not None and self.timeline_model.row_for_key(anchor[0]) >= 0:
            self.restore_anchor(anchor)
        else:
            self.scroll_to_top()
        self.viewport().update()
        self.visible_changed.emit()
        self._fetch_timer.start(0)

    def append_works(self, works: list[LibraryWork]) -> None:
        """追加去重后的下一页数据，并保留当前阅读推文及偏移。"""
        anchor = self.capture_anchor()
        self.timeline_model.append_works(works)
        if anchor is not None:
            self.restore_anchor(anchor)
        self.visible_changed.emit()
        self._fetch_timer.start(0)

    def capture_anchor(self) -> tuple[str, int] | None:
        """取得视口顶部推文的稳定主键与行顶部像素偏移。"""
        index = self.indexAt(QtCore.QPoint(4, 1))
        if not index.isValid():
            return None
        work = self.timeline_model.work_at(index.row())
        return (work.key, self.visualRect(index).top()) if work is not None else None

    def restore_anchor(self, anchor: tuple[str, int] | None) -> None:
        """在批量布局后恢复锚点，避免行高变化把读者送到别处。"""
        self._pending_anchor = anchor
        self._restore_attempts = 0
        if anchor is not None:
            self._restore_timer.start(0)

    @QtCore.Slot()
    def _apply_pending_anchor(self) -> None:
        """按实际几何修正滚动条，并容忍尚未完成的批量布局。"""
        anchor = self._pending_anchor
        if anchor is None:
            return
        row = self.timeline_model.row_for_key(anchor[0])
        if row < 0:
            self._pending_anchor = None
            return
        index = self.timeline_model.index(row, 0)
        self.scrollTo(index, QtWidgets.QAbstractItemView.ScrollHint.PositionAtTop)
        rect = self.visualRect(index)
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.value() + rect.top() - anchor[1])
        self._restore_attempts += 1
        error = abs(self.visualRect(index).top() - anchor[1])
        if error > 1 and self._restore_attempts < 8:
            self._restore_timer.start(16)
        else:
            self._pending_anchor = None
        self.visible_changed.emit()

    def media_rect(self, key: str, media_index: int) -> QtCore.QRect:
        """返回指定原始媒体项在视口中的区域，供唯一播放器覆盖。"""
        row = self.timeline_model.row_for_key(key)
        work = self.timeline_model.work_at(row)
        if work is None:
            return QtCore.QRect()
        row_rect = self.visualRect(self.timeline_model.index(row, 0))
        layout = self.tweet_delegate.layout_for(work, row_rect.width())
        if not 0 <= media_index < len(layout.media):
            return QtCore.QRect()
        return layout.media[media_index].translated(row_rect.topLeft())

    def scroll_to_top(self) -> None:
        """显式回到当前查询顶部，并取消旧的延迟锚点恢复。"""
        self._restore_timer.stop()
        self._pending_anchor = None
        self.scrollToTop()
        self.visible_changed.emit()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """跟随每次滚动立即更新视频区域，再合并分页预取检查。"""
        super().scrollContentsBy(dx, dy)
        self.visible_changed.emit()
        if hasattr(self, "_fetch_timer"):
            self._fetch_timer.start(0)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """换宽时重新测量正文和拼图，并恢复顶部阅读锚点。"""
        anchor = self.capture_anchor() if hasattr(self, "timeline_model") else None
        super().resizeEvent(event)
        if hasattr(self, "tweet_delegate"):
            self.tweet_delegate.set_viewport_width(self.viewport().width())
            self.doItemsLayout()
            self.restore_anchor(anchor)
            self.visible_changed.emit()
            self._fetch_timer.start(0)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        """首次展示时按真实视口宽度排版并检查短列表的预取。"""
        super().showEvent(event)
        self.tweet_delegate.set_viewport_width(self.viewport().width())
        self.doItemsLayout()
        self.visible_changed.emit()
        self._fetch_timer.start(0)

    @QtCore.Slot(int, int)
    def _scroll_range_changed(self, minimum: int, maximum: int) -> None:
        """在批量布局更新滚动范围后检查是否需要下一页。"""
        self.visible_changed.emit()
        self._fetch_timer.start(0)

    @QtCore.Slot()
    def _check_near_end(self) -> None:
        """只在接近当前数据底端时通知上层分页加载。"""
        scrollbar = self.verticalScrollBar()
        if (
            self.timeline_model.rowCount()
            and scrollbar.maximum() - scrollbar.value() <= FETCH_MARGIN
        ):
            self.near_end.emit()

    @QtCore.Slot(str)
    def _thumbnail_ready(self, path: str) -> None:
        """仅重绘当前可见推文，不因预览完成而改变行高或滚动位置。"""
        self.viewport().update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        """记录按下的稳定作品身份，防止拖动和刷新造成误点击。"""
        point = event.position().toPoint()
        index = self.indexAt(point)
        work = self.timeline_model.work_at(index.row()) if index.isValid() else None
        self._pressed_key = work.key if work is not None else ""
        self._pressed_position = point
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        """把单击分派给绘制时共用的命中区域。"""
        super().mouseReleaseEvent(event)
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        point = event.position().toPoint()
        if (
            point - self._pressed_position
        ).manhattanLength() > QtWidgets.QApplication.startDragDistance():
            return
        index = self.indexAt(point)
        work = self.timeline_model.work_at(index.row()) if index.isValid() else None
        if work is None or work.key != self._pressed_key:
            return
        row_rect = self.visualRect(index)
        action, media_index = self.tweet_delegate.hit_test(
            work, point - row_rect.topLeft(), row_rect.width()
        )
        self._activate(work, action, media_index, point)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        """为可打开的推文区域提供清晰的鼠标指针反馈。"""
        super().mouseMoveEvent(event)
        cursor = (
            QtCore.Qt.CursorShape.PointingHandCursor
            if self.indexAt(event.position().toPoint()).isValid()
            else QtCore.Qt.CursorShape.ArrowCursor
        )
        self.viewport().setCursor(cursor)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        """鼠标离开列表后恢复普通指针。"""
        self.viewport().unsetCursor()
        super().leaveEvent(event)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        """右键或键盘菜单统一打开当前作品的次要操作。"""
        index = self.indexAt(event.pos())
        if not index.isValid():
            index = self.currentIndex()
        work = self.timeline_model.work_at(index.row()) if index.isValid() else None
        if work is not None:
            self.setCurrentIndex(index)
            self.menu_requested.emit(work, event.globalPos())
            event.accept()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        """提供详情、媒体、复制与菜单快捷操作，方向键保留原生行为。"""
        index = self.currentIndex()
        work = self.timeline_model.work_at(index.row()) if index.isValid() else None
        if work is not None:
            key = event.key()
            if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                self.open_detail.emit(work)
                event.accept()
                return
            if key == QtCore.Qt.Key.Key_Space:
                if work.media:
                    self.open_media.emit(work, 0)
                else:
                    self.open_detail.emit(work)
                event.accept()
                return
            if event.matches(QtGui.QKeySequence.StandardKey.Copy):
                QtWidgets.QApplication.clipboard().setText(work.text)
                event.accept()
                return
            if key == QtCore.Qt.Key.Key_Menu or (
                key == QtCore.Qt.Key.Key_F10
                and event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
            ):
                point = self.visualRect(index).topRight()
                self.menu_requested.emit(work, self.viewport().mapToGlobal(point))
                event.accept()
                return
        super().keyPressEvent(event)

    def _activate(
        self, work: LibraryWork, action: str, media_index: int, point: QtCore.QPoint
    ) -> None:
        """执行当前作品的明确阅读动作，并在展开时保持阅读位置。"""
        if action == "menu":
            self.menu_requested.emit(work, self.viewport().mapToGlobal(point))
        elif action == "expand":
            anchor = self.capture_anchor()
            self.tweet_delegate.toggle_expanded(work.key)
            row = self.timeline_model.row_for_key(work.key)
            self.tweet_delegate.sizeHintChanged.emit(self.timeline_model.index(row, 0))
            self.doItemsLayout()
            self.restore_anchor(anchor)
            self.visible_changed.emit()
            logger.info(
                "已%s推文正文：%s",
                "展开" if work.key in self.tweet_delegate.expanded_keys else "收起",
                work.tweet_id,
            )
        elif action == "media":
            self.open_media.emit(work, media_index)
        else:
            self.open_detail.emit(work)

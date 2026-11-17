"""以轻量绘制还原单列推文的阅读层级与媒体拼图。"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from PySide6 import QtCore, QtGui, QtWidgets

from twitter_downloader.library.models import LibraryWork, LocalMedia

from .thumbnail_service import ThumbnailService
from .timeline_model import WORK_ROLE

BACKGROUND = QtGui.QColor("#0f1115")  # 时间线默认底色，与应用深色主题一致。
HOVER_BACKGROUND = QtGui.QColor("#141a21")  # 鼠标所在推文的轻量背景反馈。
SELECTED_BACKGROUND = QtGui.QColor("#152231")  # 键盘选中推文的背景反馈。
TEXT_COLOR = QtGui.QColor("#e7e9ea")  # 正文与作者名称的高对比文字色。
MUTED_COLOR = QtGui.QColor("#94a3b1")  # 账号、日期和已保存计数的次要文字色。
LINE_COLOR = QtGui.QColor("#26313d")  # 推文分隔线与媒体边框颜色。
ACCENT_COLOR = QtGui.QColor("#1d9bf0")  # 展开、阅读及键盘焦点的蓝色强调。
MEDIA_BACKGROUND = QtGui.QColor("#1c2732")  # 尚无可用封面时的中性占位色。
LAYOUT_CACHE_LIMIT = 256  # 保留最近布局的条数，限制长列表测量对象占用。
COLLAPSED_TEXT_LIMIT = 176  # 折叠正文的最大逻辑像素高度。
COLLAPSED_PREFIX_LIMIT = 1500  # 折叠时最多测量的字符数量，避免超长文本阻塞。
BODY_LINE_HEIGHT = 23  # 正文固定逻辑行高，约为 15 像素字号的 1.53 倍。


def _font(pixels: int, bold: bool = False) -> QtGui.QFont:
    """创建支持中文回退的逻辑像素字体。"""
    font = QtGui.QFont()
    font.setFamilies(["Segoe UI", "Microsoft YaHei", "sans-serif"])
    font.setPixelSize(pixels)
    font.setBold(bold)
    return font


@dataclass(slots=True)
class TweetLayout:
    """共享绘制、点击命中和视频覆盖层使用的同一套几何信息。"""

    width: int  # 本次布局的视口逻辑宽度。
    height: int  # 整条推文占用的逻辑高度。
    avatar: QtCore.QRect  # 本地作者头像占位区域。
    header: QtCore.QRect  # 作者名称、账号与日期区域。
    menu: QtCore.QRect  # 更多操作的可点击区域。
    text: QtCore.QRect  # 正文显示及裁剪区域。
    document: QtGui.QTextDocument | None  # 已测量的纯文本对象，禁止加载外部资源。
    expand: QtCore.QRect  # 展开或收起全文的可点击区域。
    article: QtCore.QRect  # 已保存文章或来源链接的详情入口。
    media: tuple[QtCore.QRect, ...]  # 原始顺序前四项媒体的内嵌区域。
    warning: QtCore.QRect  # 不完整作品提示区域。
    footer: QtCore.QRect  # 保存计数及详情入口区域。


class TweetDelegate(QtWidgets.QStyledItemDelegate):
    """绘制无常驻子控件的推文行，并缓存可复用的测量结果。"""

    def __init__(
        self, thumbnails: ThumbnailService, parent: QtCore.QObject | None = None
    ) -> None:
        """注入后台缩略图服务并建立有界布局缓存。"""
        super().__init__(parent)
        self.thumbnails = thumbnails  # 与详情页面共享的本地预览服务。
        self.expanded_keys: set[str] = set()  # 已展开推文的稳定主键集合。
        self._width = 640  # 视图在首次布局前使用的合理测量宽度。
        self._layouts: OrderedDict[tuple[str, str, int, bool], TweetLayout] = (
            OrderedDict()
        )
        self._body_font = _font(15)  # 正文使用的逻辑像素字体。
        self._name_font = _font(15, True)  # 作者名称使用的加粗字体。
        self._meta_font = _font(12)  # 次要信息的可读字体。

    def set_viewport_width(self, width: int) -> None:
        """同步视口宽度，让测量、绘制和命中使用相同换行边界。"""
        width = max(200, width)
        if width != self._width:
            self._width = width
            self.invalidate_layouts()

    def invalidate_layouts(self) -> None:
        """只清除可重建布局，保留用户的全文展开状态。"""
        self._layouts.clear()

    def toggle_expanded(self, key: str) -> None:
        """切换单条推文正文状态，相关缓存由状态键自然失效。"""
        if key in self.expanded_keys:
            self.expanded_keys.remove(key)
        else:
            self.expanded_keys.add(key)

    def layout_for(self, work: LibraryWork, width: int | None = None) -> TweetLayout:
        """获取不含任何磁盘或媒体解码操作的行布局。"""
        actual_width = max(200, width if width is not None else self._width)
        revision = work.signature or str(hash(work))
        key = (work.key, revision, actual_width, work.key in self.expanded_keys)
        cached = self._layouts.get(key)
        if cached is not None:
            self._layouts.move_to_end(key)
            return cached
        layout = self._measure(work, actual_width, key[-1])
        self._layouts[key] = layout
        while len(self._layouts) > LAYOUT_CACHE_LIMIT:
            self._layouts.popitem(last=False)
        return layout

    def _measure(self, work: LibraryWork, width: int, expanded: bool) -> TweetLayout:
        """测量正文与媒体，以稳定高度避免异步预览造成布局跳动。"""
        compact = width < 420
        left = 60 if compact else 78
        content_width = max(120, width - left - 18)
        avatar_size = 36 if compact else 44
        avatar = QtCore.QRect(12 if compact else 18, 18, avatar_size, avatar_size)
        header = QtCore.QRect(left, 16, content_width - 28, 25)
        menu = QtCore.QRect(width - 48, 6, 44, 44)
        y = 45
        text_rect = QtCore.QRect()
        expand_rect = QtCore.QRect()
        document: QtGui.QTextDocument | None = None
        body = work.text
        if not body and not work.media and not work.article_blocks:
            body = "未保存可阅读正文" if not work.article_urls else ""
        if body:
            document = self._text_document(
                body if expanded else body[:COLLAPSED_PREFIX_LIMIT], content_width
            )
            full_height = math.ceil(document.size().height())
            has_more = (
                expanded
                or len(body) > COLLAPSED_PREFIX_LIMIT
                or full_height > COLLAPSED_TEXT_LIMIT
            )
            shown_height = (
                full_height if expanded else self._folded_height(document, full_height)
            )
            text_rect = QtCore.QRect(left, y, content_width, shown_height)
            y += shown_height + 3
            if has_more:
                expand_rect = QtCore.QRect(left, y, content_width, 30)
                y += 32
            else:
                y += 7
        article = QtCore.QRect()
        if work.article_blocks or work.article_urls:
            article = QtCore.QRect(left, y + 2, content_width, 58)
            y += 70
        media_rects: tuple[QtCore.QRect, ...] = ()
        if work.media:
            media_rects = self._media_rects(work.media, left, y + 2, content_width)
            y = max(rect.bottom() for rect in media_rects) + 13
        warning = QtCore.QRect()
        if work.warning:
            warning = QtCore.QRect(left, y, content_width, 23)
            y += 26
        footer = QtCore.QRect(left, y, content_width, 28)
        return TweetLayout(
            width,
            max(106, y + 42),
            avatar,
            header,
            menu,
            text_rect,
            document,
            expand_rect,
            article,
            media_rects,
            warning,
            footer,
        )

    def _text_document(self, text: str, width: int) -> QtGui.QTextDocument:
        """创建不解释 HTML 的正文排版对象，并保留原始段落。"""
        document = QtGui.QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(self._body_font)
        option = QtGui.QTextOption()
        option.setWrapMode(QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        document.setPlainText(text)
        cursor = QtGui.QTextCursor(document)
        cursor.select(QtGui.QTextCursor.SelectionType.Document)
        block = QtGui.QTextBlockFormat()
        block.setLineHeight(
            BODY_LINE_HEIGHT, QtGui.QTextBlockFormat.LineHeightTypes.FixedHeight.value
        )
        cursor.mergeBlockFormat(block)
        document.setTextWidth(width)
        return document

    def _folded_height(self, document: QtGui.QTextDocument, full_height: int) -> int:
        """在完整行末折叠正文，避免直接裁掉半行文字。"""
        if full_height <= COLLAPSED_TEXT_LIMIT:
            return full_height
        bottom = 0.0
        block = document.begin()
        while block.isValid():
            block_top = document.documentLayout().blockBoundingRect(block).top()
            text_layout = block.layout()
            for number in range(text_layout.lineCount()):
                line = text_layout.lineAt(number)
                line_bottom = block_top + line.y() + line.height()
                if line_bottom > COLLAPSED_TEXT_LIMIT:
                    return max(24, math.ceil(bottom))
                bottom = line_bottom
            block = block.next()
        return max(24, min(full_height, math.ceil(bottom)))

    def _media_rects(
        self, media: tuple[LocalMedia, ...], x: int, y: int, width: int
    ) -> tuple[QtCore.QRect, ...]:
        """按一张、双列、三张非对称和四宫格排列原始媒体。"""
        count = min(4, len(media))
        if count == 1:
            item = media[0]
            ratio = (
                item.width / item.height
                if item.width and item.height and item.height > 0
                else 1.6
            )
            height = round(max(150, min(410, width / max(0.1, ratio))))
            return (QtCore.QRect(x, y, width, height),)
        gap = 3
        height = max(180, min(330, round(width * 0.65)))
        first_width = (width - gap) // 2
        second_width = width - gap - first_width
        if count == 2:
            return (
                QtCore.QRect(x, y, first_width, height),
                QtCore.QRect(x + first_width + gap, y, second_width, height),
            )
        first_height = (height - gap) // 2
        second_height = height - gap - first_height
        if count == 3:
            return (
                QtCore.QRect(x, y, first_width, height),
                QtCore.QRect(x + first_width + gap, y, second_width, first_height),
                QtCore.QRect(
                    x + first_width + gap,
                    y + first_height + gap,
                    second_width,
                    second_height,
                ),
            )
        return (
            QtCore.QRect(x, y, first_width, first_height),
            QtCore.QRect(x + first_width + gap, y, second_width, first_height),
            QtCore.QRect(x, y + first_height + gap, first_width, second_height),
            QtCore.QRect(
                x + first_width + gap,
                y + first_height + gap,
                second_width,
                second_height,
            ),
        )

    def sizeHint(
        self, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex
    ) -> QtCore.QSize:
        """返回与实际绘制一致的变高行尺寸。"""
        work = index.data(WORK_ROLE)
        if not isinstance(work, LibraryWork):
            return QtCore.QSize(self._width, 106)
        layout = self.layout_for(work)
        return QtCore.QSize(layout.width, layout.height)

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        """仅使用内存布局与预览绘制推文，不创建常驻子控件。"""
        work = index.data(WORK_ROLE)
        if not isinstance(work, LibraryWork):
            return
        layout = self.layout_for(work, option.rect.width())
        painter.save()
        painter.translate(option.rect.topLeft())
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.StateFlag.State_MouseOver)
        background = (
            SELECTED_BACKGROUND
            if selected
            else HOVER_BACKGROUND
            if hovered
            else BACKGROUND
        )
        painter.fillRect(0, 0, layout.width, layout.height, background)
        painter.setPen(LINE_COLOR)
        painter.drawLine(0, layout.height - 1, layout.width, layout.height - 1)
        self._paint_avatar(painter, work, layout.avatar)
        self._paint_header(painter, work, layout)
        if layout.document is not None:
            painter.save()
            painter.setClipRect(layout.text)
            painter.translate(layout.text.topLeft())
            context = QtGui.QAbstractTextDocumentLayout.PaintContext()
            context.palette.setColor(QtGui.QPalette.ColorRole.Text, TEXT_COLOR)
            layout.document.documentLayout().draw(painter, context)
            painter.restore()
        painter.setFont(self._body_font)
        painter.setPen(ACCENT_COLOR)
        if not layout.expand.isEmpty():
            label = "收起全文" if work.key in self.expanded_keys else "展开全文"
            painter.drawText(layout.expand, QtCore.Qt.AlignmentFlag.AlignVCenter, label)
        if not layout.article.isEmpty():
            self._paint_article(painter, work, layout.article)
        scale = painter.device().devicePixelRatioF()
        for number, rect in enumerate(layout.media):
            self._paint_media(painter, work.media[number], rect, scale)
            if number == 3 and len(work.media) > 4:
                painter.fillRect(rect, QtGui.QColor(0, 0, 0, 125))
                painter.setPen(QtCore.Qt.GlobalColor.white)
                painter.setFont(_font(19, True))
                painter.drawText(
                    rect,
                    QtCore.Qt.AlignmentFlag.AlignCenter,
                    f"+{len(work.media) - 4} · 查看全部",
                )
        self._paint_footer(painter, work, layout)
        if option.state & QtWidgets.QStyle.StateFlag.State_HasFocus:
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(QtGui.QPen(ACCENT_COLOR, 2))
            painter.drawRect(1, 1, layout.width - 3, layout.height - 3)
        painter.restore()

    def _paint_avatar(
        self, painter: QtGui.QPainter, work: LibraryWork, rect: QtCore.QRect
    ) -> None:
        """由本地账号稳定生成头像占位，不请求远程头像。"""
        name = work.display_name or work.username or "?"
        digest = hashlib.sha256((work.username or work.key).encode("utf-8")).digest()
        painter.setBrush(
            QtGui.QColor.fromHsl(int.from_bytes(digest[:2], "big") % 360, 118, 78)
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(rect)
        painter.setFont(_font(20, True))
        painter.setPen(QtCore.Qt.GlobalColor.white)
        painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, name[0].upper())

    def _paint_header(
        self, painter: QtGui.QPainter, work: LibraryWork, layout: TweetLayout
    ) -> None:
        """在一行内省略过长作者信息，保留清晰的更多操作入口。"""
        rect = layout.header
        name = work.display_name or work.username or "作者未知"
        name_metrics = QtGui.QFontMetrics(self._name_font)
        name_width = min(
            name_metrics.horizontalAdvance(name), max(52, round(rect.width() * 0.48))
        )
        painter.setFont(self._name_font)
        painter.setPen(TEXT_COLOR)
        painter.drawText(
            QtCore.QRect(rect.x(), rect.y(), name_width, rect.height()),
            QtCore.Qt.AlignmentFlag.AlignVCenter,
            name_metrics.elidedText(
                name, QtCore.Qt.TextElideMode.ElideRight, name_width
            ),
        )
        handle = f"@{work.username}" if work.username else "账号未知"
        meta = f"{handle} · {self._date_label(work.created_at)}"
        meta_rect = QtCore.QRect(
            rect.x() + name_width + 7,
            rect.y(),
            max(0, rect.width() - name_width - 7),
            rect.height(),
        )
        painter.setFont(self._meta_font)
        painter.setPen(MUTED_COLOR)
        painter.drawText(
            meta_rect,
            QtCore.Qt.AlignmentFlag.AlignVCenter,
            QtGui.QFontMetrics(self._meta_font).elidedText(
                meta, QtCore.Qt.TextElideMode.ElideRight, meta_rect.width()
            ),
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(MUTED_COLOR)
        center = layout.menu.center()
        for offset in (-5, 0, 5):
            painter.drawEllipse(
                QtCore.QPointF(center.x() + offset, center.y()), 1.5, 1.5
            )

    def _date_label(self, value: str | None) -> str:
        """显示真实保存日期，缺失或非法时间不使用目录日期冒充。"""
        if value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
                    "%Y/%m/%d"
                )
            except ValueError:
                return "时间未知"
        return "时间未知"

    def _paint_article(
        self, painter: QtGui.QPainter, work: LibraryWork, rect: QtCore.QRect
    ) -> None:
        """区分已保存文章正文与只有来源链接的记录。"""
        painter.setPen(LINE_COLOR)
        painter.setBrush(QtGui.QColor("#151e28"))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setFont(_font(13, True))
        painter.setPen(TEXT_COLOR)
        label = (
            "已保存文章 · 阅读全文"
            if work.article_blocks
            else "文章来源链接 · 查看详情"
        )
        painter.drawText(
            rect.adjusted(13, 5, -10, -25), QtCore.Qt.AlignmentFlag.AlignVCenter, label
        )
        painter.setFont(self._meta_font)
        painter.setPen(MUTED_COLOR)
        subtitle = (
            f"{len(work.article_blocks)} 段本地正文"
            if work.article_blocks
            else "未保存文章正文，打开详情查看来源"
        )
        painter.drawText(
            rect.adjusted(13, 29, -10, -5),
            QtCore.Qt.AlignmentFlag.AlignVCenter,
            QtGui.QFontMetrics(self._meta_font).elidedText(
                subtitle, QtCore.Qt.TextElideMode.ElideRight, rect.width() - 23
            ),
        )

    def _paint_media(
        self,
        painter: QtGui.QPainter,
        media: LocalMedia,
        rect: QtCore.QRect,
        scale: float,
    ) -> None:
        """裁剪已缓存预览，并为加载、缺失文件及视频提供明确反馈。"""
        painter.save()
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(rect), 11, 11)
        painter.setClipPath(path)
        painter.fillRect(rect, MEDIA_BACKGROUND)
        pixmap = self.thumbnails.request(
            media,
            QtCore.QSize(round(rect.width() * scale), round(rect.height() * scale)),
        )
        if pixmap is not None and not pixmap.isNull():
            source = QtCore.QRectF(pixmap.rect())
            factor = max(rect.width() / source.width(), rect.height() / source.height())
            source_width = rect.width() / factor
            source_height = rect.height() / factor
            source = QtCore.QRectF(
                (source.width() - source_width) / 2,
                (source.height() - source_height) / 2,
                source_width,
                source_height,
            )
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(QtCore.QRectF(rect), pixmap, source)
        else:
            painter.setPen(MUTED_COLOR)
            painter.setFont(self._meta_font)
            label = (
                "文件缺失"
                if not media.exists
                else "视频封面"
                if media.media_type == "video"
                else "图片预览"
            )
            painter.drawText(
                rect.adjusted(4, 4, -4, -4), QtCore.Qt.AlignmentFlag.AlignCenter, label
            )
        if media.media_type == "video":
            center = rect.center()
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(0, 0, 0, 145))
            painter.drawEllipse(center, 25, 25)
            painter.setBrush(QtCore.Qt.GlobalColor.white)
            triangle = QtGui.QPolygonF(
                [
                    QtCore.QPointF(center.x() - 5, center.y() - 10),
                    QtCore.QPointF(center.x() - 5, center.y() + 10),
                    QtCore.QPointF(center.x() + 11, center.y()),
                ]
            )
            painter.drawPolygon(triangle)
        painter.restore()
        painter.setPen(LINE_COLOR)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 11, 11)

    def _paint_footer(
        self, painter: QtGui.QPainter, work: LibraryWork, layout: TweetLayout
    ) -> None:
        """显示历史互动快照与详情入口，不构造虚假的社交按钮。"""
        painter.setFont(self._meta_font)
        painter.setPen(MUTED_COLOR)
        metrics = QtGui.QFontMetrics(self._meta_font)
        if not layout.warning.isEmpty():
            painter.setPen(QtGui.QColor("#d9b76f"))
            warning = metrics.elidedText(
                f"资料提示：{work.warning}",
                QtCore.Qt.TextElideMode.ElideRight,
                layout.warning.width(),
            )
            painter.drawText(
                layout.warning, QtCore.Qt.AlignmentFlag.AlignVCenter, warning
            )
            painter.setPen(MUTED_COLOR)
        values = [
            ("回复", work.reply_count),
            ("转发", work.retweet_count),
            ("喜欢", work.like_count),
        ]
        parts = [f"{label} {value:,}" for label, value in values if value is not None]
        snapshot = "   ".join(parts) or "本地保存"
        counts = layout.footer.adjusted(0, 0, -78, 0)
        painter.drawText(
            counts,
            QtCore.Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(
                snapshot, QtCore.Qt.TextElideMode.ElideRight, max(0, counts.width())
            ),
        )
        painter.setPen(ACCENT_COLOR)
        painter.drawText(
            layout.footer,
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
            "查看详情 ›",
        )

    def hit_test(
        self, work: LibraryWork, point: QtCore.QPoint, width: int | None = None
    ) -> tuple[str, int]:
        """使用绘制共用区域判定更多、展开、媒体或详情操作。"""
        layout = self.layout_for(work, width)
        if layout.menu.contains(point):
            return "menu", -1
        if layout.expand.contains(point):
            return "expand", -1
        for number, rect in enumerate(layout.media):
            if rect.contains(point):
                return "media", number
        return "detail", -1

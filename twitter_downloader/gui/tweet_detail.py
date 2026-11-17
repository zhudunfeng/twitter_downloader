"""仅显示已归档正文与本地媒体的推文详情。"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from twitter_downloader.library.models import LibraryWork


class TweetDetail(QtWidgets.QDialog):
    """提供可选择的完整正文、文章及媒体入口，不自动读取远程资源。"""

    media_requested = QtCore.Signal(object, int)  # 用户点中的作品与媒体序号。

    def __init__(
        self, work: LibraryWork, parent: QtWidgets.QWidget | None = None
    ) -> None:
        """使用纯文本渲染归档数据，避免 HTML 引入远程资源。"""
        super().__init__(parent)
        self.work = work  # 详情所对应的不可变作品快照。
        self.setWindowTitle("推文详情")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(720, 780)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel(work.display_name or work.username or "未知作者")
        title.setTextFormat(QtCore.Qt.PlainText)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QtWidgets.QLabel(
            f"@{work.username or 'unknown'}  ·  {work.created_at or '发布时间未知'}"
        )
        subtitle.setObjectName("caption")
        subtitle.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(subtitle)
        self.text = QtWidgets.QTextEdit()
        self.text.setReadOnly(True)
        self.text.setAccessibleName("完整推文及文章正文")
        paragraphs = [work.text]
        if work.article_blocks:
            paragraphs.extend(
                ["", "──────── 文章正文 ────────", "", *work.article_blocks]
            )
        if work.article_urls:
            paragraphs.extend(
                ["", "文章原始链接（点击下方打开原帖查看来源）：", *work.article_urls]
            )
        self.text.setPlainText("\n\n".join(text for text in paragraphs if text))
        layout.addWidget(self.text, 1)
        if work.warning:
            warning = QtWidgets.QLabel(work.warning)
            warning.setTextFormat(QtCore.Qt.PlainText)
            warning.setWordWrap(True)
            warning.setObjectName("warning")
            layout.addWidget(warning)
        if work.media:
            media_row = QtWidgets.QHBoxLayout()
            for index, item in enumerate(work.media):
                button = QtWidgets.QPushButton(
                    f"{'视频' if item.media_type == 'video' else '图片'} {index + 1}"
                )
                button.setObjectName("secondary")
                button.clicked.connect(
                    lambda checked=False, position=index: self._open_media(position)
                )
                media_row.addWidget(button)
            media_row.addStretch(1)
            media_box = QtWidgets.QWidget()
            media_box.setLayout(media_row)
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(media_box)
            scroll.setMaximumHeight(70)
            layout.addWidget(scroll)
        actions = QtWidgets.QHBoxLayout()
        copy = QtWidgets.QPushButton("复制正文")
        copy.clicked.connect(self._copy)
        actions.addWidget(copy)
        original = QtWidgets.QPushButton("打开原帖")
        original.setObjectName("secondary")
        original.setEnabled(QtCore.QUrl(work.url).scheme() in ("http", "https"))
        original.clicked.connect(self._open_original)
        actions.addWidget(original)
        folder = QtWidgets.QPushButton("打开文件夹")
        folder.setObjectName("secondary")
        folder.clicked.connect(self._open_folder)
        actions.addWidget(folder)
        actions.addStretch(1)
        close = QtWidgets.QPushButton("返回推文流")
        close.setObjectName("secondary")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)

    def _copy(self) -> None:
        """复制完整已保存正文与文章段落。"""
        QtWidgets.QApplication.clipboard().setText(
            "\n\n".join((self.work.text, *self.work.article_blocks)).strip()
        )

    def _open_original(self) -> None:
        """用户显式点击后才打开 HTTP(S) 原帖链接。"""
        url = QtCore.QUrl(self.work.url)
        if url.scheme() in ("http", "https"):
            QtGui.QDesktopServices.openUrl(url)

    def _open_folder(self) -> None:
        """以系统文件管理器打开当前作品目录。"""
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.work.directory))

    def _open_media(self, index: int) -> None:
        """让作品库复用同一图片查看器和视频播放器。"""
        self.media_requested.emit(self.work, index)
        if self.work.media[index].media_type == "video":
            self.close()

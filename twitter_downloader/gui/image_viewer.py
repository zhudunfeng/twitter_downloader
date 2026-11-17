"""带缩放、拖动和前后切换的离线图片查看器。"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from twitter_downloader.library.models import LocalMedia


class _ImageCanvas(QtWidgets.QGraphicsView):
    """通过图形视图缩放本地图片，保持原始比例。"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        """初始化拖动视图和无装饰画布。"""
        super().__init__(parent)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.setBackgroundBrush(QtGui.QColor("#080b10"))
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        self.item = QtWidgets.QGraphicsPixmapItem()
        self.scene().addItem(self.item)

    def fit(self) -> None:
        """将当前图片按比例缩放到可用画布。"""
        if not self.item.pixmap().isNull():
            self.fitInView(self.item, QtCore.Qt.KeepAspectRatio)

    def zoom(self, factor: float) -> None:
        """在合理倍率内缩放，避免无效的极端变换。"""
        scale = self.transform().m11() * factor
        if 0.015 <= scale <= 32:
            self.scale(factor, factor)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        """以滚轮缩放并保持鼠标所指位置相对稳定。"""
        self.zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
        event.accept()


class _ImageLoader(QtCore.QThread):
    """在后台读取显示尺寸图片，避免大图解码阻塞阅读界面。"""

    loaded = QtCore.Signal(int, QtGui.QImage, str)  # 请求序号、图像和错误说明。

    def __init__(
        self, token: int, path: str, original: bool, parent: QtCore.QObject
    ) -> None:
        """保存请求参数；original 表示用户显式请求原始分辨率。"""
        super().__init__(parent)
        self.token = token  # 用于丢弃过期解码结果的请求编号。
        self.path = path  # 已校验的本地图片路径。
        self.original = original  # 是否保留原始分辨率。

    def run(self) -> None:
        """应用 EXIF 方向并在后台解码，默认限制到显示所需尺寸。"""
        reader = QtGui.QImageReader(self.path)
        reader.setAutoTransform(True)
        size = reader.size()
        if (
            not self.original
            and size.isValid()
            and max(size.width(), size.height()) > 3200
        ):
            reader.setScaledSize(size.scaled(3200, 3200, QtCore.Qt.KeepAspectRatio))
        picture = reader.read()
        if not self.isInterruptionRequested():
            self.loaded.emit(
                self.token, picture, reader.errorString() if picture.isNull() else ""
            )


class ImageViewer(QtWidgets.QDialog):
    """独立图片浏览层，关闭后不改变底下的推文阅读位置。"""

    def __init__(
        self,
        media: tuple[LocalMedia, ...],
        index: int = 0,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """从媒体集合筛选图片，并保留用户点中的媒体位置。"""
        super().__init__(parent)
        selected = media[index] if 0 <= index < len(media) else None
        self.images = [
            item for item in media if item.media_type == "image"
        ]  # 保持原始图片顺序。
        self.index = (
            self.images.index(selected) if selected in self.images else 0
        )  # 当前图片索引。
        self._token: int = 0  # 解码请求版本。
        self._jobs: list[_ImageLoader] = []  # 尚未退出的后台读取线程。
        self._pending_image: tuple[int, str, bool] | None = (
            None  # 快速切换时只保留最后一次解码请求。
        )
        self._closing = False  # 关闭时禁止重新启动解码。
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle("查看图片")
        self.resize(1060, 800)
        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        self.counter = QtWidgets.QLabel()
        controls.addWidget(self.counter, 1)
        for text, callback in (
            ("上一张", self.previous),
            ("下一张", self.next),
            ("缩小", self.zoom_out),
            ("放大", self.zoom_in),
            ("适应窗口", self.fit),
            ("原始分辨率", self.load_original),
            ("关闭", self.close),
        ):
            button = QtWidgets.QPushButton(text)
            button.setObjectName("secondary")
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.message = QtWidgets.QLabel("正在读取图片…")
        self.message.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(self.message)
        self.canvas = _ImageCanvas()
        layout.addWidget(self.canvas, 1)
        self._load()

    def _load(self, original: bool = False) -> None:
        """发起新解码任务，并禁止旧任务覆盖已切换的图片。"""
        if not self.images:
            self.message.setText("该作品没有可查看的图片")
            return
        self._token += 1
        self.counter.setText(f"图片 {self.index + 1} / {len(self.images)}")
        self.message.setText("正在读取原图…" if original else "正在读取图片…")
        self.canvas.item.setPixmap(QtGui.QPixmap())
        self._pending_image = (self._token, self.images[self.index].path, original)
        for job in self._jobs:
            job.requestInterruption()
        self._launch_pending()

    def _launch_pending(self) -> None:
        """最多运行一个解码线程，过期请求被后续选择合并。"""
        if self._closing or self._jobs or self._pending_image is None:
            return
        token, path, original = self._pending_image
        self._pending_image = None
        job = _ImageLoader(token, path, original, self)
        self._jobs.append(job)
        job.loaded.connect(self._loaded)
        job.finished.connect(self._cleanup_jobs)
        job.start()

    def _loaded(self, token: int, picture: QtGui.QImage, error: str) -> None:
        """只显示最新请求，并在界面线程创建显示资源。"""
        if token != self._token:
            return
        if picture.isNull():
            self.message.setText(f"图片无法读取或已移动：{error}")
            return
        self.canvas.item.setPixmap(QtGui.QPixmap.fromImage(picture))
        self.canvas.setSceneRect(self.canvas.item.boundingRect())
        self.canvas.fit()
        self.message.setText("滚轮缩放 · 拖动平移 · ← → 切换 · Esc 返回")

    def _cleanup_jobs(self) -> None:
        """清除已结束线程，保留仍在工作的线程引用。"""
        for job in self._jobs[:]:
            if job.isFinished():
                self._jobs.remove(job)
                job.deleteLater()
        self._launch_pending()

    def previous(self) -> None:
        """循环查看上一张本地图片。"""
        if self.images:
            self.index = (self.index - 1) % len(self.images)
            self._load()

    def next(self) -> None:
        """循环查看下一张本地图片。"""
        if self.images:
            self.index = (self.index + 1) % len(self.images)
            self._load()

    def zoom_in(self) -> None:
        """放大当前图片。"""
        self.canvas.zoom(1.25)

    def zoom_out(self) -> None:
        """缩小当前图片。"""
        self.canvas.zoom(0.8)

    def fit(self) -> None:
        """恢复适应窗口的缩放。"""
        self.canvas.fit()

    def load_original(self) -> None:
        """经显式操作后载入原始分辨率用于细节查看。"""
        self._load(original=True)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        """支持方向键切图及加减号缩放，Escape 沿用对话框行为。"""
        if event.key() == QtCore.Qt.Key_Left:
            self.previous()
        elif event.key() == QtCore.Qt.Key_Right:
            self.next()
        elif event.key() in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
            self.zoom_in()
        elif event.key() == QtCore.Qt.Key_Minus:
            self.zoom_out()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """等待图片读取安全退出，避免销毁仍在运行的 QThread。"""
        self._stop_loading()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        """Escape 或对话框接受/拒绝入口也必须安全结束解码。"""
        self._stop_loading()
        super().done(result)

    def _stop_loading(self) -> None:
        """放弃待处理请求并等待唯一后台读取安全退出。"""
        self._closing = True
        self._pending_image = None
        self._token += 1
        for job in self._jobs:
            job.requestInterruption()
        for job in self._jobs:
            job.wait()

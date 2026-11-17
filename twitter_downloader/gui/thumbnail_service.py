"""有界的本地图片缩略图与视频封面服务。"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6 import QtCore, QtGui

from twitter_downloader.library.models import LocalMedia

logger = logging.getLogger(__name__)

MEMORY_LIMIT = 128 * 1024 * 1024  # 缩略图内存上限，单位为字节。
DISK_LIMIT = 512 * 1024 * 1024  # 磁盘预览缓存上限，单位为字节。
WORKER_COUNT = 3  # 预览任务最大并行数，避免抢占下载与界面资源。
PENDING_LIMIT = 48  # 等待和执行中的预览请求上限。
POSTER_TIMEOUT = 5  # 单个视频封面提取超时，单位为秒。
MAX_PREVIEW_EDGE = 1600  # 单边解码尺寸上限，原图由独立查看器加载。


class _ThumbnailSignals(QtCore.QObject):
    """跨线程传递图像数据，不在后台创建显示资源。"""

    finished = QtCore.Signal(str, str, QtGui.QImage)


class _ThumbnailJob(QtCore.QRunnable):
    """从磁盘缓存或原文件生成单张有界预览。"""

    def __init__(
        self,
        key: str,
        media: LocalMedia,
        size: QtCore.QSize,
        cache_dir: Path,
        ffmpeg: str | None,
        stopped: threading.Event,
        signals: _ThumbnailSignals,
    ) -> None:
        """保存只读任务参数和共享的取消标识。"""
        super().__init__()
        self.key = key  # 包含文件版本和输出尺寸的缓存键。
        self.media = media  # 已通过作品读取层验证的本地媒体。
        self.size = size  # 请求的物理像素尺寸。
        self.cache_dir = cache_dir  # 专用缓存目录，不保存作品原文件。
        self.ffmpeg = ffmpeg  # 可选的视频封面工具路径。
        self.stopped = stopped  # 服务关闭标识。
        self.signals = signals  # 仅传输 QImage 的跨线程信号对象。

    def run(self) -> None:
        """在后台读写预览，失败时返回空图而不影响作品浏览。"""
        image = QtGui.QImage()
        try:
            if not self.stopped.is_set():
                cached = self.cache_dir / f"{self.key}.jpg"
                if cached.is_file():
                    image = QtGui.QImage(str(cached))
                if image.isNull() and self.media.exists:
                    image = self._decode()
                    if not image.isNull() and not self.stopped.is_set():
                        self.cache_dir.mkdir(parents=True, exist_ok=True)
                        temporary = self.cache_dir / f"{self.key}.tmp"
                        if image.save(str(temporary), "JPEG", 86):
                            os.replace(temporary, cached)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("生成媒体预览失败：%s；%s", self.media.path, exc)
        # 关闭之后仍完成信号，让主线程清理正在执行的任务索引。
        self.signals.finished.emit(self.key, self.media.path, image)

    def _decode(self) -> QtGui.QImage:
        """根据媒体类型进行缩放解码或提取视频首帧。"""
        if self.media.media_type == "video":
            return self._video_frame()
        reader = QtGui.QImageReader(self.media.path)
        reader.setAutoTransform(True)
        original = reader.size()
        if original.isValid():
            # 拼图采用铺满裁剪，先按覆盖区域解码，避免竖图预览被过度缩小。
            decoded_size = original.scaled(
                self.size, QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding
            )
            if max(decoded_size.width(), decoded_size.height()) > MAX_PREVIEW_EDGE:
                decoded_size.scale(
                    MAX_PREVIEW_EDGE,
                    MAX_PREVIEW_EDGE,
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                )
            reader.setScaledSize(decoded_size)
        image = reader.read()
        if not image.isNull() and (
            image.width() > MAX_PREVIEW_EDGE or image.height() > MAX_PREVIEW_EDGE
        ):
            image = image.scaled(
                MAX_PREVIEW_EDGE,
                MAX_PREVIEW_EDGE,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        return image

    def _video_frame(self) -> QtGui.QImage:
        """在有界子进程中提取封面，缺少工具时保留占位。"""
        if not self.ffmpeg:
            return QtGui.QImage()
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0.1",
            "-i",
            self.media.path,
            "-frames:v",
            "1",
            "-vf",
            f"scale={self.size.width()}:{self.size.height()}:force_original_aspect_ratio=decrease",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=POSTER_TIMEOUT,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return (
                QtGui.QImage.fromData(result.stdout)
                if result.returncode == 0
                else QtGui.QImage()
            )
        except (OSError, subprocess.TimeoutExpired):
            return QtGui.QImage()


class _PruneJob(QtCore.QRunnable):
    """在后台按修改时间清理专用磁盘缓存。"""

    def __init__(self, directory: Path, stopped: threading.Event) -> None:
        """保存可清理目录及关闭标识。"""
        super().__init__()
        self.directory = directory  # 只允许操作此专用缓存目录。
        self.stopped = stopped  # 关闭后停止继续清理。

    def run(self) -> None:
        """清理超限的旧预览，不读取或修改作品文件。"""
        try:
            files = []
            for path in self.directory.glob("*.jpg"):
                if self.stopped.is_set():
                    return
                stat = path.stat()
                files.append((stat.st_mtime_ns, stat.st_size, path))
            total = sum(item[1] for item in files)
            for _, size, path in sorted(files):
                if total <= DISK_LIMIT or self.stopped.is_set():
                    break
                try:
                    path.unlink()
                    total -= size
                except OSError:
                    continue
        except OSError as exc:
            logger.debug("清理预览缓存失败：%s", exc)


class ThumbnailService(QtCore.QObject):
    """提供不阻塞绘制的预览查询和两级缓存。"""

    ready = QtCore.Signal(str)

    def __init__(
        self, cache_dir: str | Path, parent: QtCore.QObject | None = None
    ) -> None:
        """初始化专用线程池和有界内存缓存。"""
        super().__init__(parent)
        self._cache_dir = Path(cache_dir)  # 预览磁盘缓存目录。
        self._cache: OrderedDict[str, QtGui.QPixmap] = (
            OrderedDict()
        )  # 最近使用的显示图像。
        self._cost = 0  # 当前显示图像占用的估算字节数。
        self._pending: set[str] = set()  # 防止同一版本、尺寸被重复调度。
        self._failed: OrderedDict[str, None] = (
            OrderedDict()
        )  # 有界的失败记忆，防止重试风暴。
        self._stopped = threading.Event()  # 跨线程共享关闭标识。
        self._closed = False  # 主线程服务关闭状态。
        self._completed = 0  # 用于低频触发磁盘清理的完成计数。
        self._priority = 0  # 新请求使用更高优先级，快速滚动时优先处理新可见媒体。
        self._ffmpeg = shutil.which("ffmpeg")  # 视频封面可选工具。
        self._signals = _ThumbnailSignals(self)
        self._signals.finished.connect(
            self._on_finished, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(WORKER_COUNT)
        self._pool.setExpiryTimeout(5000)
        self._pool.start(_PruneJob(self._cache_dir, self._stopped))

    def request(self, media: LocalMedia, size: QtCore.QSize) -> QtGui.QPixmap | None:
        """立即返回内存命中，否则排队生成；此方法不读写文件。"""
        if self._closed or not media.exists:
            return None
        bounded = QtCore.QSize(
            max(32, min(MAX_PREVIEW_EDGE, size.width())),
            max(32, min(MAX_PREVIEW_EDGE, size.height())),
        )
        identity = (
            f"{media.path}\0{media.signature}\0{media.size_bytes}\0"
            f"{bounded.width()}x{bounded.height()}"
        )
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        if (
            key in self._pending
            or key in self._failed
            or len(self._pending) >= PENDING_LIMIT
        ):
            return None
        self._pending.add(key)
        self._priority += 1
        self._pool.start(
            _ThumbnailJob(
                key,
                media,
                bounded,
                self._cache_dir,
                self._ffmpeg,
                self._stopped,
                self._signals,
            ),
            self._priority,
        )
        return None

    @QtCore.Slot(str, str, QtGui.QImage)
    def _on_finished(self, key: str, path: str, image: QtGui.QImage) -> None:
        """在界面线程创建显示资源，并按字节预算淘汰缓存。"""
        self._pending.discard(key)
        if self._closed:
            return
        if image.isNull():
            self._failed[key] = None
            while len(self._failed) > 2048:
                self._failed.popitem(last=False)
        else:
            pixmap = QtGui.QPixmap.fromImage(image)
            previous = self._cache.pop(key, None)
            if previous is not None:
                self._cost -= previous.width() * previous.height() * 4
            self._cache[key] = pixmap
            self._cost += pixmap.width() * pixmap.height() * 4
            while self._cost > MEMORY_LIMIT and self._cache:
                _, removed = self._cache.popitem(last=False)
                self._cost -= removed.width() * removed.height() * 4
        self._completed += 1
        if self._completed % 96 == 0:
            self._pool.start(_PruneJob(self._cache_dir, self._stopped))
        self.ready.emit(path)

    def close(self) -> None:
        """停止等待任务，并等待有超时保护的执行任务退出。"""
        if self._closed:
            return
        self._closed = True
        self._stopped.set()
        self._pool.clear()
        self._pool.waitForDone()
        self._pending.clear()
        self._cache.clear()
        self._cost = 0
        logger.info("媒体预览服务已关闭")

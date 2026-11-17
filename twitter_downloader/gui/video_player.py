"""可复用的本地视频播放器，同一作品库始终只保留一个解码实例。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

logger = logging.getLogger(__name__)


def format_duration(milliseconds: int) -> str:
    """将毫秒转换为播放器使用的分秒或时分秒文本。"""
    seconds = max(0, milliseconds // 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


class VideoPlayer(QtWidgets.QFrame):
    """在列表和全屏窗口之间移动同一个播放器，避免叠加声音。"""

    close_requested = QtCore.Signal()  # 用户关闭当前内嵌视频。
    placement_changed = QtCore.Signal()  # 全屏返回时请求宿主重新检查可见区域。

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        """创建视频画面、音轨输出及键盘可操作的控制栏。"""
        super().__init__(parent)
        self.setObjectName("videoPlayer")
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.current_path: str = ""  # 当前本地视频路径。
        self._duration: int = 0  # 已加载视频的总时长，单位毫秒。
        self._fullscreen: QtWidgets.QDialog | None = None  # 正在使用的全屏宿主。
        self._inline_parent: QtWidgets.QWidget | None = None  # 全屏退出后的原始宿主。
        self._inline_geometry = QtCore.QRect()  # 全屏前的列表内位置。
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.65)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget(self)
        self.video.setAspectRatioMode(QtCore.Qt.KeepAspectRatio)
        self.player.setVideoOutput(self.video)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.video, 1)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(QtCore.Qt.PlainText)
        self.message.setObjectName("playerMessage")
        self.message.hide()
        layout.addWidget(self.message)

        controls = QtWidgets.QWidget()
        controls.setObjectName("playerControls")
        row = QtWidgets.QHBoxLayout(controls)
        row.setContentsMargins(6, 5, 6, 5)
        row.setSpacing(5)
        self.play_button = QtWidgets.QToolButton()
        self.play_button.setAccessibleName("播放或暂停视频")
        self.play_button.setToolTip("播放 / 暂停（空格）")
        self.play_button.clicked.connect(self.toggle_playback)
        row.addWidget(self.play_button)
        self.seek = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setAccessibleName("播放进度")
        self.seek.sliderMoved.connect(self._seek_to)
        self.seek.sliderReleased.connect(self._seek_released)
        self.seek.actionTriggered.connect(self._seek_action)
        row.addWidget(self.seek, 1)
        self.time_label = QtWidgets.QLabel("0:00 / 0:00")
        self.time_label.setObjectName("caption")
        row.addWidget(self.time_label)
        self.mute = QtWidgets.QToolButton()
        self.mute.setCheckable(True)
        self.mute.setToolTip("静音")
        self.mute.setAccessibleName("静音")
        self.mute.toggled.connect(self._set_muted)
        row.addWidget(self.mute)
        self.volume = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(65)
        self.volume.setFixedWidth(55)
        self.volume.setAccessibleName("音量")
        self.volume.valueChanged.connect(self._set_volume)
        row.addWidget(self.volume)
        self.speed = QtWidgets.QComboBox()
        for speed in (0.5, 1.0, 1.5, 2.0):
            self.speed.addItem(f"{speed:g}×", speed)
        self.speed.setCurrentIndex(1)
        self.speed.setAccessibleName("播放速度")
        self.speed.currentIndexChanged.connect(self._set_speed)
        row.addWidget(self.speed)
        self.fullscreen_button = QtWidgets.QToolButton()
        self.fullscreen_button.setText("全屏")
        self.fullscreen_button.setAccessibleName("切换全屏")
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        row.addWidget(self.fullscreen_button)
        close_button = QtWidgets.QToolButton()
        close_button.setText("关闭")
        close_button.setAccessibleName("关闭当前视频")
        close_button.clicked.connect(self._request_close)
        row.addWidget(close_button)
        layout.addWidget(controls)
        self.system_button = QtWidgets.QPushButton("使用系统播放器打开")
        self.system_button.clicked.connect(self._open_system)
        self.system_button.hide()
        layout.addWidget(self.system_button)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(self._error)
        self._state_changed(QMediaPlayer.StoppedState)
        self._set_muted(False)
        self.hide()

    def play_path(self, path: str) -> None:
        """显式载入本地文件并开始播放，切换前停止旧音轨。"""
        self.stop()
        self.current_path = path
        self.show()
        if not Path(path).is_file():
            self._error(QMediaPlayer.ResourceError, "视频文件已移动或不存在")
            return
        self.message.setText("正在加载视频…")
        self.message.show()
        self.system_button.hide()
        logger.info("开始播放本地视频：%s", path)
        self.player.setSource(QtCore.QUrl.fromLocalFile(path))
        self.player.play()

    def toggle_playback(self) -> None:
        """切换播放与暂停，已结束的视频从开头重新播放。"""
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.EndOfMedia:
                self.player.setPosition(0)
            self.player.play()

    def pause(self) -> None:
        """作品离开视野或页面隐藏时暂停音视频。"""
        self.player.pause()

    def stop(self) -> None:
        """退出全屏并清空当前媒体，释放后端解码资源。"""
        if self._fullscreen is not None:
            self._fullscreen.close()
        self.player.stop()
        self.player.setSource(QtCore.QUrl())
        self.current_path = ""
        self.hide()

    def is_fullscreen(self) -> bool:
        """返回播放器是否正处于独立全屏宿主中。"""
        return self._fullscreen is not None

    def toggle_fullscreen(self) -> None:
        """移动同一个视频组件进入或离开全屏，保留播放状态。"""
        if self._fullscreen is not None:
            self._fullscreen.close()
            return
        self._inline_parent = self.parentWidget()
        self._inline_geometry = self.geometry()
        dialog = QtWidgets.QDialog(self.window())
        dialog.setWindowTitle("视频 · Esc 返回")
        dialog.setStyleSheet("background: #000000;")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self)
        self._fullscreen = dialog
        dialog.finished.connect(self._restore_inline)
        self.fullscreen_button.setText("退出全屏")
        dialog.showFullScreen()
        self.show()
        self.setFocus()

    def _restore_inline(self, result: int) -> None:
        """全屏关闭后恢复列表宿主与几何位置，不重建播放器。"""
        dialog = self._fullscreen
        self._fullscreen = None
        self.setParent(self._inline_parent)
        original_layout = (
            self._inline_parent.layout() if self._inline_parent is not None else None
        )
        # 独立视频窗口使用布局管理尺寸；视口覆盖层则恢复原有几何区域。
        if original_layout is not None:
            original_layout.addWidget(self)
        else:
            self.setGeometry(self._inline_geometry)
        self.fullscreen_button.setText("全屏")
        self.show()
        if dialog is not None:
            dialog.deleteLater()
        self.placement_changed.emit()

    def _request_close(self) -> None:
        """向作品库通知用户关闭当前视频。"""
        self.stop()
        self.close_requested.emit()

    def _seek_to(self, value: int) -> None:
        """将千分比滑动位置转换为实际毫秒位置。"""
        if self._duration:
            self.player.setPosition(self._duration * value // 1000)

    def _seek_released(self) -> None:
        """在松开进度滑块时提交最后的位置。"""
        self._seek_to(self.seek.value())

    def _seek_action(self, action: int) -> None:
        """处理方向键和轨道点击，保持键盘进度操作可用。"""
        if action != QtWidgets.QAbstractSlider.SliderNoAction:
            self._seek_to(self.seek.sliderPosition())

    def _set_volume(self, value: int) -> None:
        """把百分比音量应用到唯一音轨输出。"""
        self.audio.setVolume(value / 100)

    def _control_icon(self, icon: QtWidgets.QStyle.StandardPixmap) -> QtGui.QIcon:
        """将平台媒体图标着为浅色，确保深色控制栏内具有清晰对比。"""
        pixmap = self.style().standardIcon(icon).pixmap(64, 64)
        painter = QtGui.QPainter(pixmap)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QtGui.QColor("#dce7f4"))
        painter.end()
        return QtGui.QIcon(pixmap)

    def _set_muted(self, muted: bool) -> None:
        """设置静音状态并同步按钮图标。"""
        self.audio.setMuted(muted)
        icon = (
            QtWidgets.QStyle.SP_MediaVolumeMuted
            if muted
            else QtWidgets.QStyle.SP_MediaVolume
        )
        self.mute.setIcon(self._control_icon(icon))

    def _set_speed(self, index: int) -> None:
        """请求后端使用所选倍率，支持情况由当前媒体后端决定。"""
        self.player.setPlaybackRate(float(self.speed.itemData(index)))

    def _position_changed(self, position: int) -> None:
        """同步时间标签及未被用户拖动的进度条。"""
        self.time_label.setText(
            f"{format_duration(position)} / {format_duration(self._duration)}"
        )
        if not self.seek.isSliderDown():
            self.seek.setValue(position * 1000 // max(1, self._duration))

    def _duration_changed(self, duration: int) -> None:
        """记录媒体时长并更新跳转能力。"""
        self._duration = duration
        self.seek.setEnabled(duration > 0)
        self._position_changed(self.player.position())

    def _state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """根据实际播放状态显示正确的播放或暂停图标。"""
        icon = (
            QtWidgets.QStyle.SP_MediaPause
            if state == QMediaPlayer.PlayingState
            else QtWidgets.QStyle.SP_MediaPlay
        )
        self.play_button.setIcon(self._control_icon(icon))

    def _media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """完成加载后撤下提示，避免覆盖正常视频画面。"""
        if status in (
            QMediaPlayer.LoadedMedia,
            QMediaPlayer.BufferedMedia,
            QMediaPlayer.EndOfMedia,
        ):
            self.message.hide()

    def _error(self, error: QMediaPlayer.Error, message: str) -> None:
        """以中文呈现后端错误，并提供显式系统播放器回退入口。"""
        if error == QMediaPlayer.NoError:
            return
        logger.warning("本地视频播放失败：%s", message)
        self.message.setText(
            f"暂时无法播放此视频：{message}\n可使用系统播放器打开，其他作品仍可正常浏览。"
        )
        self.message.show()
        self.system_button.show()

    def _open_system(self) -> None:
        """经用户点击后使用系统程序打开当前本地视频。"""
        if self.current_path:
            self.pause()
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.current_path))

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        """提供空格播放、F 全屏及 Escape 返回的键盘入口。"""
        if event.key() == QtCore.Qt.Key_Space:
            self.toggle_playback()
        elif event.key() == QtCore.Qt.Key_F:
            self.toggle_fullscreen()
        elif event.key() == QtCore.Qt.Key_Escape:
            if self._fullscreen is not None:
                self._fullscreen.close()
            else:
                self._request_close()
        else:
            super().keyPressEvent(event)

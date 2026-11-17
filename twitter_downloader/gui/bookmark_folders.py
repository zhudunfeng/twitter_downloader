"""收藏夹多选与管理窗口，只提交意图而不在界面线程访问数据库。"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6 import QtCore, QtWidgets

from twitter_downloader.library.models import BookmarkFolder
from twitter_downloader.library.personal_state import validate_folder_name


class _FolderDialog(QtWidgets.QDialog):
    """复用收藏夹表单、内联错误与异步保存反馈。"""

    create_requested = QtCore.Signal(str)  # 经统一校验的新建名称，存储仍重新校验。

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        """建立清晰的输入标签、状态反馈和键盘可达的原生控件。"""
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(360, 360)
        self.resize(470, 530)
        self._folders: tuple[BookmarkFolder, ...] = ()  # 当前可用收藏夹快照。
        self._busy = False  # 等待共用控制器返回时阻止重复提交。
        self._error_message = ""  # 保留失败说明，忙碌信号解除时仍可阅读。
        self.layout_box = QtWidgets.QVBoxLayout(self)
        self.layout_box.setContentsMargins(22, 20, 22, 20)
        self.layout_box.setSpacing(12)
        self.hint = QtWidgets.QLabel()
        self.hint.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.hint.setWordWrap(True)
        self.layout_box.addWidget(self.hint)
        self.folder_list = QtWidgets.QListWidget()
        self.folder_list.setAccessibleName("收藏夹列表")
        self.folder_list.setMinimumHeight(150)
        self.layout_box.addWidget(self.folder_list, 1)
        self.name_label = QtWidgets.QLabel("新收藏夹名称")
        self.layout_box.addWidget(self.name_label)
        create_row = QtWidgets.QHBoxLayout()
        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("最多 40 个字符")
        self.name_input.setClearButtonEnabled(True)
        self.name_input.setAccessibleName("新收藏夹名称")
        self.name_label.setBuddy(self.name_input)
        self.name_input.returnPressed.connect(self._create)
        create_row.addWidget(self.name_input, 1)
        self.create_button = QtWidgets.QPushButton("新建")
        self.create_button.setObjectName("secondary")
        self.create_button.setAutoDefault(False)
        self.create_button.clicked.connect(self._create)
        create_row.addWidget(self.create_button)
        self.layout_box.addLayout(create_row)
        self.status_label = QtWidgets.QLabel()
        self.status_label.setObjectName("warning")
        self.status_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("收藏夹操作状态")
        self.layout_box.addWidget(self.status_label)
        self.setStyleSheet(
            "QListWidget:focus, QLineEdit:focus, QPushButton:focus { border: 2px solid #1d9bf0; }"
            "QListWidget::item { min-height: 36px; padding: 4px 8px; }"
        )

    def _validated_name(self, value: str, excluding_id: str = "") -> str | None:
        """复用存储校验与名称归一化，保留用户输入以便原位修正。"""
        try:
            name, key = validate_folder_name(value)
            if any(
                folder.id != excluding_id
                and validate_folder_name(folder.name)[1] == key
                for folder in self._folders
            ):
                raise ValueError("已存在同名收藏夹，请使用其他名称")
            return name
        except (TypeError, ValueError) as exc:
            self.show_error(str(exc))
            return None

    def _create(self) -> None:
        """提交新建请求，成功后由控制器刷新列表并恢复输入。"""
        if self._busy:
            return
        name = self._validated_name(self.name_input.text())
        if name is None:
            self.name_input.setFocus()
            return
        self.set_busy(True)
        self.create_requested.emit(name)

    def set_busy(self, busy: bool) -> None:
        """显示异步处理状态并阻止重复写入，取消键仍在完成后可用。"""
        self._busy = busy
        self.name_input.setEnabled(not busy)
        self.create_button.setEnabled(not busy)
        self.folder_list.setEnabled(not busy)
        if busy:
            self._error_message = ""
        self.status_label.setText("正在保存…" if busy else self._error_message)

    def show_error(self, message: str, *, busy: bool = False) -> None:
        """内联显示可恢复的中文错误，不把失败伪装成空收藏夹。"""
        self.set_busy(busy)
        self._error_message = message
        self.status_label.setText(message)

    def reject(self) -> None:
        """已提交的保存完成前保持窗口，避免让取消看起来撤销了已接受的写入。"""
        if self._busy:
            self.status_label.setText("正在保存，请稍候再关闭")
            return
        super().reject()


class BookmarkFolderPicker(_FolderDialog):
    """以原生复选框一次选择作品所属的全部收藏夹。"""

    saved = QtCore.Signal(object)  # 用户确认的完整收藏夹 ID 元组。
    cancel_all_requested = QtCore.Signal()  # 用户明确要求取消此作品的全部收藏归属。

    def __init__(
        self,
        folders: Iterable[BookmarkFolder],
        selected_ids: Iterable[str],
        parent: QtWidgets.QWidget | None = None,
        *,
        collected: bool | None = None,
    ) -> None:
        """回显既有归属；新收藏由调用方预选默认夹而不提前写入。"""
        super().__init__(parent)
        selected = set(selected_ids)
        self._initial_selected = selected  # 首次列表建立前使用的完整勾选集合。
        self._was_collected = bool(selected) if collected is None else collected
        self.setWindowTitle("整理收藏夹")
        self.hint.setText(
            "选择此作品的收藏夹，可同时勾选多个。点击保存后生效；取消不改变归属。"
        )
        self.folder_list.setAccessibleDescription(
            "空格切换当前收藏夹勾选，上下键选择，Tab 前往保存。"
        )
        self.folder_list.itemChanged.connect(self._selection_changed)
        buttons = QtWidgets.QHBoxLayout()
        self.cancel_all_button = QtWidgets.QPushButton("取消全部收藏")
        self.cancel_all_button.setObjectName("secondary")
        self.cancel_all_button.setAutoDefault(False)
        self.cancel_all_button.setVisible(self._was_collected)
        self.cancel_all_button.clicked.connect(self._cancel_all)
        buttons.addWidget(self.cancel_all_button)
        buttons.addStretch(1)
        self.cancel_button = QtWidgets.QPushButton("取消")
        self.cancel_button.setObjectName("secondary")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.save_button = QtWidgets.QPushButton("保存")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        self.layout_box.addLayout(buttons)
        self.set_folders(folders)
        self.folder_list.setFocus()

    def selected_ids(self) -> tuple[str, ...]:
        """按列表顺序返回勾选的稳定标识，不因重名或排序改变身份。"""
        return tuple(
            str(self.folder_list.item(row).data(QtCore.Qt.ItemDataRole.UserRole))
            for row in range(self.folder_list.count())
            if self.folder_list.item(row).checkState() == QtCore.Qt.CheckState.Checked
        )

    def set_folders(
        self, folders: Iterable[BookmarkFolder], select_id: str = ""
    ) -> None:
        """刷新可用收藏夹并保留未提交勾选，新建成功时同时勾选新夹。"""
        selected = (
            set(self.selected_ids()) if self._folders else set(self._initial_selected)
        )
        if select_id:
            selected.add(select_id)
        self._folders = tuple(folders)
        blocker = QtCore.QSignalBlocker(self.folder_list)
        self.folder_list.clear()
        for folder in self._folders:
            item = QtWidgets.QListWidgetItem(folder.name)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, folder.id)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                QtCore.Qt.CheckState.Checked
                if folder.id in selected
                else QtCore.Qt.CheckState.Unchecked
            )
            self.folder_list.addItem(item)
        del blocker
        if select_id:
            self.name_input.clear()
        self._error_message = ""
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        """同步多选保存及取消入口的启用状态。"""
        super().set_busy(busy)
        if hasattr(self, "save_button"):
            self.save_button.setEnabled(not busy and bool(self.selected_ids()))
            self.cancel_all_button.setEnabled(not busy)
            self.cancel_button.setEnabled(not busy)

    def _selection_changed(self, item: QtWidgets.QListWidgetItem) -> None:
        """空集合不能经普通保存擦除收藏，必须使用明确的取消全部入口。"""
        self.save_button.setEnabled(not self._busy and bool(self.selected_ids()))

    def _save(self) -> None:
        """提交完整目标集合，异步成功后由页面关闭窗口。"""
        selected = self.selected_ids()
        if self._busy or not selected:
            return
        self.set_busy(True)
        self.saved.emit(selected)

    def _cancel_all(self) -> None:
        """明确提交空归属集合，喜欢状态由独立逻辑保持。"""
        if not self._busy and self._was_collected:
            self.set_busy(True)
            self.cancel_all_requested.emit()


class BookmarkFolderManager(_FolderDialog):
    """管理逻辑收藏夹，删除操作明确说明默认夹兜底行为。"""

    rename_requested = QtCore.Signal(str, str)  # 收藏夹稳定 ID 与新名称。
    delete_requested = QtCore.Signal(str)  # 已确认删除的自定义收藏夹 ID。

    def __init__(
        self,
        folders: Iterable[BookmarkFolder],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """建立单选管理列表，默认收藏夹禁止重命名和删除。"""
        super().__init__(parent)
        self.setWindowTitle("管理收藏夹")
        self.hint.setText(
            "收藏夹是逻辑分类，同一作品可属于多个收藏夹。默认收藏夹会保留。"
        )
        self.folder_list.currentRowChanged.connect(self._selected)
        actions = QtWidgets.QHBoxLayout()
        self.rename_button = QtWidgets.QPushButton("重命名")
        self.rename_button.setObjectName("secondary")
        self.rename_button.setAutoDefault(False)
        self.rename_button.clicked.connect(self._rename)
        actions.addWidget(self.rename_button)
        self.delete_button = QtWidgets.QPushButton("删除收藏夹")
        self.delete_button.setObjectName("secondary")
        self.delete_button.setAutoDefault(False)
        self.delete_button.clicked.connect(self._delete)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        self.close_button = QtWidgets.QPushButton("完成")
        self.close_button.clicked.connect(self.accept)
        actions.addWidget(self.close_button)
        self.layout_box.addLayout(actions)
        self.set_folders(folders)

    def _current_folder(self) -> BookmarkFolder | None:
        """从稳定标识解析当前选中收藏夹。"""
        item = self.folder_list.currentItem()
        folder_id = (
            str(item.data(QtCore.Qt.ItemDataRole.UserRole)) if item is not None else ""
        )
        return next(
            (folder for folder in self._folders if folder.id == folder_id), None
        )

    def set_folders(self, folders: Iterable[BookmarkFolder]) -> None:
        """刷新名称与可用夹，尽量保留管理中的选中项。"""
        current = self._current_folder()
        selected_id = current.id if current is not None else ""
        self._folders = tuple(folders)
        self.folder_list.clear()
        selected_row = 0
        for row, folder in enumerate(self._folders):
            item = QtWidgets.QListWidgetItem(folder.name)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, folder.id)
            item.setToolTip(
                "系统默认收藏夹，不能删除或重命名" if folder.is_system else folder.name
            )
            self.folder_list.addItem(item)
            if folder.id == selected_id:
                selected_row = row
        self.folder_list.setCurrentRow(selected_row if self._folders else -1)
        self.name_input.clear()
        self._error_message = ""
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        """与后台管理事务同步，禁止在提交期间重复操作同一收藏夹。"""
        super().set_busy(busy)
        if hasattr(self, "close_button"):
            self.close_button.setEnabled(not busy)
            self._selected()

    def _selected(self, row: int = -1) -> None:
        """仅为选中的自定义收藏夹启用修改操作。"""
        folder = self._current_folder()
        enabled = not self._busy and folder is not None and not folder.is_system
        self.rename_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def _rename(self) -> None:
        """读取新名称，统一校验后提交稳定 ID 关联的重命名。"""
        folder = self._current_folder()
        if self._busy or folder is None or folder.is_system:
            return
        value, accepted = QtWidgets.QInputDialog.getText(
            self, "重命名收藏夹", "新名称（最多 40 个字符）", text=folder.name
        )
        if not accepted:
            return
        name = self._validated_name(value, folder.id)
        if name is not None:
            self.set_busy(True)
            self.rename_requested.emit(folder.id, name)

    def _delete(self) -> None:
        """确认删除逻辑夹，原件保留且失去全部归属的作品转入默认夹。"""
        folder = self._current_folder()
        if self._busy or folder is None or folder.is_system:
            return
        question = QtWidgets.QMessageBox(self)
        question.setWindowTitle("删除收藏夹")
        question.setIcon(QtWidgets.QMessageBox.Icon.Question)
        question.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        question.setText(
            f"删除“{folder.name}”？\n\n仅在此收藏夹中的作品会转入默认收藏夹；其他归属、喜欢状态及原始文件保持不变。"
        )
        question.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No
        )
        question.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        if question.exec() == QtWidgets.QMessageBox.StandardButton.Yes:
            self.set_busy(True)
            self.delete_requested.emit(folder.id)

"""本地推文时间线的数据模型。"""

from __future__ import annotations

from PySide6 import QtCore

from twitter_downloader.library.models import LibraryWork, PersonalWorkState

WORK_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 1  # 推文实体的自定义数据角色。
PERSONAL_ROLE = WORK_ROLE + 1  # 独立于归档内容的本地喜欢与收藏状态。
PENDING_ROLE = WORK_ROLE + 2  # 当前作品是否有尚未确认的个人状态写入。


class TimelineModel(QtCore.QAbstractListModel):
    """只保存当前分页记录，不在绘制期间访问磁盘或数据库。"""

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        """初始化时间线记录与稳定主键索引。"""
        super().__init__(parent)
        self._works: list[LibraryWork] = []  # 当前已经加载的推文记录。
        self._rows: dict[str, int] = {}  # 稳定主键到可见行号的映射。
        self._personal: dict[str, PersonalWorkState] = {}  # 已加载作品的个人状态快照。
        self._pending: set[str] = set()  # 正在保存的作品主键，不参与内容签名。

    def rowCount(self, parent: QtCore.QModelIndex | None = None) -> int:
        """返回顶层记录数量，时间线不存在子节点。"""
        return 0 if parent is not None and parent.isValid() else len(self._works)

    def data(
        self,
        index: QtCore.QModelIndex,
        role: int = int(QtCore.Qt.ItemDataRole.DisplayRole),
    ) -> object:
        """返回推文实体、辅助阅读文字或完整时间提示。"""
        work = self.work_at(index.row()) if index.isValid() else None
        if work is None:
            return None
        if role == WORK_ROLE:
            return work
        state = self.personal_state(work.key)
        if role == PERSONAL_ROLE:
            return state
        if role == PENDING_ROLE:
            return work.key in self._pending
        if role in (
            int(QtCore.Qt.ItemDataRole.DisplayRole),
            int(QtCore.Qt.ItemDataRole.AccessibleTextRole),
        ):
            personal = f"{'已喜欢' if state.liked else '未喜欢'}，{'已收藏' if state.bookmarked else '未收藏'}"
            return f"{work.display_name or work.username}，@{work.username}，{work.text}，{personal}"
        if role == int(QtCore.Qt.ItemDataRole.ToolTipRole):
            return work.created_at or "发布时间未知"
        return None

    def roleNames(self) -> dict[int, QtCore.QByteArray]:
        """公开自定义角色，便于自动化检查与其他视图复用。"""
        roles = super().roleNames()
        roles[WORK_ROLE] = QtCore.QByteArray(b"work")
        roles[PERSONAL_ROLE] = QtCore.QByteArray(b"personalState")
        roles[PENDING_ROLE] = QtCore.QByteArray(b"personalPending")
        return roles

    def set_works(self, works: list[LibraryWork]) -> None:
        """替换当前查询页，并按稳定主键去重。"""
        unique = {work.key: work for work in works}
        self.beginResetModel()
        self._works = list(unique.values())
        self._rows = {work.key: row for row, work in enumerate(self._works)}
        self.endResetModel()

    def append_works(self, works: list[LibraryWork]) -> None:
        """追加分页记录，避免重叠分页产生重复推文。"""
        added: list[LibraryWork] = []
        seen = set(self._rows)
        for work in works:
            if work.key not in seen:
                seen.add(work.key)
                added.append(work)
        if not added:
            return
        start = len(self._works)
        self.beginInsertRows(QtCore.QModelIndex(), start, start + len(added) - 1)
        self._works.extend(added)
        self._rows.update({work.key: start + n for n, work in enumerate(added)})
        self.endInsertRows()

    def work_at(self, row: int) -> LibraryWork | None:
        """按行号取得推文，越界时返回空值。"""
        return self._works[row] if 0 <= row < len(self._works) else None

    def personal_state(self, key: str) -> PersonalWorkState:
        """从纯内存取得个人状态，未加载时使用明确的空状态。"""
        return self._personal.get(key, PersonalWorkState(key))

    def set_personal_state(
        self, state: PersonalWorkState, pending: bool = False
    ) -> None:
        """仅更新本地动作角色，不重置模型或使媒体布局缓存失效。"""
        self._personal[state.work_key] = state
        if pending:
            self._pending.add(state.work_key)
        else:
            self._pending.discard(state.work_key)
        row = self.row_for_key(state.work_key)
        if row >= 0:
            index = self.index(row, 0)
            self.dataChanged.emit(
                index,
                index,
                [
                    PERSONAL_ROLE,
                    PENDING_ROLE,
                    int(QtCore.Qt.ItemDataRole.AccessibleTextRole),
                ],
            )

    def remove_work(self, key: str) -> bool:
        """按原生行删除通知移除作品，保持其他行的持久模型索引。"""
        row = self.row_for_key(key)
        if row < 0:
            return False
        self.beginRemoveRows(QtCore.QModelIndex(), row, row)
        self._works.pop(row)
        self._rows = {work.key: number for number, work in enumerate(self._works)}
        self.endRemoveRows()
        return True

    def sync_works(self, works: list[LibraryWork]) -> None:
        """用局部增删和移动合并查询结果，避免个人筛选补页重置整个列表。"""
        unique = list({work.key: work for work in works}.values())
        wanted = {work.key for work in unique}
        for row in range(len(self._works) - 1, -1, -1):
            if self._works[row].key not in wanted:
                self.remove_work(self._works[row].key)
        for target, work in enumerate(unique):
            source = self.row_for_key(work.key)
            if source < 0:
                self.beginInsertRows(QtCore.QModelIndex(), target, target)
                self._works.insert(target, work)
                self._rows = {
                    item.key: number for number, item in enumerate(self._works)
                }
                self.endInsertRows()
            elif source != target:
                # 已确认的前缀不再移动；当前目标作品只会从后方向前移。
                self.beginMoveRows(
                    QtCore.QModelIndex(), source, source, QtCore.QModelIndex(), target
                )
                self._works.insert(target, self._works.pop(source))
                self._rows = {
                    item.key: number for number, item in enumerate(self._works)
                }
                self.endMoveRows()
            if self._works[target] != work:
                self._works[target] = work
                index = self.index(target, 0)
                self.dataChanged.emit(
                    index, index, [WORK_ROLE, int(QtCore.Qt.ItemDataRole.DisplayRole)]
                )

    def row_for_key(self, key: str) -> int:
        """按稳定主键取得行号，缺失时返回负一。"""
        return self._rows.get(key, -1)

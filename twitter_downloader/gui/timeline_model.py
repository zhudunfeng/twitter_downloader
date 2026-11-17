"""本地推文时间线的数据模型。"""

from __future__ import annotations

from PySide6 import QtCore

from twitter_downloader.library.models import LibraryWork

WORK_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 1  # 推文实体的自定义数据角色。


class TimelineModel(QtCore.QAbstractListModel):
    """只保存当前分页记录，不在绘制期间访问磁盘或数据库。"""

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        """初始化时间线记录与稳定主键索引。"""
        super().__init__(parent)
        self._works: list[LibraryWork] = []  # 当前已经加载的推文记录。
        self._rows: dict[str, int] = {}  # 稳定主键到可见行号的映射。

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
        if role in (
            int(QtCore.Qt.ItemDataRole.DisplayRole),
            int(QtCore.Qt.ItemDataRole.AccessibleTextRole),
        ):
            return (
                f"{work.display_name or work.username}，@{work.username}，{work.text}"
            )
        if role == int(QtCore.Qt.ItemDataRole.ToolTipRole):
            return work.created_at or "发布时间未知"
        return None

    def roleNames(self) -> dict[int, QtCore.QByteArray]:
        """公开自定义角色，便于自动化检查与其他视图复用。"""
        roles = super().roleNames()
        roles[WORK_ROLE] = QtCore.QByteArray(b"work")
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

    def row_for_key(self, key: str) -> int:
        """按稳定主键取得行号，缺失时返回负一。"""
        return self._rows.get(key, -1)

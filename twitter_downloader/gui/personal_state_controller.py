"""在后台串行保存本地喜欢与收藏夹，并向所有阅读界面同步状态。"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6 import QtCore

from twitter_downloader.library.models import BookmarkFolder, PersonalWorkState
from twitter_downloader.library.personal_state import PersonalStateRepository

logger = logging.getLogger(__name__)

PENDING_LIMIT = 128  # 执行中与尚未执行的个人写入数量上限。
SHUTDOWN_WAIT_MS = 10_000  # 关闭时等待已接受操作落盘的单次最长毫秒数。
_WORK_ACTIONS = frozenset(("set_liked", "set_folders"))  # 可以按作品合并的动作。
_FOLDER_ACTIONS = frozenset(
    ("create_folder", "rename_folder", "delete_folder")
)  # 收藏夹结构变更构成队列合并边界。


@dataclass(frozen=True, slots=True)
class _Command:
    """记录一次绝对目标状态操作，不向数据库发送相对切换指令。"""

    serial: int  # 当前控制器中严格递增的请求标识。
    action: str  # 已校验的写入动作名称。
    key: str = ""  # 作品稳定键，或改名与删除所需的收藏夹标识。
    value: bool | str | tuple[str, ...] | None = None  # 操作所需的不可变目标值。


@dataclass(frozen=True, slots=True)
class _Outcome:
    """传递已提交结果或失败说明，不能包含线程所属的数据库连接。"""

    command: _Command  # 产生结果的原始请求。
    result: object = None  # 提交后的实体或删除收藏夹所影响的状态映射。
    folders: tuple[BookmarkFolder, ...] | None = None  # 结构操作后的收藏夹快照。
    error: str = ""  # 业务可展示的中文失败说明，成功时为空。
    unavailable: bool = False  # 确定的数据库损坏会停止本线程后续写入。


class _PersonalWorker(QtCore.QThread):
    """独占一个数据库连接，并用有界条件队列依次提交个人操作。"""

    updated = QtCore.Signal()  # 通知主线程提取已完成结果，不依赖退出时处理事件。

    def __init__(self, path: Path, parent: QtCore.QObject) -> None:
        """保存纯数据参数；SQLite 连接稍后在工作线程中创建。"""
        super().__init__(parent)
        self.path = path  # 独立的用户个人数据库路径。
        self._condition = threading.Condition()  # 保护待执行命令及完成结果。
        self._queue: deque[_Command] = deque()  # 等待执行且允许合并的有界队列。
        self._outcomes: deque[_Outcome] = deque()  # 等待 GUI 确认的已完成结果。
        self._accepting = True  # 关闭后拒绝新命令，但继续排空已经接受的操作。
        self._active = False  # 当前是否有一个已从队列取出的命令。

    def submit(self, command: _Command) -> tuple[bool, int | None]:
        """接受命令并合并同一结构边界内尚未开始的同类目标。"""
        with self._condition:
            if not self._accepting:
                return False, None
            if command.action in _WORK_ACTIONS:
                for position in range(len(self._queue) - 1, -1, -1):
                    previous = self._queue[position]
                    if previous.action in _FOLDER_ACTIONS:
                        break
                    if (previous.action, previous.key) == (command.action, command.key):
                        # 只替换尚未开始的命令；收藏夹删除与重命名前后不能跨界合并。
                        self._queue[position] = command
                        self._condition.notify()
                        return True, previous.serial
            if len(self._queue) + int(self._active) >= PENDING_LIMIT:
                return False, None
            self._queue.append(command)
            self._condition.notify()
            return True, None

    def take_outcomes(self) -> list[_Outcome]:
        """一次提取所有完成结果，保证关闭后的同步排空不会重复回调。"""
        with self._condition:
            outcomes = list(self._outcomes)
            self._outcomes.clear()
            return outcomes

    def close_when_idle(self) -> None:
        """停止接受新操作，唤醒线程并在已接受队列排空后结束。"""
        with self._condition:
            self._accepting = False
            self._condition.notify()

    def _publish(self, outcome: _Outcome) -> None:
        """先保留完成结果再发信号，避免 GUI 等待退出时丢失最后一次提交。"""
        with self._condition:
            self._outcomes.append(outcome)
        self.updated.emit()

    def run(self) -> None:
        """在线程内初始化、执行短事务并最终关闭个人数据库连接。"""
        repository: PersonalStateRepository | None = None
        initial = _Command(0, "initialize")
        try:
            try:
                repository = PersonalStateRepository(self.path)
                folders = tuple(repository.list_folders())
            except Exception as exc:
                logger.exception("个人数据初始化失败，保留原数据库：%s", self.path)
                self._publish(_Outcome(initial, error=f"个人数据暂不可用：{exc}"))
                return
            self._publish(_Outcome(initial, folders=folders))
            while True:
                with self._condition:
                    while not self._queue and self._accepting:
                        self._condition.wait()
                    if not self._queue:
                        return
                    command = self._queue.popleft()
                    self._active = True
                try:
                    self._publish(self._execute(repository, command))
                except Exception as exc:
                    logger.exception(
                        "个人操作保存失败：%s；%s", command.action, command.key
                    )
                    labels = {
                        "set_liked": "喜欢状态",
                        "set_folders": "作品收藏",
                        "create_folder": "新建收藏夹",
                        "rename_folder": "重命名收藏夹",
                        "delete_folder": "删除收藏夹",
                    }
                    label = labels.get(command.action, "个人操作")
                    code = getattr(exc, "sqlite_errorcode", 0)
                    fatal = isinstance(code, int) and (code & 0xFF) in (
                        sqlite3.SQLITE_CORRUPT,
                        sqlite3.SQLITE_NOTADB,
                    )
                    message = f"{label}保存失败：{exc}"
                    self._publish(_Outcome(command, error=message, unavailable=fatal))
                    if fatal:
                        # 确定损坏不能继续尝试写入；逐项报告已接受目标未保存。
                        with self._condition:
                            self._accepting = False
                            waiting = list(self._queue)
                            self._queue.clear()
                        for pending in waiting:
                            self._publish(
                                _Outcome(
                                    pending,
                                    error="个人数据损坏，已保留原库；该修改未保存，请恢复后重试",
                                    unavailable=True,
                                )
                            )
                        return
                finally:
                    with self._condition:
                        self._active = False
        finally:
            with self._condition:
                self._accepting = False
            if repository is not None:
                repository.close()

    def _execute(
        self, repository: PersonalStateRepository, command: _Command
    ) -> _Outcome:
        """调用固定仓储方法，提交后再返回状态及必要的收藏夹快照。"""
        result: object
        if command.action == "set_liked":
            result = repository.set_liked(command.key, bool(command.value))
        elif command.action == "set_folders":
            if not isinstance(command.value, tuple):
                raise ValueError("收藏夹选择参数无效")
            result = repository.set_folders(command.key, command.value)
        elif command.action == "create_folder":
            result = repository.create_folder(str(command.value))
        elif command.action == "rename_folder":
            result = repository.rename_folder(command.key, str(command.value))
        elif command.action == "delete_folder":
            result = repository.delete_folder(command.key)
        else:
            raise ValueError("不支持的个人操作")
        folders = (
            tuple(repository.list_folders())
            if command.action in _FOLDER_ACTIONS
            else None
        )
        return _Outcome(command, result=result, folders=folders)


class PersonalStateController(QtCore.QObject):
    """维护已确认与待保存状态，供列表、详情和收藏夹窗口共同使用。"""

    ready = QtCore.Signal(object)  # 首次或重试初始化成功后的收藏夹元组。
    state_changed = QtCore.Signal(object)  # 某作品应立即显示的个人状态快照。
    folders_changed = QtCore.Signal(object)  # 已确认的完整收藏夹元组。
    operation_finished = QtCore.Signal(str, object)  # 已提交动作名称及仓储返回值。
    operation_failed = QtCore.Signal(str, str)  # 未保存操作的动作及稳定键，用于定向解锁表单。
    failed = QtCore.Signal(str)  # 用户可展示且可重试的中文错误。
    busy_changed = QtCore.Signal(bool)  # 是否仍有初始化或写入未完成。

    def __init__(self, path: str | Path, parent: QtCore.QObject | None = None) -> None:
        """建立 GUI 状态容器，并延迟到事件循环开始后接收初始化结果。"""
        super().__init__(parent)
        self.path = Path(path)  # 个人数据库位置，不属于可删除的索引缓存。
        self.available = False  # 数据库成功初始化后才开放个人功能。
        self.folders: tuple[BookmarkFolder, ...] = ()  # 已确认的收藏夹列表。
        self.states: dict[str, PersonalWorkState] = {}  # 合并待保存意图后的显示状态。
        self.last_error = ""  # 最近一次可展示错误，成功操作会清空。
        self._confirmed: dict[str, PersonalWorkState] = {}  # 数据库已确认的版本基线。
        self._pending: dict[int, _Command] = {}  # 已接受且尚未处理完成的操作。
        self._serial = 0  # 本控制器的请求版本计数，不替代数据库 revision。
        self._closing = False  # 退出排空期间拒绝新增个人写入。
        self._shutdown_failed = False  # 关闭等待期间发生保存失败时保留主窗口。
        self._initializing = True  # 后台初始化也计入忙碌状态。
        self._reported_busy = True  # 上次已发出的忙碌状态，减少无效信号。
        self._last_failed: _Command | None = None  # 可显式重试的最后一个失败目标。
        self._worker: _PersonalWorker | None = None  # 控制器拥有的唯一串行线程。
        self._start_worker()

    @property
    def busy(self) -> bool:
        """返回是否仍有初始化或已接受的个人写入等待确认。"""
        return self._initializing or bool(self._pending)

    @property
    def has_failed_operation(self) -> bool:
        """返回是否仍保留一个尚未成功重试的失败目标。"""
        return self._last_failed is not None

    def _start_worker(self) -> None:
        """启动新的数据库所属线程，重试也不会移动或重建损坏个人库。"""
        self._initializing = True
        self._worker = _PersonalWorker(self.path, self)
        self._worker.updated.connect(self._drain_results)
        self._worker.start()
        self._update_busy()

    def state_for(self, key: str) -> PersonalWorkState:
        """只从内存读取显示状态，未水合的作品返回未标记实体。"""
        return self.states.get(key, PersonalWorkState(key))

    def is_pending(self, key: str) -> bool:
        """检查指定作品是否仍有已接受的喜欢或收藏操作。"""
        return any(
            command.key == key and command.action in _WORK_ACTIONS
            for command in self._pending.values()
        )

    def hydrate(self, states: dict[str, PersonalWorkState]) -> None:
        """合并后台查询快照，绝不让旧 revision 或快照覆盖待保存意图。"""
        for key, state in states.items():
            previous = self._confirmed.get(key)
            if previous is not None and state.revision < previous.revision:
                continue
            self._confirmed[key] = state
            self._refresh_state(key)

    def set_liked(self, key: str, enabled: bool) -> bool:
        """提交喜欢的绝对目标值，立即显示最新意图并标记保存中。"""
        return self._enqueue("set_liked", key, bool(enabled))

    def set_folders(self, key: str, folder_ids: tuple[str, ...] | list[str]) -> bool:
        """提交完整目标归属集合，后台在一个事务中保存多收藏夹关系。"""
        return self._enqueue("set_folders", key, tuple(sorted(set(folder_ids))))

    def create_folder(self, name: str) -> bool:
        """在同一写队列中创建收藏夹，名称校验交由共享仓储规则完成。"""
        return self._enqueue("create_folder", value=name)

    def rename_folder(self, folder_id: str, name: str) -> bool:
        """串行修改收藏夹名称，稳定标识和作品归属保持原样。"""
        return self._enqueue("rename_folder", folder_id, name)

    def delete_folder(self, folder_id: str) -> bool:
        """提交收藏夹删除，由仓储原子地将孤立作品转入默认夹。"""
        return self._enqueue("delete_folder", folder_id)

    def retry(self) -> bool:
        """重试上次失败目标，或在初始化失败后重新打开现有个人库。"""
        self._drain_results()
        if self._closing:
            if self._worker is not None and self._worker.isRunning():
                return False
            # 超时后旧线程已排空，显式重试才解除关闭态并启动可接收写入的新线程。
            self._closing = False
        # 新目标仍在保存时不能把旧失败目标排到其后，避免重试覆盖用户的新意图。
        if self.busy:
            return False
        if self.available:
            command = self._last_failed
            if command is None:
                return False
            return self._enqueue(command.action, command.key, command.value)
        if self._worker is not None:
            if self._worker.isRunning():
                return False
            self._worker.deleteLater()
        self.last_error = ""
        self._start_worker()
        return True

    def _enqueue(
        self,
        action: str,
        key: str = "",
        value: bool | str | tuple[str, ...] | None = None,
    ) -> bool:
        """有界接受新意图，只有线程确认接受后才改变界面显示状态。"""
        self._drain_results()
        if self._closing or self._worker is None:
            self.failed.emit("正在关闭个人数据，未接受新的修改")
            return False
        if not self.available and not self._initializing:
            self.failed.emit(self.last_error or "个人数据不可用，请重试后再修改")
            return False
        self._serial += 1
        command = _Command(self._serial, action, key, value)
        accepted, replaced_serial = self._worker.submit(command)
        if not accepted:
            self.failed.emit("个人操作队列暂时已满或正在关闭，请稍后重试")
            return False
        if replaced_serial is not None:
            self._pending.pop(replaced_serial, None)
        self._pending[command.serial] = command
        if action in _WORK_ACTIONS:
            self._refresh_state(key, force=True)
        self._update_busy()
        return True

    def _refresh_state(self, key: str, *, force: bool = False) -> None:
        """将同一作品最新待保存目标叠加到已确认基线，不覆盖另一类动作。"""
        state = self._confirmed.get(key, PersonalWorkState(key))
        now = int(time.time() * 1000)
        for serial in sorted(self._pending):
            command = self._pending[serial]
            if command.key != key:
                continue
            if command.action == "set_liked":
                liked_at = (state.liked_at or now) if command.value else None
                state = replace(state, liked_at=liked_at)
            elif command.action == "set_folders" and isinstance(command.value, tuple):
                collected_at = (state.collected_at or now) if command.value else None
                state = replace(
                    state, folder_ids=command.value, collected_at=collected_at
                )
        changed = self.states.get(key) != state
        self.states[key] = state
        if changed or force:
            self.state_changed.emit(state)

    @QtCore.Slot()
    def _drain_results(self) -> None:
        """在 GUI 线程处理已提交结果；退出等待后也可同步调用本方法。"""
        if self._worker is None:
            return
        for outcome in self._worker.take_outcomes():
            self._handle_outcome(outcome)
        self._update_busy()

    def _handle_outcome(self, outcome: _Outcome) -> None:
        """更新确认版本，再重新叠加更晚意图，最后通知界面执行查询校正。"""
        command = outcome.command
        if command.action == "initialize":
            self._initializing = False
            if outcome.error:
                self._shutdown_failed = self._shutdown_failed or self._closing
                self.available = False
                affected = {
                    pending.key
                    for pending in self._pending.values()
                    if pending.action in _WORK_ACTIONS
                }
                rejected = list(self._pending.values())
                self._pending.clear()
                for key in affected:
                    self._refresh_state(key, force=True)
                self.last_error = outcome.error
                for pending in rejected:
                    self.operation_failed.emit(pending.action, pending.key)
                self.failed.emit(outcome.error)
                return
            self.available = not self._closing
            self.folders = outcome.folders or ()
            self.last_error = ""
            if not self._closing:
                self.folders_changed.emit(self.folders)
                self.ready.emit(self.folders)
            return
        self._pending.pop(command.serial, None)
        if outcome.error:
            self._shutdown_failed = self._shutdown_failed or self._closing
            if outcome.unavailable:
                self.available = False
            newer = any(
                pending.action == command.action
                and pending.key == command.key
                and pending.serial > command.serial
                for pending in self._pending.values()
            )
            if not newer:
                self._last_failed = command
            self.last_error = outcome.error
            if command.action in _WORK_ACTIONS:
                self._refresh_state(command.key, force=True)
            if not newer:
                self.operation_failed.emit(command.action, command.key)
            self.failed.emit(outcome.error)
            return
        self.last_error = ""
        if self._last_failed is not None and (
            self._last_failed.action,
            self._last_failed.key,
        ) == (command.action, command.key):
            self._last_failed = None
        result = outcome.result
        if isinstance(result, PersonalWorkState):
            self._accept_state(result)
        elif command.action == "delete_folder" and isinstance(result, dict):
            for state in result.values():
                if isinstance(state, PersonalWorkState):
                    self._accept_state(state)
        if outcome.folders is not None:
            self.folders = outcome.folders
            self.folders_changed.emit(self.folders)
        logger.info("个人操作已保存：%s；%s", command.action, command.key)
        self.operation_finished.emit(command.action, result)

    def _accept_state(self, state: PersonalWorkState) -> None:
        """接受仓储提交版本，同时保留同作品尚未完成的最新目标。"""
        previous = self._confirmed.get(state.work_key)
        if previous is None or state.revision >= previous.revision:
            self._confirmed[state.work_key] = state
        self._refresh_state(state.work_key, force=True)

    def _update_busy(self) -> None:
        """只在忙碌状态发生变化时通知共享界面。"""
        busy = self.busy
        if busy != self._reported_busy:
            self._reported_busy = busy
            self.busy_changed.emit(busy)

    def shutdown(self) -> bool:
        """停止接受操作并等待队列落盘；超时返回失败让主窗口保留线程。"""
        self._closing = True
        self._shutdown_failed = False
        self.available = False
        worker = self._worker
        if worker is None:
            return True
        worker.close_when_idle()
        finished = worker.wait(SHUTDOWN_WAIT_MS)
        # wait 期间 GUI 不处理排队信号，必须主动取回已经落盘的最后一批结果。
        self._drain_results()
        if not finished:
            logger.warning("个人操作仍在保存，暂缓关闭窗口")
            self.failed.emit("个人操作仍在保存，请稍后再次关闭窗口")
            return False
        self._initializing = False
        self._update_busy()
        if self._shutdown_failed:
            # 让主窗口维持开启；用户可重试打开个人库并重新提交失败目标。
            self._closing = False
            return False
        return not self._pending

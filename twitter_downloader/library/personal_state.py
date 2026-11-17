"""独立保存不可由下载原件重建的喜欢与多收藏夹数据。"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import unicodedata
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import BookmarkFolder, PersonalWorkState

logger = logging.getLogger(__name__)
PERSONAL_SCHEMA_VERSION = 1  # 个人数据版本，禁止按目录缓存的策略删除重建。
DEFAULT_FOLDER_ID = "default"  # 默认收藏夹固定主键，不受展示名称影响。
DEFAULT_FOLDER_NAME = "默认收藏夹"  # 系统内置收藏夹名称。
FOLDER_NAME_LIMIT = 40  # 收藏夹名称允许的最大 Unicode 字符数。
PERSONAL_SQLITE_TIMEOUT = 3.0  # 个人操作遇到锁竞争时的最大 SQLite 等待秒数。
STATE_BATCH_SIZE = 400  # 批量查询主键数，避免超过 SQLite 绑定参数限制。
_INITIALIZATION_LOCK = threading.Lock()  # 同进程首次建立个人库时串行检查结构。


def validate_folder_name(name: str) -> tuple[str, str]:
    """统一校验收藏夹名称并生成支持 Unicode 的唯一性比较键。"""
    if not isinstance(name, str):
        raise TypeError("收藏夹名称必须是文字")
    display = name.strip()
    if not display:
        raise ValueError("请输入收藏夹名称")
    if len(display) > FOLDER_NAME_LIMIT:
        raise ValueError(f"收藏夹名称不能超过 {FOLDER_NAME_LIMIT} 个字符")
    if any(unicodedata.category(char).startswith("C") for char in display):
        raise ValueError("收藏夹名称不能包含换行或控制字符")
    return display, unicodedata.normalize("NFKC", display).casefold()


def _validated_key(work_key: str) -> str:
    """拒绝空作品标识，避免操作被错误地保存为公共空记录。"""
    if not isinstance(work_key, str) or not work_key or "\x00" in work_key:
        raise ValueError("作品标识无效")
    return work_key


def _read_states(
    connection: sqlite3.Connection, keys: Iterable[str], schema: str
) -> dict[str, PersonalWorkState]:
    """在已有事务快照内分批读取状态，缺失记录返回未标记实体。"""
    if schema not in ("main", "personal"):
        raise ValueError("个人数据查询范围无效")
    unique = tuple(dict.fromkeys(_validated_key(key) for key in keys))
    states = {key: PersonalWorkState(key) for key in unique}
    for start in range(0, len(unique), STATE_BATCH_SIZE):
        batch = unique[start : start + STATE_BATCH_SIZE]
        placeholders = ",".join("?" for _ in batch)
        memberships: dict[str, list[str]] = {}
        for row in connection.execute(
            f"SELECT work_key, folder_id FROM {schema}.folder_items "
            f"WHERE work_key IN ({placeholders}) ORDER BY folder_id",
            batch,
        ):
            memberships.setdefault(str(row[0]), []).append(str(row[1]))
        for row in connection.execute(
            f"SELECT work_key, liked_at, collected_at, revision FROM {schema}.work_state "
            f"WHERE work_key IN ({placeholders})",
            batch,
        ):
            key = str(row[0])
            states[key] = PersonalWorkState(
                key, row[1], row[2], tuple(memberships.pop(key, ())), int(row[3])
            )
        # 即使旧库中出现不完整的排序元数据，收藏真值仍由归属关系决定。
        for key, folders in memberships.items():
            states[key] = PersonalWorkState(key, folder_ids=tuple(folders))
    return states


def read_states(
    connection: sqlite3.Connection, keys: Iterable[str], schema: str = "main"
) -> dict[str, PersonalWorkState]:
    """在同一短快照内读取归属和时间，避免并发保存造成半新半旧状态。"""
    owns_snapshot = not connection.in_transaction
    if owns_snapshot:
        connection.execute("BEGIN")
    try:
        return _read_states(connection, keys, schema)
    finally:
        if owns_snapshot:
            # 只释放自己建立的只读快照，不提交或回滚调用方的个人写事务。
            connection.rollback()


class PersonalStateRepository:
    """在创建它的线程内管理持久个人库，任何错误都不会清空重建。"""

    def __init__(self, path: str | Path) -> None:
        """创建或验证独立个人库，失败时保留数据库和所有原始下载。"""
        self.path = Path(path)  # 当前用户的持久个人数据库路径。
        self._connection: sqlite3.Connection | None = None  # 仅所属线程可使用。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _INITIALIZATION_LOCK:
                connection = sqlite3.connect(
                    self.path, timeout=PERSONAL_SQLITE_TIMEOUT, isolation_level=None
                )
                self._connection = connection
                connection.row_factory = sqlite3.Row
                self._initialize()
        except Exception:
            self.close()
            logger.exception("个人数据无法打开，已保留原库：%s", self.path)
            raise

    def _db(self) -> sqlite3.Connection:
        """取得尚未关闭的本线程连接。"""
        if self._connection is None:
            raise RuntimeError("个人数据库已经关闭")
        return self._connection

    def _initialize(self) -> None:
        """校验版本后建立首版结构，普通打开不执行迁移或覆盖数据。"""
        connection = self._db()
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > PERSONAL_SCHEMA_VERSION:
            raise RuntimeError("个人数据由更新版本创建，请使用新版应用打开")
        if version not in (0, PERSONAL_SCHEMA_VERSION):
            raise RuntimeError("个人数据版本不受支持，已保留原库")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=EXTRA")
        if version == PERSONAL_SCHEMA_VERSION:
            self._validate_schema()
            return
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if tables:
            # 另一个进程可能刚好在两次读取之间完成首建，先重读版本再判断未知数据。
            if (
                int(connection.execute("PRAGMA user_version").fetchone()[0])
                == PERSONAL_SCHEMA_VERSION
            ):
                self._validate_schema()
                return
            raise RuntimeError("个人数据缺少有效版本，已保留原库，请先备份并恢复")
        connection.execute("PRAGMA journal_mode=DELETE")
        with self._transaction():
            # 跨进程竞争建库时，取得写锁后重新确认，不能覆盖刚建立的数据。
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == PERSONAL_SCHEMA_VERSION:
                return
            if version != 0:
                raise RuntimeError("个人数据版本已改变，请重新打开应用")
            connection.execute(
                "CREATE TABLE work_state (work_key TEXT PRIMARY KEY, liked_at INTEGER, "
                "collected_at INTEGER, revision INTEGER NOT NULL DEFAULT 0)"
            )
            connection.execute(
                "CREATE TABLE folders (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "name_key TEXT NOT NULL UNIQUE, is_system INTEGER NOT NULL CHECK(is_system IN (0,1)), "
                "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE folder_items (folder_id TEXT NOT NULL REFERENCES folders(id) "
                "ON DELETE CASCADE, work_key TEXT NOT NULL, added_at INTEGER NOT NULL, "
                "PRIMARY KEY(folder_id,work_key))"
            )
            connection.execute(
                "CREATE INDEX personal_work_folders ON folder_items(work_key,folder_id)"
            )
            connection.execute(
                "CREATE INDEX personal_liked ON work_state(liked_at DESC,work_key)"
            )
            connection.execute(
                "CREATE INDEX personal_collected ON work_state(collected_at DESC,work_key)"
            )
            connection.execute(
                "CREATE INDEX personal_folder_order ON folder_items(folder_id,added_at DESC,work_key)"
            )
            now = time.time_ns() // 1_000_000
            display, name_key = validate_folder_name(DEFAULT_FOLDER_NAME)
            connection.execute(
                "INSERT INTO folders VALUES (?,?,?,?,?,?)",
                (DEFAULT_FOLDER_ID, display, name_key, 1, now, now),
            )
            connection.execute(f"PRAGMA user_version={PERSONAL_SCHEMA_VERSION}")
        logger.info("已建立独立个人数据库：%s", self.path)
        self._validate_schema()

    def _validate_schema(self) -> None:
        """验证必需字段与系统收藏夹，损坏时禁止假装空库继续写入。"""
        connection = self._db()
        connection.execute(
            "SELECT work_key,liked_at,collected_at,revision FROM work_state LIMIT 0"
        )
        connection.execute(
            "SELECT folder_id,work_key,added_at FROM folder_items LIMIT 0"
        )
        connection.execute(
            "SELECT id,name,name_key,is_system,created_at,updated_at FROM folders LIMIT 0"
        )
        default = connection.execute(
            "SELECT is_system FROM folders WHERE id=?", (DEFAULT_FOLDER_ID,)
        ).fetchone()
        if default is None or int(default[0]) != 1:
            raise RuntimeError("个人数据中的默认收藏夹无效，已保留原库")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """以短写事务原子保存一个用户动作，任何异常均完整回滚。"""
        connection = self._db()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def list_folders(self) -> tuple[BookmarkFolder, ...]:
        """返回默认夹优先、其余按创建时间稳定排序的收藏夹。"""
        rows = self._db().execute(
            "SELECT id,name,is_system,created_at,updated_at FROM folders "
            "ORDER BY is_system DESC,created_at ASC,id ASC"
        )
        return tuple(
            BookmarkFolder(
                str(row[0]), str(row[1]), bool(row[2]), int(row[3]), int(row[4])
            )
            for row in rows
        )

    def get_states(self, keys: Iterable[str]) -> dict[str, PersonalWorkState]:
        """批量取得作品标记，未操作过的作品也有明确未标记状态。"""
        return read_states(self._db(), keys)

    def _state(self, work_key: str) -> PersonalWorkState:
        """在当前事务内读取一条完整状态。"""
        return self.get_states((work_key,))[work_key]

    def set_liked(self, work_key: str, enabled: bool) -> PersonalWorkState:
        """幂等设置喜欢，绝不回写或覆盖收藏归属。"""
        key = _validated_key(work_key)
        if not isinstance(enabled, bool):
            raise TypeError("喜欢状态必须为布尔值")
        with self._transaction() as connection:
            previous = self._state(key)
            if previous.liked == enabled:
                return previous
            now = time.time_ns() // 1_000_000
            connection.execute(
                "INSERT INTO work_state(work_key,liked_at,revision) VALUES (?,?,1) "
                "ON CONFLICT(work_key) DO UPDATE SET liked_at=excluded.liked_at, "
                "revision=work_state.revision+1",
                (key, now if enabled else None),
            )
            state = self._state(key)
        logger.info("已%s本地喜欢：%s", "添加" if enabled else "取消", key)
        return state

    def set_folders(
        self, work_key: str, folder_ids: Iterable[str]
    ) -> PersonalWorkState:
        """原子替换完整收藏夹集合，重复保存不改时间或版本。"""
        key = _validated_key(work_key)
        if isinstance(folder_ids, str):
            raise TypeError("请选择有效的收藏夹列表")
        values = tuple(folder_ids)
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError("收藏夹标识无效")
        desired = tuple(sorted(set(values)))
        with self._transaction() as connection:
            previous = self._state(key)
            for folder_id in desired:
                self._folder(folder_id)
            if previous.folder_ids == desired:
                return previous
            old_ids, new_ids = set(previous.folder_ids), set(desired)
            now = time.time_ns() // 1_000_000
            for folder_id in old_ids - new_ids:
                connection.execute(
                    "DELETE FROM folder_items WHERE folder_id=? AND work_key=?",
                    (folder_id, key),
                )
            for folder_id in new_ids - old_ids:
                connection.execute(
                    "INSERT INTO folder_items VALUES (?,?,?)", (folder_id, key, now)
                )
            # 收藏时间描述连续收藏周期；在多个收藏夹之间调整不重排全部收藏。
            collected_at = (
                previous.collected_at
                if old_ids and new_ids
                else now
                if new_ids
                else None
            )
            connection.execute(
                "INSERT INTO work_state(work_key,collected_at,revision) VALUES (?,?,1) "
                "ON CONFLICT(work_key) DO UPDATE SET collected_at=excluded.collected_at, "
                "revision=work_state.revision+1",
                (key, collected_at),
            )
            state = self._state(key)
        logger.info("已保存作品收藏夹：%s，共 %d 个", key, len(desired))
        return state

    def _folder(self, folder_id: str) -> BookmarkFolder:
        """按稳定标识重验收藏夹，防止已删除的选择被静默保存。"""
        row = (
            self._db()
            .execute(
                "SELECT id,name,is_system,created_at,updated_at FROM folders WHERE id=?",
                (folder_id,),
            )
            .fetchone()
        )
        if row is None:
            raise ValueError("收藏夹已不存在，请刷新后重新选择")
        return BookmarkFolder(
            str(row[0]), str(row[1]), bool(row[2]), int(row[3]), int(row[4])
        )

    def create_folder(self, name: str) -> BookmarkFolder:
        """创建规范化名称唯一的自定义收藏夹。"""
        display, name_key = validate_folder_name(name)
        folder_id, now = str(uuid.uuid4()), time.time_ns() // 1_000_000
        try:
            with self._transaction() as connection:
                connection.execute(
                    "INSERT INTO folders VALUES (?,?,?,?,?,?)",
                    (folder_id, display, name_key, 0, now, now),
                )
                folder = self._folder(folder_id)
        except sqlite3.IntegrityError as exc:
            raise ValueError("已存在同名收藏夹") from exc
        logger.info("已新建收藏夹：%s", display)
        return folder

    def rename_folder(self, folder_id: str, name: str) -> BookmarkFolder:
        """重命名自定义收藏夹，保持稳定标识和作品关系。"""
        display, name_key = validate_folder_name(name)
        try:
            with self._transaction() as connection:
                previous = self._folder(folder_id)
                if previous.is_system:
                    raise ValueError("默认收藏夹不能重命名")
                if previous.name == display:
                    return previous
                connection.execute(
                    "UPDATE folders SET name=?,name_key=?,updated_at=? WHERE id=?",
                    (display, name_key, time.time_ns() // 1_000_000, folder_id),
                )
                folder = self._folder(folder_id)
        except sqlite3.IntegrityError as exc:
            raise ValueError("已存在同名收藏夹") from exc
        logger.info("已重命名收藏夹：%s", display)
        return folder

    def delete_folder(self, folder_id: str) -> dict[str, PersonalWorkState]:
        """删除自定义夹，并将无其他归属的作品原子转入默认夹。"""
        with self._transaction() as connection:
            folder = self._folder(folder_id)
            if folder.is_system:
                raise ValueError("默认收藏夹不能删除")
            items = connection.execute(
                "SELECT work_key,added_at FROM folder_items WHERE folder_id=?",
                (folder_id,),
            ).fetchall()
            # 集合 SQL 一次转移孤儿，避免大收藏夹逐作品发出数万次查询。
            connection.execute(
                "INSERT INTO folder_items(folder_id,work_key,added_at) "
                "SELECT ?,fi.work_key,fi.added_at FROM folder_items AS fi "
                "WHERE fi.folder_id=? AND NOT EXISTS (SELECT 1 FROM folder_items AS other "
                "WHERE other.work_key=fi.work_key AND other.folder_id<>?)",
                (DEFAULT_FOLDER_ID, folder_id, folder_id),
            )
            connection.execute(
                "UPDATE work_state SET revision=revision+1 WHERE work_key IN "
                "(SELECT work_key FROM folder_items WHERE folder_id=?)",
                (folder_id,),
            )
            connection.execute("DELETE FROM folders WHERE id=?", (folder_id,))
            changed = self.get_states(str(row[0]) for row in items)
        logger.info("已删除收藏夹：%s，保留 %d 条作品收藏", folder.name, len(changed))
        return changed

    def backup_to(self, destination: str | Path) -> None:
        """使用 SQLite 在线备份获得一致副本，供升级前备份与显式恢复使用。"""
        target = Path(destination)
        if target.resolve() == self.path.resolve() or target.exists():
            raise ValueError("个人数据备份目标已存在或与原库相同")
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = sqlite3.connect(target)
        try:
            self._db().backup(backup)
        finally:
            backup.close()
        logger.info("个人数据一致备份已保存：%s", target)

    def close(self) -> None:
        """关闭本线程个人连接，不删除用户数据或侧文件。"""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

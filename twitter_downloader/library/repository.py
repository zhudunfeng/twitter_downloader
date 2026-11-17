"""线程内使用的可重建 SQLite 作品索引。"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .models import LibraryQuery, LibraryWork, LocalMedia, PersonalWorkState
from .personal_state import DEFAULT_FOLDER_ID, PERSONAL_SCHEMA_VERSION, read_states

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 2  # 索引结构及相对路径存储版本；升级不修改原始下载文件。
SQLITE_TIMEOUT = 10.0  # 多个后台任务短暂并发访问索引时的锁等待秒数。
_INITIALIZATION_LOCK = (
    threading.Lock()
)  # 串行化同进程首次建库和损坏恢复，避免并发改写日志模式。
_LOCK_ERRORS = frozenset({5, 6})  # SQLite 标准 BUSY/LOCKED 错误码，兼容 Python 3.10。
_CORRUPT_ERRORS = frozenset({11, 26})  # SQLite 标准 CORRUPT/NOTADB 错误码。


def _sqlite_error_code(error: sqlite3.DatabaseError) -> int:
    """读取标准错误码，旧版 Python 仅对已知 SQLite 原生错误文本作回退。"""
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 0xFF
    message = str(error).casefold()
    for phrase, fallback in (
        ("database is locked", 5),
        ("database is busy", 5),
        ("database table is locked", 6),
        ("database schema is locked", 6),
        ("database disk image is malformed", 11),
        ("file is not a database", 26),
    ):
        if phrase in message:
            return fallback
    return 0


def _absolute_path(value: object) -> Path:
    """仅规范化绝对本地路径，不访问磁盘或离线网络共享。"""
    if not isinstance(value, str) or not value or "\x00" in value or "://" in value:
        raise ValueError("作品索引包含无效本地路径")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("作品索引需要绝对下载根目录")
    return Path(os.path.abspath(path))


def _relative_path(base: Path, value: object) -> Path:
    """在已验证目录内展开相对路径，拒绝父级跳转、盘符和远程链接。"""
    if not isinstance(value, str) or not value or "\x00" in value or ":" in value:
        raise ValueError("作品索引包含无效相对路径")
    relative = PurePosixPath(value.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("作品索引相对路径越界")
    candidate = Path(os.path.abspath(base.joinpath(*relative.parts)))
    candidate.relative_to(base)
    return candidate


def _serialize_work(work: LibraryWork) -> tuple[str, str, str]:
    """根目录只存绝对位置，作品目录和媒体文件分别存相对位置。"""
    data = asdict(work)
    root = _absolute_path(work.root)
    directory = _absolute_path(work.directory)
    relative_directory = directory.relative_to(root).as_posix()
    data["root"] = str(root)
    data["directory"] = relative_directory
    data["paths_relative"] = True
    for media in data["media"]:
        media["path"] = _absolute_path(media["path"]).relative_to(directory).as_posix()
    return (
        str(root),
        relative_directory,
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
    )


def _deserialize_work(payload: str) -> LibraryWork:
    """还原界面的绝对路径实体，并兼容升级前的绝对路径缓存。"""
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise TypeError("作品索引记录必须为对象")
    relative = data.pop("paths_relative", False)
    if not isinstance(relative, bool):
        raise TypeError("作品索引路径版本无效")
    root = _absolute_path(data["root"])
    directory = (
        _relative_path(root, data["directory"])
        if relative
        else _absolute_path(data["directory"])
    )
    directory.relative_to(root)
    data["root"], data["directory"] = str(root), str(directory)
    media_values = data.get("media", [])
    if not isinstance(media_values, (list, tuple)):
        raise TypeError("作品媒体列表无效")
    media_items: list[LocalMedia] = []
    for value in media_values:
        if not isinstance(value, dict) or value.get("media_type") not in (
            "image",
            "video",
        ):
            raise ValueError("作品媒体记录无效")
        media = dict(value)
        path = (
            _relative_path(directory, media["path"])
            if relative
            else _absolute_path(media["path"])
        )
        if path == directory:
            raise ValueError("媒体路径不能指向作品目录本身")
        path.relative_to(directory)
        media["path"] = str(path)
        media_items.append(LocalMedia(**media))
    data["media"] = tuple(media_items)
    for field in ("article_blocks", "article_urls"):
        values = data.get(field, [])
        if not isinstance(values, (list, tuple)) or any(
            not isinstance(value, str) for value in values
        ):
            raise ValueError("作品文章字段无效")
        data[field] = tuple(values)
    for field in (
        "key",
        "tweet_id",
        "username",
        "display_name",
        "text",
        "url",
        "warning",
        "signature",
    ):
        if field in data and not isinstance(data[field], str):
            raise ValueError("作品文字字段无效")
    if data.get("created_at") is not None and not isinstance(data["created_at"], str):
        raise ValueError("作品日期字段无效")
    return LibraryWork(**data)


class LibraryRepository:
    """在创建它的线程中管理一个 SQLite 连接。"""

    def __init__(
        self, db_path: str | Path, personal_path: str | Path | None = None
    ) -> None:
        """打开索引；确定的损坏数据库先保留备份再重建。"""
        self.path = Path(db_path)  # 当前用户可写的索引文件位置。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None  # 仅供所属线程访问的连接。
        self.personal_error: str = ""  # 个人库不可用原因，不能伪装为没有喜欢或收藏。
        self._personal_attached: bool = False  # 是否已只读附加经过版本校验的个人库。
        with _INITIALIZATION_LOCK:
            try:
                self._open()
            except sqlite3.DatabaseError as exc:
                self.close()
                # 锁竞争、磁盘只读等错误不是损坏，不能因此移动仍在使用的数据库。
                code = _sqlite_error_code(exc)
                if code not in _CORRUPT_ERRORS:
                    raise
                backup = self.path.with_name(
                    f"{self.path.name}.corrupt-{time.time_ns()}"
                )
                logger.warning("作品索引损坏，保留备份并重建：%s", backup)
                self.path.replace(backup)
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(f"{self.path}{suffix}")
                    if sidecar.exists():
                        sidecar.replace(Path(f"{backup}{suffix}"))
                self._open()

        # 个人库不属于可重建缓存，附加和校验必须在上方损坏恢复边界之外。
        if personal_path is not None:
            self._attach_personal(Path(personal_path))

    def _attach_personal(self, path: Path) -> None:
        """只读附加独立个人数据，失败时保留主作品库及个人原库。"""
        connection = self._db()
        attached = False
        try:
            uri = path.absolute().as_uri() + "?mode=ro"
            connection.execute("ATTACH DATABASE ? AS personal", (uri,))
            attached = True
            version = int(
                connection.execute("PRAGMA personal.user_version").fetchone()[0]
            )
            if version != PERSONAL_SCHEMA_VERSION:
                raise RuntimeError("个人数据版本不受支持，请使用对应版本应用打开")
            connection.execute(
                "SELECT work_key,liked_at,collected_at,revision FROM personal.work_state LIMIT 0"
            )
            connection.execute(
                "SELECT folder_id,work_key,added_at FROM personal.folder_items LIMIT 0"
            )
            default = connection.execute(
                "SELECT is_system FROM personal.folders WHERE id=?",
                (DEFAULT_FOLDER_ID,),
            ).fetchone()
            if default is None or int(default[0]) != 1:
                raise RuntimeError("个人数据中的默认收藏夹无效")
            self._personal_attached = True
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
            self.personal_error = f"个人数据不可用，原库已保留：{exc}"
            logger.error("%s", self.personal_error)
            if attached:
                try:
                    connection.execute("DETACH DATABASE personal")
                except sqlite3.Error:
                    logger.warning("无法释放不可用的个人库附加连接，将随作业关闭")

    def _require_personal(self) -> None:
        """个人筛选必须使用有效个人库，不能返回误导性的空列表。"""
        if not self._personal_attached:
            raise RuntimeError(self.personal_error or "个人数据库尚未就绪，请稍后重试")

    def personal_states(self, keys: Iterable[str]) -> dict[str, PersonalWorkState]:
        """批量水合作品个人状态，不对每个可见行单独查询。"""
        self._require_personal()
        return read_states(self._db(), keys, "personal")

    def _open(self) -> None:
        """初始化连接、写前日志和筛选排序所需的数据结构。"""
        connection = sqlite3.connect(self.path, timeout=SQLITE_TIMEOUT, uri=True)
        self._connection = connection
        connection.row_factory = sqlite3.Row
        deadline = time.monotonic() + SQLITE_TIMEOUT
        while True:
            try:
                self._initialize(connection)
                return
            except sqlite3.OperationalError as exc:
                connection.rollback()
                code = _sqlite_error_code(exc)
                if code not in _LOCK_ERRORS or time.monotonic() >= deadline:
                    self.close()
                    raise
                # 首次建库也可能与另一个进程竞争 WAL 模式，有限等待后再重新检查版本。
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _initialize(self, connection: sqlite3.Connection) -> None:
        """只在首次建库或版本升级时持有写锁，普通查询连接不反复执行 DDL。"""
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            connection.close()
            self._connection = None
            raise RuntimeError("作品索引由更新版本创建，请使用新版应用打开")
        mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).casefold()
        if mode != "wal":
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        if version == SCHEMA_VERSION:
            return
        connection.execute("BEGIN IMMEDIATE")
        # 等待跨进程建库写锁后再次确认，避免重复迁移刚刚完成的数据库。
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            connection.rollback()
            self.close()
            raise RuntimeError("作品索引由更新版本创建，请使用新版应用打开")
        if version == SCHEMA_VERSION:
            connection.commit()
            return
        connection.execute("""
            CREATE TABLE IF NOT EXISTS library_works (
                key TEXT PRIMARY KEY,
                root TEXT NOT NULL,
                directory TEXT NOT NULL,
                author TEXT NOT NULL,
                published_date TEXT,
                sort_at REAL NOT NULL,
                first_seen REAL NOT NULL,
                search_text TEXT NOT NULL,
                has_image INTEGER NOT NULL,
                has_video INTEGER NOT NULL,
                has_article INTEGER NOT NULL,
                is_text INTEGER NOT NULL,
                signature TEXT NOT NULL,
                payload TEXT NOT NULL
            )
        """)
        for name, columns in (
            ("sort", "sort_at DESC, key"),
            ("author", "author, sort_at DESC"),
            ("root", "root"),
            ("date", "published_date"),
        ):
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS library_{name} ON library_works({columns})"
            )
        self._migrate_paths(connection)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.commit()

    def _migrate_paths(self, connection: sqlite3.Connection) -> None:
        """把旧索引转成相对路径，保留稳定标识、首次导入时间及所有原件。"""
        cursor = connection.execute("SELECT key, payload FROM library_works")
        converted = 0
        while rows := cursor.fetchmany(80):
            for row in rows:
                try:
                    root, directory, payload = _serialize_work(
                        _deserialize_work(row["payload"])
                    )
                    connection.execute(
                        "UPDATE library_works SET root=?, directory=?, payload=? WHERE key=?",
                        (root, directory, payload, row["key"]),
                    )
                    converted += 1
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    AttributeError,
                    RecursionError,
                ):
                    connection.execute(
                        "DELETE FROM library_works WHERE key=?", (row["key"],)
                    )
                    logger.warning("升级时移除一条无效作品缓存，可重新扫描恢复")
        if converted:
            logger.info("作品索引相对路径存储升级完成：%d 条", converted)

    def _db(self) -> sqlite3.Connection:
        """取得尚未关闭的同线程连接。"""
        if self._connection is None:
            raise RuntimeError("作品索引已经关闭")
        return self._connection

    def upsert(self, work: LibraryWork) -> None:
        """新增或更新作品；保留首次导入时间作为未知发布日期的稳定回退。"""
        connection = self._db()
        previous = connection.execute(
            "SELECT first_seen, signature FROM library_works WHERE key=?", (work.key,)
        ).fetchone()
        if (
            previous is not None
            and previous["signature"] == work.signature
            and work.signature
        ):
            return
        first_seen = previous["first_seen"] if previous is not None else time.time()
        published_date = work.created_at[:10] if work.created_at else None
        sort_at = first_seen
        if work.created_at:
            try:
                timestamp = datetime.fromisoformat(work.created_at)
                # 旧下载器写出的无时区时间保留其数值，并按 UTC 作确定性排序。
                sort_at = timestamp.replace(
                    tzinfo=timestamp.tzinfo or timezone.utc
                ).timestamp()
            except (ValueError, OverflowError, OSError):
                published_date = None
        search_text = "\n".join(
            (
                work.username,
                work.display_name,
                work.text,
                *work.article_blocks,
                work.tweet_id,
            )
        ).casefold()
        kinds = {media.media_type for media in work.media}
        root, directory, payload = _serialize_work(work)
        values = (
            work.key,
            root,
            directory,
            work.username,
            published_date,
            sort_at,
            first_seen,
            search_text,
            int("image" in kinds),
            int("video" in kinds),
            int(bool(work.article_blocks)),
            int(not work.media),
            work.signature,
            payload,
        )
        with connection:
            connection.execute(
                """
                INSERT INTO library_works VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    root=excluded.root, directory=excluded.directory, author=excluded.author,
                    published_date=excluded.published_date, sort_at=excluded.sort_at,
                    search_text=excluded.search_text, has_image=excluded.has_image,
                    has_video=excluded.has_video, has_article=excluded.has_article,
                    is_text=excluded.is_text, signature=excluded.signature, payload=excluded.payload
            """,
                values,
            )

    def _where(self, query: LibraryQuery | None) -> tuple[str, list[Any]]:
        """构造绑定参数的筛选条件，搜索字符串按普通子串而非 SQL 通配符处理。"""
        query = query or LibraryQuery()
        conditions: list[str] = []
        parameters: list[Any] = []
        if query.scope not in ("all", "liked", "bookmarked"):
            raise ValueError("作品列表范围无效")
        if query.folder_id and query.scope != "bookmarked":
            raise ValueError("只能在收藏列表内选择收藏夹")
        if query.scope != "all":
            self._require_personal()
            if query.scope == "liked":
                conditions.append(
                    "EXISTS (SELECT 1 FROM personal.work_state AS ps "
                    "WHERE ps.work_key=library_works.key AND ps.liked_at IS NOT NULL)"
                )
            else:
                membership = "fi.work_key=library_works.key"
                if query.folder_id:
                    folder = (
                        self._db()
                        .execute(
                            "SELECT 1 FROM personal.folders WHERE id=?",
                            (query.folder_id,),
                        )
                        .fetchone()
                    )
                    if folder is None:
                        raise ValueError("收藏夹已不存在，请刷新后重新选择")
                    membership += " AND fi.folder_id=?"
                    parameters.append(query.folder_id)
                conditions.append(
                    f"EXISTS (SELECT 1 FROM personal.folder_items AS fi WHERE {membership})"
                )
        if query.search.strip():
            value = (
                query.search.strip()
                .casefold()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            conditions.append("search_text LIKE ? ESCAPE '\\'")
            parameters.append(f"%{value}%")
        if query.author:
            conditions.append("author = ?")
            parameters.append(query.author)
        field = {
            "image": "has_image",
            "video": "has_video",
            "article": "has_article",
            "text": "is_text",
        }.get(query.kind)
        if field:
            conditions.append(f"{field} = 1")
        for value, operator in ((query.since, ">="), (query.until, "<=")):
            if value:
                try:
                    normalized = datetime.fromisoformat(value).date().isoformat()
                except ValueError:
                    continue
                conditions.append(f"published_date {operator} ?")
                parameters.append(normalized)
        return (" WHERE " + " AND ".join(conditions) if conditions else ""), parameters

    def _order(self, query: LibraryQuery | None) -> tuple[str, list[Any]]:
        """构造固定排序表达式，收藏夹范围只绑定稳定标识而不拼接用户输入。"""
        query = query or LibraryQuery()
        if query.sort == "recent" and query.scope == "liked":
            return (
                (
                    "(SELECT liked_at FROM personal.work_state AS ps "
                    "WHERE ps.work_key=library_works.key) DESC, key ASC"
                ),
                [],
            )
        if query.sort == "recent" and query.scope == "bookmarked":
            if query.folder_id:
                return (
                    (
                        "(SELECT added_at FROM personal.folder_items AS fi "
                        "WHERE fi.work_key=library_works.key AND fi.folder_id=?) DESC, key ASC"
                    ),
                    [query.folder_id],
                )
            return (
                (
                    "(SELECT collected_at FROM personal.work_state AS ps "
                    "WHERE ps.work_key=library_works.key) DESC, key ASC"
                ),
                [],
            )
        direction = "ASC" if query.sort == "oldest" else "DESC"
        return f"sort_at {direction}, key ASC", []

    def query(
        self, query: LibraryQuery | None = None, offset: int = 0, limit: int = 80
    ) -> list[LibraryWork]:
        """分页读取作品实体，单条损坏的缓存不会阻止其他作品显示。"""
        where, parameters = self._where(query)
        order, order_parameters = self._order(query)
        rows = (
            self._db()
            .execute(
                f"SELECT key, payload FROM library_works{where} ORDER BY {order} LIMIT ? OFFSET ?",
                [
                    *parameters,
                    *order_parameters,
                    max(1, min(int(limit), 10000)),
                    max(0, int(offset)),
                ],
            )
            .fetchall()
        )
        works: list[LibraryWork] = []
        damaged_keys: list[str] = []
        for row in rows:
            try:
                works.append(_deserialize_work(row["payload"]))
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                logger.warning("跳过一条损坏的作品索引记录，可通过重新扫描恢复")
                damaged_keys.append(row["key"])
        if damaged_keys:
            # 清掉可重建的坏缓存，避免总数和分页偏移因不可显示记录而永远无法抵达末页。
            with self._db():
                self._db().executemany(
                    "DELETE FROM library_works WHERE key=?",
                    ((key,) for key in damaged_keys),
                )
        return works

    def count(self, query: LibraryQuery | None = None) -> int:
        """返回当前筛选条件下的作品总数。"""
        where, parameters = self._where(query)
        return int(
            self._db()
            .execute(f"SELECT COUNT(*) FROM library_works{where}", parameters)
            .fetchone()[0]
        )

    def authors(self) -> list[tuple[str, int]]:
        """按账号返回作品数，未知账号保留为空字符串。"""
        rows = (
            self._db()
            .execute(
                "SELECT author, COUNT(*) FROM library_works GROUP BY author ORDER BY author COLLATE NOCASE"
            )
            .fetchall()
        )
        return [(row[0], int(row[1])) for row in rows]

    def prune(
        self, root: str, seen_keys: set[str], *, started_at: float | None = None
    ) -> int:
        """完整扫描成功后删除已不存在的缓存记录，绝不操作媒体原件。"""
        connection = self._db()
        with connection:
            connection.execute(
                "CREATE TEMP TABLE IF NOT EXISTS scan_seen (key TEXT PRIMARY KEY)"
            )
            connection.execute("DELETE FROM scan_seen")
            connection.executemany(
                "INSERT INTO scan_seen VALUES (?)", ((key,) for key in seen_keys)
            )
            # 扫描期间新下载的作品可能已被另一连接加入，不能因遍历快照中没有它就清除。
            cutoff = started_at if started_at is not None else time.time()
            cursor = connection.execute(
                "DELETE FROM library_works WHERE root=? AND first_seen <= ? AND key NOT IN (SELECT key FROM scan_seen)",
                (root, cutoff),
            )
        return cursor.rowcount

    def close(self) -> None:
        """关闭本线程连接，释放文件锁和临时表。"""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

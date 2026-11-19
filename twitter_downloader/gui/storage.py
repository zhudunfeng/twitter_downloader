"""解析便携式数据目录，并在启动前安全导入旧版用户数据。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DATA_DIR_ENV = (
    "TWITTER_DOWNLOADER_DATA_DIR"  # 显式覆盖数据目录的环境变量，优先于引导文件。
)
BACKUP_TIMEOUT_SECONDS = 10.0  # 每个数据库快照等待写锁或复制完成的最长秒数。
INITIALIZED_FILE = (
    ".storage-initialized.json"  # 已初始化标记，防止清理配置后重复导入旧数据。
)
_DATA_FILES = (
    "config.json",
    "personal.sqlite3",
    "library.sqlite3",
)  # 可识别已有软件数据集的文件，不能被自动合并或覆盖。
_LOG_FILES = (
    "desktop.log",
    "desktop.log.1",
    "desktop.log.2",
)  # 可一并保留的旧版轮转日志。


class StorageError(RuntimeError):
    """报告可由用户更换路径或修复文件解决的存储配置错误。"""


def resolve_software_directory() -> Path:
    """定位软件本身而不是终端工作目录，冻结版本使用可执行文件所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


SOFTWARE_DIR = resolve_software_directory()  # EXE 所在目录，源码运行时为项目根目录。
STORAGE_CONFIG_PATH = (
    SOFTWARE_DIR / "storage.json"
)  # 重启前后都可定位的数据目录引导文件。
DEFAULT_DATA_DIR = SOFTWARE_DIR / "data"  # 默认把全部应用持久数据集中到软件目录内。
DEFAULT_OUTPUT_DIR = (
    SOFTWARE_DIR / "downloads"
)  # GUI 新下载默认目录；CLI 的相对目录约定不变。
LEGACY_APP_DATA_DIR = (
    Path(os.environ.get("APPDATA") or Path.home() / ".config") / "TwitterDownloader"
).resolve()  # 仅用于首次导入的旧版数据位置，不再作为默认写入位置。


def _resolve_path(value: str | Path, label: str) -> Path:
    """校验路径输入，并把相对路径固定解释为软件目录下的路径。"""
    raw = str(value).strip()
    if not raw or any(ord(character) < 32 for character in raw) or "://" in raw:
        raise StorageError(f"{label}必须是非空的本地目录路径")
    try:
        path = Path(raw).expanduser()
        if path.drive and not path.is_absolute():
            raise StorageError(f"{label}不能使用缺少根目录的盘符相对路径")
        return (path if path.is_absolute() else SOFTWARE_DIR / path).resolve()
    except (OSError, ValueError) as exc:
        raise StorageError(f"{label}无效：{exc}") from exc


def _read_bootstrap() -> dict[str, Any]:
    """严格读取引导文件，损坏时明确报错而不是偷偷切换数据集。"""
    try:
        data = json.loads(STORAGE_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise StorageError(
            f"无法读取数据目录配置 {STORAGE_CONFIG_PATH}：{exc}"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("data_dir"), str):
        raise StorageError(
            f"数据目录配置必须包含字符串 data_dir：{STORAGE_CONFIG_PATH}"
        )
    if type(data.get("version", 1)) is not int or data.get("version", 1) != 1:
        raise StorageError(f"不支持此版本的数据目录配置：{STORAGE_CONFIG_PATH}")
    if "migrate_from" in data and not isinstance(data["migrate_from"], str):
        raise StorageError("数据目录配置中的 migrate_from 必须是路径字符串")
    return data


def _has_dataset(path: Path) -> bool:
    """识别已有应用数据，避免覆盖用户选择的另一个现存数据集。"""
    for name in (INITIALIZED_FILE, *_DATA_FILES):
        try:
            if stat.S_ISREG((path / name).stat().st_mode):
                return True
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            raise StorageError(
                f"无法检查软件数据，未切换到空数据集：{path}：{exc}"
            ) from exc
    return False


def _fallback_source() -> tuple[Path, bool]:
    """先继承已启用的便携默认数据，只有没有便携数据时才探测旧用户目录。"""
    if _has_dataset(DEFAULT_DATA_DIR):
        return DEFAULT_DATA_DIR, True
    return LEGACY_APP_DATA_DIR, False


def _selection() -> tuple[Path, Path | None, bool]:
    """依优先级选择数据目录、导入源以及是否必须保留显式迁移来源。"""
    if DATA_DIR_ENV in os.environ:
        target = _resolve_path(os.environ[DATA_DIR_ENV], DATA_DIR_ENV)
        if _has_dataset(target):
            return target, None, False
        bootstrap = _read_bootstrap()
        if bootstrap:
            previous = _resolve_path(bootstrap["data_dir"], "上次配置的数据目录")
            if _has_dataset(previous):
                return target, previous, True
            if "migrate_from" in bootstrap:
                return (
                    target,
                    _resolve_path(bootstrap["migrate_from"], "迁移源目录"),
                    True,
                )
            if previous == target:
                source, required = _fallback_source()
                return target, source, required
            return target, previous, True
        source, required = _fallback_source()
        return target, source, required
    bootstrap = _read_bootstrap()
    target = _resolve_path(bootstrap.get("data_dir", DEFAULT_DATA_DIR), "数据目录")
    source = bootstrap.get("migrate_from")
    if source is not None:
        return target, _resolve_path(source, "迁移源目录"), True
    source, required = _fallback_source()
    return target, source, required


def _initial_directory() -> tuple[Path, StorageError | None]:
    """延迟到应用启动时报告配置错误，模块导入阶段不创建任何文件。"""
    try:
        return _selection()[0], None
    except StorageError as exc:
        # 此路径仅保证导入可完成；prepare_storage 会在任何持久写入前拒绝启动。
        return DEFAULT_DATA_DIR, exc


APP_DATA_DIR, _STARTUP_ERROR = (
    _initial_directory()
)  # 本次进程固定路径，设置修改后重启才生效。
CACHE_DIR = APP_DATA_DIR / "previews"  # 可重新生成的媒体预览也跟随数据目录。


def configured_data_directory() -> Path:
    """读取下次启动将使用的目录，不改变本次进程的活动路径。"""
    return _selection()[0]


def resolve_output_directory(value: str | Path) -> Path:
    """将 GUI 下载目录相对软件位置解析，空值使用便携式默认下载目录。"""
    return _resolve_path(
        value if str(value).strip() else DEFAULT_OUTPUT_DIR, "下载目录"
    )


def _probe_writable(path: Path) -> None:
    """用同目录短暂探针检查权限，不把失败目录回退到用户 C 盘。"""
    probe = ""
    try:
        with tempfile.NamedTemporaryFile(
            dir=path, prefix=".twitter-write-", delete=False
        ) as handle:
            probe = handle.name
    finally:
        if probe:
            Path(probe).unlink(missing_ok=True)


def _validate_target(path: Path) -> None:
    """允许空目录或已有数据集，拒绝普通文件及无关的非空目录。"""
    try:
        if path.exists():
            if not path.is_dir():
                raise StorageError(f"数据路径不是目录：{path}")
            if any(path.iterdir()) and not _has_dataset(path):
                raise StorageError("请选择空目录或已有软件数据目录，不能与其他文件混合")
            _probe_writable(path)
            return
        parent = path.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if not parent.is_dir():
            raise StorageError(f"数据目录的上级路径不是目录：{parent}")
        _probe_writable(parent)
    except OSError as exc:
        raise StorageError(
            f"数据目录不可写：{path}。请更换有写入权限的目录：{exc}"
        ) from exc


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """在目标同目录原子发布 JSON，失败时保留之前的有效文件。"""
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def configure_data_directory(path: str | Path) -> Path:
    """校验并原子保存下次启动的数据目录，活动数据库连接保持不变。"""
    if DATA_DIR_ENV in os.environ:
        raise StorageError(
            f"当前由环境变量 {DATA_DIR_ENV} 指定数据目录，请修改该变量后重启"
        )
    target = _resolve_path(path, "数据目录")
    _validate_target(target)
    try:
        relative = target.relative_to(SOFTWARE_DIR)
        stored_path = relative.as_posix()
    except ValueError:
        stored_path = str(target)
    data: dict[str, Any] = {
        "version": 1,  # 引导文件格式版本。
        "data_dir": stored_path,  # 相对软件目录或绝对路径，重启后使用。
    }
    if target != APP_DATA_DIR:
        data["migrate_from"] = str(
            APP_DATA_DIR
        )  # 空目标目录首次启动时复制的原数据根目录。
    try:
        _write_json(STORAGE_CONFIG_PATH, data)
    except OSError as exc:
        logger.error("保存数据目录配置失败：%s", exc)
        raise StorageError(
            f"无法保存 {STORAGE_CONFIG_PATH}。软件目录可能只读，可设置 {DATA_DIR_ENV} 后重启：{exc}"
        ) from exc
    logger.info(
        "数据目录设置已保存，重启后生效：%s；本次仍使用：%s", target, APP_DATA_DIR
    )
    return target


def _backup_database(source: Path, destination: Path) -> None:
    """通过 SQLite 快照复制已提交内容，包含尚未检查点的 WAL 记录。"""
    started = time.monotonic()
    timeout = min(2.0, BACKUP_TIMEOUT_SECONDS)  # 单次锁等待也必须服从整体复制时限。

    def progress(status: int, remaining: int, total: int) -> None:
        """限制快照等待，避免源数据库被其他进程锁定时无限阻塞启动。"""
        if time.monotonic() - started > BACKUP_TIMEOUT_SECONDS:
            raise StorageError(f"数据库导入超时，请关闭其他软件实例后重试：{source}")

    with (
        closing(
            sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=timeout)
        ) as original,
        closing(sqlite3.connect(destination, timeout=timeout)) as copied,
    ):
        original.backup(copied, pages=128, progress=progress, sleep=0.05)
        result = copied.execute("PRAGMA quick_check").fetchall()
        if result != [("ok",)]:
            raise StorageError(f"数据库校验失败，未发布迁移结果：{source}")


def _copy_dataset(source: Path | None, stage: Path, required: bool) -> bool:
    """仅复制应用拥有的配置和快照，不移动下载原件或重建型预览缓存。"""
    if source is None:
        return False
    try:
        try:
            source_stat = source.stat()
        except FileNotFoundError:
            if required:
                raise StorageError(f"迁移源目录不可访问，原数据未删除：{source}")
            return False
        if not stat.S_ISDIR(source_stat.st_mode):
            raise StorageError(f"迁移源路径不是目录，原数据未删除：{source}")
        if required and not _has_dataset(source):
            raise StorageError(
                f"迁移源缺少软件数据，已停止切换以防创建空个人库：{source}"
            )
        copied = False
        for name in (*_DATA_FILES, *_LOG_FILES):
            original = source / name
            try:
                original_stat = original.stat()
            except FileNotFoundError:
                if original.is_symlink():
                    raise StorageError(f"旧数据链接的原文件不可访问：{original}")
                continue
            # Python 3.14 的 exists/is_file 会隐藏访问错误；只有确实不存在才可跳过。
            if not stat.S_ISREG(original_stat.st_mode):
                raise StorageError(f"旧数据中应为文件的路径无效：{original}")
            if original.suffix == ".sqlite3":
                _backup_database(original, stage / name)
            else:
                shutil.copy2(original, stage / name)
            copied = True
        return copied
    except (OSError, sqlite3.Error) as exc:
        raise StorageError(f"导入旧数据失败，原文件保持不变：{source}：{exc}") from exc


def prepare_storage() -> None:
    """在日志和数据库打开之前准备完整数据集，失败时绝不创建替代个人库。"""
    if _STARTUP_ERROR is not None:
        raise StorageError(str(_STARTUP_ERROR))
    target, source, required = _selection()
    if target != APP_DATA_DIR:
        raise StorageError("数据目录配置已在启动期间改变，请重新启动程序")
    _validate_target(target)
    if target.is_dir() and _has_dataset(target):
        logger.info("使用已有软件数据目录，不覆盖或合并：%s", target)
        return
    if source is not None and source.resolve() == target:
        if required:
            raise StorageError("迁移源与空目标相同，请修正迁移源目录，原文件保持不变")
        source, required = None, False
    stage: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(
            tempfile.mkdtemp(prefix=f".{target.name}-import-", dir=target.parent)
        )
        imported = _copy_dataset(source, stage, required)
        _write_json(
            stage / INITIALIZED_FILE,
            {
                "version": 1,  # 数据目录初始化标记的格式版本。
                "migrated_from": str(source)
                if imported
                else "",  # 实际导入来源，原件永久保留。
            },
        )
        # 只有全部快照成功后才发布目录；无关文件或另一个实例抢先出现时拒绝覆盖。
        if target.exists():
            target.rmdir()
        stage.rename(target)
        stage = None
        if imported:
            logger.info(
                "旧数据已完整导入：%s -> %s；原件保留，预览缓存按需重建", source, target
            )
        else:
            logger.info("已在软件数据目录完成初始化：%s", target)
    except (OSError, sqlite3.Error) as exc:
        logger.error("数据目录初始化失败，未改用其他目录：%s", exc)
        raise StorageError(f"无法初始化数据目录 {target}：{exc}") from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)

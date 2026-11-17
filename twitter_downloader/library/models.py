"""作品库与界面之间传递的不可变实体。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocalMedia:
    """描述一个经过本地路径校验的媒体文件。"""

    path: str  # 媒体的绝对本地路径，不允许远程 URL。
    media_type: str  # 媒体类别，取 image 或 video。
    width: int | None = None  # 保存的原始像素宽度。
    height: int | None = None  # 保存的原始像素高度。
    exists: bool = True  # 扫描时文件是否存在且可访问。
    size_bytes: int | None = None  # 实际文件字节数，未知时为空。
    signature: str = ""  # 路径、修改时间与大小组成的缓存失效标记。


@dataclass(frozen=True, slots=True)
class LibraryWork:
    """描述一条可脱离网络阅读的本地推文作品。"""

    key: str  # 下载根目录与相对目录共同确定的稳定标识。
    root: str  # 此作品所属的规范化下载根目录。
    directory: str  # 原始作品文件所在的绝对目录。
    tweet_id: str  # 推文标识，元数据缺失时使用目录名。
    username: str  # 已保存的账号名，未知时为空。
    display_name: str  # 已保存的显示名称，未知时为空。
    created_at: str | None = None  # 规范化的发布时间，未知时为空。
    text: str = ""  # 推文正文，保留原始段落和换行。
    article_blocks: tuple[str, ...] = ()  # 按原顺序保存的文章段落。
    article_urls: tuple[str, ...] = ()  # 已保存的外部文章来源链接。
    media: tuple[LocalMedia, ...] = ()  # 按原始顺序排列的本地媒体。
    url: str = ""  # 原始推文地址。
    like_count: int | None = None  # 下载时保存的点赞数快照。
    retweet_count: int | None = None  # 下载时保存的转发数快照。
    reply_count: int | None = None  # 下载时保存的回复数快照。
    warning: str = ""  # 不完整元数据或媒体的可读提示。
    signature: str = ""  # 整条作品内容及文件的版本标记。


@dataclass(frozen=True, slots=True)
class LibraryQuery:
    """封装作品列表的筛选与排序参数。"""

    search: str = ""  # 中文或英文子串搜索，空字符串表示不限。
    author: str = ""  # 精确账号筛选，空字符串表示全部账号。
    kind: str = ""  # image、video、text、article；空字符串表示全部。
    since: str = ""  # 含起始日的 ISO 日期，空字符串表示不限。
    until: str = ""  # 含结束日的 ISO 日期，空字符串表示不限。
    sort: str = "newest"  # newest/oldest 按发表时间，recent 按个人操作时间。
    scope: str = "all"  # all 全部作品、liked 喜欢、bookmarked 收藏。
    folder_id: str = ""  # 收藏夹稳定标识；空字符串表示全部收藏。


@dataclass(frozen=True, slots=True)
class PersonalWorkState:
    """描述独立于下载原件与可重建索引的个人标记。"""

    work_key: str  # 现有根目录限定的作品稳定标识。
    liked_at: int | None = None  # 当前喜欢的 UTC 毫秒时间，未喜欢时为空。
    collected_at: int | None = None  # 当前连续收藏周期的首次 UTC 毫秒时间。
    folder_ids: tuple[str, ...] = ()  # 作品所属的全部收藏夹稳定标识。
    revision: int = 0  # 该作品个人状态实际变更后的递增版本。

    @property
    def liked(self) -> bool:
        """根据喜欢时间判断本地喜欢状态。"""
        return self.liked_at is not None

    @property
    def bookmarked(self) -> bool:
        """仅根据归属关系判断收藏，时间字段不作为收藏开关。"""
        return bool(self.folder_ids)


@dataclass(frozen=True, slots=True)
class BookmarkFolder:
    """描述不移动原始下载文件的逻辑收藏夹。"""

    id: str  # 重命名后保持不变的稳定标识。
    name: str  # 经过统一校验的收藏夹展示名称。
    is_system: bool  # 是否为禁止重命名和删除的系统默认夹。
    created_at: int  # 创建时的 UTC 毫秒时间。
    updated_at: int  # 最近重命名时的 UTC 毫秒时间。

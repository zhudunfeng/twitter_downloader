# PRD: GUI 改造 — Windows 本地安装 UI（保留 CLI）

## 1. 目标与约束

### 1.1 目标
- 提供 Windows 本地可安装的 GUI 入口，用于输入 Twitter/X 链接并完成下载
- 保留现有 CLI 行为：`python -m twitter_downloader <URL>` 和 `single/batch` 命令不变
- 最终可交付 `.exe` 安装包（Inno Setup）

### 1.2 核心约束
- `twitter_downloader/` 作为单一能力层，CLI 和 GUI 共享同一套下载能力
- 不修改 CLI 使用者依赖的行为：`cli.py`、`__main__.py`、`downloader.py` 的公共方法签名不破坏
- 新增 `gui/` 目录，因此引入 PySide6 仅影响新目录

---

## 2. 需求清单

### 2.1 功能性需求
1. URL 输入与校验：支持 `x.com` / `twitter.com` 推文链接
2. 输出目录选择：默认 `downloads`，支持修改；支持 cookies 文件配置
3. 下载任务执行：调用现有下载链路，支持视频（转 MP4）和图片（转 JPEG）
4. 日志输出：显示 yt-dlp/下载进度信息
5. 历史任务列表：本次迭代至少保留当前会话内已完成/失败记录
6. 打开目录：点击任务可打开输出目录

### 2.2 非功能性需求
- 支持高分屏（PySide6 原生支持）
- 单窗口界面，避免多窗口管理
- 长任务不阻塞 UI 主线程（QThread 后台执行）

---

## 3. 架构设计

### 3.1 目录变更

```
twitter_downloader/
├── __init__.py
├── __main__.py               # 保持
├── cli.py                    # 保持
├── downloader.py             # 保持
├── extractor.py              # 保持
├── models.py                 # 保持
├── output.py                 # 保持
├── utils.py                  # 保持
└── core.py                   # 新增：Downloader 使用方统一入口（CLI/GUI 均从此调用）

gui/
├── main.py                   # 新增：PySide6 主窗口
├── config.py                 # 新增：配置持久化
└── resources.py              # 新增：图标/样式
```

### 3.2 交互契约
- `core.py` 暴露：
  - `ProgressHandler` Protocol：包含 `on_start`、`on_progress`、`on_done`、`on_error`
  - `download_one(url, output_dir, cookies, progress)` -> `str`
- `downloader.py` 新增可选的 `progress` 参数，并在 `progress_hooks` 中抛出现有进度事件
- CLI 调用时传 `progress=None`，行为不变
- GUI 调用时传 Qt Signal 实现，更新进度条

---

## 4. 实现要点

### 4.1 core.py
```python
from __future__ import annotations
from typing import Iterable, Protocol

from .downloader import Downloader
from .extractor import YtDlpTweetExtractor
from .models import TweetInfo
from .output import OutputPathBuilder


class ProgressHandler(Protocol):
    def on_start(self, url: str) -> None: ...
    def on_progress(self, percent: float, status: str) -> None: ...
    def on_done(self, path: str) -> None: ...
    def on_error(self, url: str, error: str) -> None: ...


def download_one(
    url: str,
    output_dir: str = "downloads",
    cookies: str | None = None,
    progress: ProgressHandler | None = None,
) -> str:
    tweet = YtDlpTweetExtractor(cookies=cookies).extract(url)
    return Downloader(
        output_path_builder=OutputPathBuilder(base_dir=output_dir),
        cookies=cookies,
        progress=progress,
    ).download(tweet)
```

### 4.2 downloader.py 改动
- 构造器增加 `progress: ProgressHandler | None = None`
- 构造 `yt_dlp.YoutubeDL(options)` 前注入 `progress_hooks`
- hook 中根据 yt-dlp 提供的 `downloaded_bytes` / `total_bytes` 计算进度百分比并回调

### 4.3 持久化配置
- 存放路径：`%APPDATA%\TwitterDownloader\config.json`
- 字段：`output_dir`、`cookies_path`、`window_geometry`
- GUI 启动时读写，CLI 不读取

---

## 5. GUI 相对原型

### 5.1 窗口布局（建议）
- 顶部：一行 URL 输入框 + Cookies 选择按钮 + 输出目录选择 + Download 按钮
- 中部：只读文本日志（可折叠）
- 下部：进度条 + 状态标签
- 侧边栏：当前会话任务列表（成功高亮、失败标红）
- 底部状态栏：就绪 / 下载中 / 错误

### 5.2 线程模型
- `Worker(QThread)`：一个 Worker 对应一个任务
- 主窗口维护 FIFO 队列，顺序执行任务
- 线程信号：
  - `started(url)`
  - `progress(percent, status)`
  - `finished(path)`
  - `error(message)`

---

## 6. 验收标准（DoD）

1. CLI 行为不变：
   - `python -m twitter_downloader single <URL>` 能正常下载
   - `python -m twitter_downloader --help` 输出正常
2. GUI 可启动：
   - `python -m twitter_downloader.gui` 显示主窗口
3. 单任务下载闭环：
   - 输入合法 URL，点击 Download，出现输出目录，日志正常输出
4. 失败可感知：
   - 非法 URL 给出错误提示
   - 网络异常错误信息显示在日志区

---

## 7. 依赖扩展

在 `requirements.txt` 追加：
```
PySide6
```

不删除现有依赖。

---

## 8. 任务拆分

| # | 子任务 | 优先级 | 备注 |
|---|--------|--------|------|
| 1 | 新增 `twitter_downloader/core.py` | P0 |增加 ProgressHandler 协议 + download_one |
| 2 | 修改 `twitter_downloader/downloader.py` 支持 progress hook | P0 |保持 CLI 无副作用的默认行为 |
| 3 | 新增 `gui/config.py` | P1 |配置读写与默认值处理 |
| 4 | 新增 `gui/resources.py` | P1 |图标、样式主题 |
| 5 | 新增 `gui/main.py` | P0 |主窗口、主线程队列、Worker、任务列表 |
| 6 | 新增 `gui/__main__.py` | P1 |支持 `python -m twitter_downloader.gui` |
| 7 | 更新 `requirements.txt` | P1 |追加 `PySide6` |
| 8 | 补充 README / 打包说明 | P2 |PyInstaller + Inno Setup 命令示例 |

---

## 9. 排除项（本次不做）
- 不支持多账号同时登录/切换
- 不做浏览器内嵌
- 不做 Electron/Tauri
- 不做全局热键/托盘常驻
- 不改变 `downloader.py` 现有类名与公共方法签名

---

## 10. 风险与回滚
- 最大风险在于 `downloader.py` 改动导致既有 CLI 下载失败
  - 缓解：默认 `progress=None` 保留原路径；使用 yt-dlp progress_hooks 为纯附加行为
-  PySide6 安装包体积增大
  - 影响：仅 GUI 开发者可见，最终用户以安装包形式分发

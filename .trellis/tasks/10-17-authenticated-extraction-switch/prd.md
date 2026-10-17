# PRD: 认证/匿名解析接口开关与自动 Cookie 加载

## 1. 背景

`https://x.com/laosj666/status/1819930329953775785/video/1` 属于年龄限制（成人）内容。匿名访问时 X 返回
`TweetTombstone`，但程序仍然输出 `Saved to:` 并只写入空元数据，用户无法察觉失败。

根因有两处：

1. `YtDlpTweetExtractor._extract_status()` 直接调用 yt-dlp 的 `_call_syndication_api`，绕过了 yt-dlp
   自带的认证分支（`is_logged_in` → GraphQL）。
2. 空状态被当作成功的 `TweetInfo`，且没有任何 cookie 自动加载机制。

## 2. 目标

- 默认走**认证接口**（GraphQL + cookie）解析元数据，可下载受限内容。
- 由用户在 GUI 中通过开关控制使用「认证接口」还是「匿名接口」，默认认证。
- 不传 cookie 时自动探测可用登录态。
- 受限内容失败时必须给出明确错误，而不是静默产出空结果。
- 不破坏既有 CLI 行为与公共 API。

## 3. 需求清单

### 3.1 功能性需求

1. `YtDlpTweetExtractor` 新增 `use_authenticated: bool = True`：
   - `True`：调用 yt-dlp `_extract_status`（认证/GraphQL 路径）。
   - `False`：通过 `extractor_args={"twitter": {"api": ["syndication"]}}` 强制匿名 syndication 接口。
2. GUI 在 Cookies 下方新增复选框 `Use authenticated API (GraphQL + cookies)`，默认勾选。
3. GUI 开关持久化到 `config.json` 的 `use_authenticated`，重启保持。
4. CLI 新增 `--anonymous` 标志（`single` / `batch`），默认不启用即认证路径。
5. 自动 cookie 加载顺序：
   1. 显式 `--cookies` / GUI cookie 路径
   2. `%APPDATA%\TwitterDownloader\cookies.txt`
   3. 当前工作目录 `cookies.txt`
   4. 已登录 X 的浏览器（chrome / edge / firefox / …）
6. 匿名模式下不尝试 cookie 重试，错误信息提示启用认证接口。
7. 日志同时输出到控制台与日志文件：
   - 文件始终记录 DEBUG 全量日志，按 2 MB 轮转保留 5 份。
   - 默认路径 `%APPDATA%\TwitterDownloader\logs\twitter_downloader.log`，可用 `--log-file` 覆盖。
   - GUI 日志区内容同步写入同一份文件，并在启动时显示日志路径。

### 3.2 非功能性需求

- 浏览器 cookie 探测必须静默，不污染输出。
- 媒体下载使用 `video_info` 中码率最高的 `video/mp4` 直链。
- 保持 `downloader.py` 公共签名不变。

## 4. 架构变更

```
twitter_downloader/
  extractor.py   # use_authenticated 开关、restriction 检测、最佳 mp4 变体
  utils.py       # cookie_sources() 惰性候选源、_SilentLogger
  core.py        # download_one/download_batch 透传 use_authenticated
  cli.py         # --anonymous 标志、--log-file
  logging_setup.py  # 控制台 + 轮转文件双路日志
  gui/main.py    # 复选框、Worker 透传、配置持久化
  gui/config.py  # 默认值 use_authenticated: True
tests/
  test_download_flow.py  # 解析、CLI 与日志用例
  test_gui.py            # 3 个新增用例
```

## 5. 验收标准（DoD）

1. `python -m twitter_downloader single <受限推文>` 无需额外参数即可下载（存在已保存 cookie 时）。
2. `--anonymous` 对受限推文返回明确错误，提示启用认证接口。
3. GUI 复选框默认勾选，重启后保持上次选择。
4. 受限内容不再产生空目录 + 空 JSON 的“假成功”。
5. `python -m unittest discover -s tests` 全部通过。
6. CLI 与 GUI 的日志同时出现在控制台和日志文件中，文件保留 DEBUG 级细节。

## 6. 排除项

- 不实现 GUI 内的 cookie 导入/导出向导。
- 不自动解密 Chrome 应用绑定加密的 cookie 数据库。
- 不改变图片下载与输出目录结构。

## 7. 风险

- Chrome 运行中时 cookie 数据库被独占锁定，浏览器路径不可用 → 由文件回退与错误提示覆盖。
- yt-dlp 的 `_extract_status` 属内部 API，升级后可能变动 → 运行时校验可调用性并给出明确错误。

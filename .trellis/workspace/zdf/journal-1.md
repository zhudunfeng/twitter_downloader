# Journal - zdf (Part 1)

> AI development session journal
> Started: 2026-08-14

---



## Session 1: 认证/匿名解析接口开关与双路日志

**Date**: 2026-10-17
**Task**: 认证/匿名解析接口开关与双路日志
**Branch**: `feat/authenticated-extraction-switch`

### Summary

修复年龄限制推文只写空元数据的问题；改用认证 GraphQL 提取、自动加载 cookie，新增 GUI/CLI 接口开关与双路日志

### Main Changes

# 认证/匿名解析接口开关与双路日志

## 问题

`https://x.com/laosj666/status/1819930329953775785/video/1` 是年龄限制内容。程序对外报告
`Saved to:`，但目录里只有空 `tweet.json`，没有任何媒体文件。

排查确认两处缺陷：

1. `YtDlpTweetExtractor._extract_status()` 直接调用 yt-dlp 的 `_call_syndication_api`（匿名接口），
   绕开了 yt-dlp 自带的认证分支。匿名访问只拿到 `TweetTombstone`。
2. 空/受限响应被当作正常结果，静默写出空元数据。

同时确认环境限制：Chrome 正在运行，cookie 数据库被独占锁定（WinError 32），且使用应用绑定加密
（`app_bound_encrypted_key`），当前 yt-dlp 无法解密；Firefox 无 X 登录态。因此通过 Chrome DevTools
的 Application 面板读取会话 cookie，导出为 Netscape 格式供程序使用。

## 实现

### 1. 认证提取

- `_extract_status()` 改用 yt-dlp 的 `_extract_status`，按登录态自动走 GraphQL。
- `_restriction_reason()` 识别 `TweetTombstone` / `TweetUnavailable`，以及缺少 `id_str`/`user`/`full_text`
  的空壳响应，抛出明确错误。
- `TweetWithVisibilityResults` 自动解包 `tweet` 字段。
- 视频改用 `video_info` 中码率最高的 MP4 直链，避免二次解析。

### 2. 接口开关（默认认证）

- `YtDlpTweetExtractor(..., use_authenticated=True)`；`False` 时向 yt-dlp 注入
  `extractor_args={"twitter": {"api": ["syndication"]}}`。
- GUI 在 Cookies 下方新增 `Use authenticated API (GraphQL + cookies)` 复选框，默认勾选并持久化到
  `config.json` 的 `use_authenticated`。
- CLI 新增 `--anonymous`；默认即认证路径。

### 3. 自动 Cookie 加载

`cookie_sources()` 按序惰性产出：显式参数 → `%APPDATA%\TwitterDownloader\cookies.txt` →
`./cookies.txt` → 已登录 X 的浏览器。探测使用静默 logger，避免污染终端输出。

### 4. 双路日志

新增 `twitter_downloader/logging_setup.py`：

- 控制台 handler：默认 INFO，`--verbose` 时 DEBUG。
- 文件 handler：始终 DEBUG 全量，2 MB 轮转、保留 5 份。
- 默认路径 `%APPDATA%\TwitterDownloader\logs\twitter_downloader.log`，`--log-file` 可覆盖。
- 重复调用幂等，不会重复添加 handler。
- CLI、GUI、core、downloader、extractor 全部接入同一 logger；GUI 日志区内容同步落盘。

## 验证

- `python -m unittest discover -s tests`：26 个用例全部通过。
- 认证模式实测下载 2 个 MP4（851,916 / 757,087 字节），`ffmpeg` 解码无错误。
- 匿名模式对同一推文返回明确错误并提示启用认证接口。
- 控制台与日志文件内容一致，文件额外保留 DEBUG 行（cookie 探测与接口选择细节）。

## 提交

- `f9b2d84` feat: 支持认证接口解析并新增匿名/认证开关
- `7d07071` feat: 日志双路输出并补充中文注释
- `ce97a98` chore(task): archive authenticated-extraction-switch


### Git Commits

| Hash | Message |
|------|---------|
| `f9b2d84` | (see git log) |
| `7d07071` | (see git log) |
| `ce97a98` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete

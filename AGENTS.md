# Twitter X Downloader

使用 `yt-dlp` 实现 Twitter/X 推文下载（视频转 MP4、图片转 JPEG、文章正文元数据），按账号+日期分类。

## Project
- 功能：从 Twitter/X 链接下载视频、图片及元数据
- 技术栈：Python 3 + yt-dlp + Pillow + requests + Rich + Click
- 入口：`twitter_downloader`
- 支持：`python -m twitter_downloader`

## Commands
- Python 路径：`D:\adunsoftware\python\python3.14.4\python.exe`
- pip 路径可用：`D:\adunsoftware\python\python3.14.4\Scripts\pip.exe`
- 安装依赖：`D:\adunsoftware\python\python3.14.4\Scripts\pip.exe install requests rich Pillow click yt-dlp`
- 运行单条：`python -m twitter_downloader <URL>`
- 展开帮助：`python -m twitter_downloader --help`

## Architecture
- `twitter_downloader/`
  - `cli.py`：CLI 入口，支持 `single`
  - `downloader.py`：`Downloader`，视频、图片、元数据统一落盘
  - `extractor.py`：`YtDlpTweetExtractor`，解析推文信息
  - `models.py`：`TweetInfo`、`User`、`MediaItem`、`ArticleBlock`
  - `output.py`：按账号/日期分类目录生成
  - `utils.py`：URL 校验、HTTP 下载、图片转 JPEG

## Conventions
- 面向对象设计，少量 CLI Python Script 在顶层
- `Downloader.download()` 返回落盘目录字符串；异常抛 `RuntimeError`
- 图片统一转 JPEG，视频含 `verbose=True` 调试 `yt-dlp`
- 单个 URL 最大并行线程数：8；目录按 `username/YYYYMMDD/tweet_id` 组织

## Notes
- 项目处于早期阶段，已有目录级模块骨架
- 依赖安装命令中的 `Pillow` 保持 PEP 503 规范写法

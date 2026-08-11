# Twitter Downloader

基于 yt-dlp 的 Twitter / X 内容下载器。支持单条推文下载，并自动按 用户名/日期/推文ID 分类保存视频、图片及元数据。

## 功能
- 视频下载并转为 MP4
- 图片下载并转为 JPEG
- 输出 tweet.json / tweet.md 元数据
- 自动生成分类目录

## 环境
- Python 3.8+

## 安装
可以直接使用当前环境中的 yt-dlp：

```bash
D:\adunsoftware\python\python3.14.4\python.exe -m pip install --upgrade yt-dlp Pillow requests
```

如需要 CLI 扩展依赖：

```bash
D:\adunsoftware\python\python3.14.4\python.exe -m pip install rich click
```

## 使用方法

```bash
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader <URL>
>D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader single https://x.com/Fn_dapty/status/2087064228775460908/video/1 --verbose
```

## 输出目录结构

downloads/  <username>/    <YYYYMMDD>/      <tweet_id>/        <tweet_id>_1.jpg        tweet.json        tweet.md

## 常见问题
- 若出现 yt-dlp 版本较旧导致 Twitter 解析失败，请先升级 yt-dlp。
- 若目标内容需要登录态，可使用 yt-dlp 支持的 cookies 方式。
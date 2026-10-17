# Twitter / X Downloader

基于 `yt-dlp` 的 Twitter / X 推文下载器。支持下载文字、图片、视频和元数据，并按 `用户名/日期/推文 ID` 分类保存。

## 功能

- 视频下载并封装为 MP4
- 图片下载并转换为 JPEG
- 保存 `tweet.json` 与 `tweet.md` 元数据
- 支持单条和批量推文下载
- 每条推文内的图片下载最多使用 8 个线程

## 环境与安装

- Python 3.8+
- `ffmpeg`：下载包含独立音频流的视频时，`yt-dlp` 使用它合并为 MP4

安装项目依赖：

```powershell
D:\adunsoftware\python\python3.14.4\Scripts\pip.exe install -r requirements.txt
```

`yt-dlp` 对 X 的解析会随站点变化，发生解析错误时可先升级它：

```powershell
D:\adunsoftware\python\python3.14.4\python.exe -m pip install --upgrade yt-dlp
```

## 图形界面

安装依赖后，从项目根目录启动 Windows 图形界面：

```powershell
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader.gui
```

打包为 Windows 安装包的命令和 Inno Setup 配置见 [PACKAGING.md](PACKAGING.md)。

## 单条下载

以下两种形式等效：

```powershell
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader https://x.com/Fn_dapty/status/2087064228775460908/video/1
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader single https://x.com/Fn_dapty/status/2087064228775460908/video/1 --verbose
```

使用 `--output` 指定保存根目录：

```powershell
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader single <URL> --output E:\downloads
```

## 批量下载

创建 UTF-8 编码的文本文件，例如 `urls.txt`。每行填写一个 X/Twitter 推文链接；空行和以 `#` 开头的行会被忽略，重复链接只下载一次。

```text
# videos
https://x.com/Fn_dapty/status/2087064228775460908/video/1
https://x.com/example/status/1234567890123456789
```

执行批量下载：

```powershell
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader batch urls.txt --output E:\downloads
```

任务按文件顺序执行。某一条失败不会阻止后续链接继续下载；命令最后会输出成功/失败数，只要存在失败项就以非零状态码退出，便于脚本或 CI 检测。

## 登录态与调试

私密内容、成人限制内容或需要登录的内容可以传入 Netscape 格式 cookie 文件，或读取浏览器 cookie：

```powershell
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader batch urls.txt --cookies E:\cookies.txt
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader single <URL> --cookies browser=chrome --verbose
```

不传 `--cookies` 时程序会自动加载登录态，顺序为：

1. `%APPDATA%\TwitterDownloader\cookies.txt`
2. 当前目录下的 `cookies.txt`
3. 已登录 X 的浏览器（`chrome`、`edge`、`firefox` 等）

也可以显式写出 `--cookies auto`，效果与不传相同。把浏览器导出的 cookie 放到上述路径后，后续下载无需再指定参数。

### 解析接口（认证 / 匿名）

提取元数据有两条接口路径，命令行和 GUI 都可切换：

| 模式 | 命令行 | GUI | 说明 |
|---|---|---|---|
| 认证接口（默认） | 默认 / 不加参数 | 勾选 “Use authenticated API (GraphQL + cookies)” | 走 X 的 GraphQL 接口并携带登录态，可读取成人限制、仅登录可见等内容 |
| 匿名接口 | `--anonymous` | 取消勾选该选项 | 走公开的 syndication 接口，不发送 cookie；遇到受限内容会直接报错 |

```powershell
# 使用匿名接口（不推荐用于受限内容）
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader single <URL> --anonymous
```

GUI 中的该开关会自动保存到配置文件，下次启动保持上次选择。

> `yt-dlp` 无法读取正在运行且使用应用绑定加密的 Chrome cookie 数据库时，请先把 cookie 导出为 Netscape 文本文件，或关闭浏览器后重试。

运行 `D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader --help` 查看完整命令帮助。

## 输出目录

```text
downloads/
  <username>/
    <YYYYMMDD>/
      <tweet_id>/
        <tweet_id>_video_1.mp4
        <tweet_id>_1.jpg
        tweet.json
        tweet.md
```

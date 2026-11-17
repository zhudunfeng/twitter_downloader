# Twitter / X Downloader

基于 `yt-dlp` 的 Twitter / X 推文下载器。支持下载文字、图片、视频和元数据，并按 `用户名/日期/推文 ID` 分类保存。

## 功能

- 视频下载并封装为 MP4
- 图片下载并转换为 JPEG
- 保存 `tweet.json` 与 `tweet.md` 元数据
- 支持单条和批量推文下载
- 每条推文内的图片下载最多使用 8 个线程
- 内置离线作品库，按 Twitter / X 单列推文时间线浏览已下载内容
- 同一条推文中展示正文、图片拼图与视频；支持长文展开、完整文章阅读和正文复制
- 图片支持放大、拖动、前后切换；视频支持应用内播放、进度拖动、静音、倍速与全屏
- 搜索本地正文、作者或推文 ID，按作者、发布日期、媒体类型筛选；导入目录后重启仍可浏览
- 本地喜欢与收藏独立保存；支持自定义收藏夹，同一作品可加入多个收藏夹
- “喜欢”“收藏”单独查看，全部收藏自动去重，单个收藏夹内继续搜索与筛选

## 环境与安装

- Python 3.10+（开发验证使用 Python 3.14.4）
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

启动后默认进入**作品库**。左侧可切换“喜欢”“收藏”“下载”和“设置”；前三个入口复用同一推文阅读器。窄窗口会收起导航文字与右侧筛选栏，点击“筛选”仍可设置作者、日期与排序。

### 浏览已有下载

1. 在作品库点击“导入本地作品”或“添加下载目录”，选择原来的 `downloads` 根目录；也可选择单个作品目录。
2. 后台扫描时可以继续阅读。列表中点击图片查看大图、点击视频封面播放，长文可展开；回车或菜单中的“阅读完整正文”打开可选择复制的全文及已保存文章。
3. 新下载会自动入库。浏览历史内容时只提示“有新作品”，点击后才回到顶部。通过命令行新增或手动移动文件后，在作品库点击“刷新”。

原始文件保留原位。作品库兼容旧版 `tweet.json`、`tweet.md` 和既有独立图片/视频编号，缺少或损坏的元数据会显示恢复提示；缺失的头像使用本地占位，未保存的发布日期和互动数据不作补造。只有文章链接而没有保存正文的作品会明确显示未保存全文。

### 媒体操作

- 图片：滚轮或按钮缩放，拖动平移，左右方向键切图，`Esc` 返回；“原始分辨率”按需读取大图。
- 视频：默认不自动播放。播放控件提供播放/暂停、进度、音量/静音、倍速与全屏；聚焦播放器时可用空格、`F`、`Esc`。同一时间只使用一个播放器，离开页面或视频滑出可见区域时暂停。小拼图、额外媒体或空间不足时，在应用内视频窗口播放。
- 正文：列表保持原换行，详情页支持选择、复制；右键菜单可复制原帖链接、打开原帖或所在文件夹。打开外部网页和系统播放器均需主动点击。

阅读本地内容不需要 cookies，也不会自动加载远程头像或媒体。内嵌视频能否解码取决于 Qt 多媒体后端、具体编码和系统支持；不支持或损坏的视频会显示错误，并提供“使用系统播放器打开”。`ffmpeg` 可生成视频封面，缺少它时保留占位封面，仍可点击播放受支持的视频。

### 喜欢与多收藏夹

- 点击推文下方的“喜欢”切换本地喜欢状态，左侧“喜欢”查看已喜欢的作品。列表选中作品后可用 `Ctrl+L` 操作；详情页和右键菜单也提供相同入口。
- 点击“收藏”或按 `Ctrl+B` 打开收藏夹多选窗口，可同时勾选多个收藏夹。新收藏预选默认收藏夹，点击“保存”后生效；取消窗口不改变作品归属。可在窗口内新建收藏夹，新夹会自动勾选，仍须保存才归属作品。
- “收藏”页可切换“全部收藏”、默认收藏夹和自定义收藏夹，也可打开“管理收藏夹”新建、重命名或删除。名称去除首尾空白，限 1–40 个字符，不含控制字符，按统一 Unicode 规则拒绝重名。默认收藏夹不能改名或删除。
- 同一作品加入 A、B 后，在两个夹内均可查看，“全部收藏”只出现一次。右键“从【收藏夹】移除”只移除当前归属；最后一个归属移除后取消收藏。“取消全部收藏”会移除该作品的全部归属，喜欢不受影响。
- 删除自定义收藏夹不删除作品文件；因此失去全部归属的作品转入默认收藏夹，仍在其他夹中的作品保留原归属。收藏夹是逻辑分类，不在磁盘中移动原文件。
- 喜欢默认按最近喜欢排序，全部收藏按本轮首次收藏时间排序，单夹按加入时间排序；添加其他收藏夹不重置首次收藏时间。也可切换为发布日期排序。搜索、作者、日期及媒体类型筛选只作用于当前列表。
- 喜欢、收藏仅保存在本机，不会向 X 发送操作。推文中的“原帖快照”计数保持下载时记录，不随本地喜欢变化。已有下载内容导入后即可使用这些功能，无需重新下载。

### 索引、个人数据与缓存

Windows 下设置和可重建的 `library.sqlite3` 存放于 `%APPDATA%\TwitterDownloader`，预览缓存位于 `%LOCALAPPDATA%\TwitterDownloader\previews`。它们与原始下载文件分开存放；关闭程序后可删除索引/预览并重新导入原目录。应用日志为同一配置目录下轮转的 `desktop.log`。

喜欢、收藏夹及归属保存在同一配置目录的 **`personal.sqlite3`**，这是不能从原始下载重建的个人数据，**不要当作缓存删除**。普通刷新、索引重建、同根目录同路径重新导入会保留个人记录。备份整个应用数据目录前应先关闭程序；删除整个目录也会删除个人记录。个人库读取或写入失败时会明确显示错误并提供重试，不会自动清空重建；主作品库仍可阅读。

个人标记以“下载根目录＋作品相对目录”的现有身份为准：不同根目录的副本独立，移动或重命名根目录/作品目录不会自动迁移标记。原件被移除后，相应记录暂不出现在可读列表，个人状态保留，原路径重新导入可恢复。

下载设置保存根目录及可选 cookie 来源。已有下载任务进行时，关闭窗口会提示等待完成，以免损坏未完成的媒体。

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

私密内容或需要登录的内容可以传入 Netscape 格式 cookie 文件，或读取浏览器 cookie：

```powershell
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader batch urls.txt --cookies E:\cookies.txt
D:\adunsoftware\python\python3.14.4\python.exe -m twitter_downloader single <URL> --cookies browser=chrome --verbose
```

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

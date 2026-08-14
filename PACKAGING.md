# Windows 构建与打包

本文说明如何在 Windows 上完整构建 `TwitterDownloader`：先使用
PyInstaller 构建 `onedir` 应用目录，再使用 Inno Setup 将该目录制作成
Windows 安装程序。除非命令中另有说明，所有命令都应在项目根目录执行。

采用 `onedir` 模式是有意的。Qt、`yt-dlp` 及其运行时文件会保留在应用目录
中，便于检查目标机器缺少的系统依赖。

## 1. 准备构建环境

构建机器需要准备以下环境：

- Windows 10 或更高版本。
- 项目使用的 Python：
  `D:\adunsoftware\python\python3.14.4\python.exe`。如果使用其他 Python 3
  安装路径，请同步修改下面命令中的路径。
- 可在 `PATH` 中找到的 `ffmpeg`。`yt-dlp` 需要它将独立的视频流和音频流
  合并为 MP4。PyInstaller 不会自动打包 `ffmpeg`。
- Inno Setup 6，用于构建安装程序。下载地址：
  <https://jrsoftware.org/isinfo.php>。

先检查工具是否可用：

```powershell
Set-Location E:\pyprojects\twitter_downloader

$Python = "D:\adunsoftware\python\python3.14.4\python.exe"
$Pip = "D:\adunsoftware\python\python3.14.4\Scripts\pip.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python was not found at $Python"
}
if (-not (Test-Path -LiteralPath $Pip)) {
    throw "pip was not found at $Pip"
}

& $Python --version
& $Pip --version
ffmpeg -version
```

如果找不到 `ffmpeg`，请先安装 Windows 版本，并将其 `bin` 目录加入
`PATH`，然后重新打开 PowerShell 窗口再执行上述检查。

## 2. 安装 Python 依赖

安装项目运行时依赖以及固定版本的 PyInstaller：

```powershell
Set-Location E:\pyprojects\twitter_downloader

$Pip = "D:\adunsoftware\python\python3.14.4\Scripts\pip.exe"
& $Pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Application dependency installation failed"
}

& $Pip install pyinstaller==6.22.0
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller installation failed"
}
```

应用依赖在 `requirements.txt` 中声明。PyInstaller 是构建阶段依赖，单独
安装可以避免普通运行环境必须安装它。

## 3. 执行构建前检查

创建发布产物前，先运行单元测试和 Python 编译检查：

```powershell
Set-Location E:\pyprojects\twitter_downloader

$Python = "D:\adunsoftware\python\python3.14.4\python.exe"
& $Python -m unittest discover -s tests -p 'test*.py' -v
if ($LASTEXITCODE -ne 0) {
    throw "Unit tests failed"
}

& $Python -m compileall -q twitter_downloader gui_launcher.py
if ($LASTEXITCODE -ne 0) {
    throw "Python compilation check failed"
}
```

同时检查命令行入口：

```powershell
& $Python -m twitter_downloader --help
```

## 4. 构建 PyInstaller 应用

`gui_launcher.py` 是冻结应用的入口文件。它使用绝对导入，确保 PyInstaller
启动可执行文件时能够稳定解析 GUI 包。

下面的命令会将临时文件统一写入 `.build\windows`，不会与源代码目录中常规的
`dist` 或 `build` 目录混用：

```powershell
Set-Location E:\pyprojects\twitter_downloader

$Python = "D:\adunsoftware\python\python3.14.4\python.exe"
$BuildRoot = Join-Path (Get-Location) ".build\windows"

New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --noconsole `
    --onedir `
    --name TwitterDownloader `
    --paths . `
    --distpath (Join-Path $BuildRoot "dist") `
    --workpath (Join-Path $BuildRoot "work") `
    --specpath $BuildRoot `
    gui_launcher.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed"
}
```

构建完成后，应用目录和可执行文件应位于：

```text
.build\windows\dist\TwitterDownloader\
.build\windows\dist\TwitterDownloader\TwitterDownloader.exe
```

请保留整个应用目录。可执行文件依赖旁边的 DLL、Qt 平台插件、Python 扩展
和其他数据文件，不能只复制 `.exe` 文件。

## 5. 测试冻结后的应用

需要交互测试时，直接启动可执行文件：

```powershell
$Executable = (Resolve-Path ".build\windows\dist\TwitterDownloader\TwitterDownloader.exe").Path
& $Executable
```

在没有桌面显示器的构建机上，可以使用 Qt 的 `offscreen` 平台进行冒烟测试。
该测试会确认冻结后的 Qt 运行时和插件能够加载，并让进程短暂保持运行：

```powershell
$Executable = (Resolve-Path ".build\windows\dist\TwitterDownloader\TwitterDownloader.exe").Path
$PreviousQtPlatform = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = "offscreen"
$Process = $null

try {
    $Process = Start-Process -FilePath $Executable -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 4
    if ($Process.HasExited) {
        throw "Frozen application exited early with code $($Process.ExitCode)"
    }
}
finally {
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force
    }
    $env:QT_QPA_PLATFORM = $PreviousQtPlatform
}
```

该冒烟测试不会下载推文。要进行端到端测试，请使用真实的推文 URL 和有效的
Cookie 文件。

## 6. 构建 Inno Setup 安装程序

安装脚本为 `installer\TwitterDownloader.iss`。它需要一个包含
`TwitterDownloader.exe` 及其全部依赖文件的目录。`SourceDir` 预处理变量
用于指定上一步生成的 `.build\windows\dist` 应用目录。

查找 `ISCC.exe` 并构建安装程序：

```powershell
Set-Location E:\pyprojects\twitter_downloader

$IsccCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$Iscc = $IsccCandidates |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1

if (-not $Iscc) {
    throw "ISCC.exe was not found. Install Inno Setup 6 first."
}

$SourceDir = (Resolve-Path ".build\windows\dist\TwitterDownloader").Path
& $Iscc "/DSourceDir=$SourceDir" "installer\TwitterDownloader.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed"
}
```

安装程序生成位置为：

```text
dist\installer\TwitterDownloader-Setup.exe
```

该路径由 `.iss` 文件中的 `OutputDir=..\dist\installer` 定义，路径是相对
于 `installer\TwitterDownloader.iss` 所在目录计算的。

运行生成的 `TwitterDownloader-Setup.exe`，按照安装向导完成安装。默认安装
位置在 `%ProgramFiles%` 下，同时创建开始菜单快捷方式，并提供创建桌面快捷
方式的选项。安装结束后，安装向导默认会启动应用。

## 7. 发布前检查清单

分发安装程序前，请确认：

1. `requirements.txt` 中的依赖版本正确。
2. `VERSION` 与 `installer\TwitterDownloader.iss` 中的 `AppVersion` 一致。
3. 单元测试和 `compileall` 检查均通过。
4. `TwitterDownloader.exe` 能在干净的 Windows 用户环境中启动。
5. 如果视频需要合并音视频流，目标机器的 `PATH` 中包含 `ffmpeg`。
6. 使用测试 URL 下载后，能够在输出目录中得到预期的 `tweet.json`、
   `tweet.md`、JPEG 和 MP4 文件。

## 8. 常见问题

### 找不到 Python 或 pip

将命令中的 `$Python` 和 `$Pip` 修改为实际路径。两个路径必须来自同一个
Python 安装，否则依赖可能被安装到 PyInstaller 无法访问的位置。

### 可执行文件启动后立即退出

先通过 Python 模块入口启动 GUI，以便在控制台中查看 traceback：

```powershell
& $Python -m twitter_downloader.gui
```

确认问题后，使用 `--clean --noconfirm` 重新构建，并检查是否完整复制了
`TwitterDownloader` 目录，而不是只复制 `.exe` 文件。

### 视频下载或 MP4 合并失败

安装 `ffmpeg`，确认在同一个 PowerShell 会话中执行 `ffmpeg -version` 成功，
然后从能访问该 `PATH` 的环境启动打包应用。`ffmpeg` 是应用明确依赖的外部
运行时组件。

### Inno Setup 报告找不到源文件

确认 `$SourceDir` 指向包含 `TwitterDownloader.exe` 的目录。传入目录路径，
不要传入 `.exe` 文件路径：

```powershell
Test-Path ".build\windows\dist\TwitterDownloader\TwitterDownloader.exe"
```

### 从干净目录重新构建

确认路径无误后，只删除生成目录并重新构建：

```powershell
Remove-Item -LiteralPath ".build" -Recurse -Force
Remove-Item -LiteralPath "dist" -Recurse -Force
```

上述目录只包含构建和安装程序产物，不包含源代码。

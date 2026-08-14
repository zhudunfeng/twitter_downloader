# Windows Packaging

The GUI entry point is `python -m twitter_downloader.gui`. Build it from the
repository root with the dedicated absolute-import launcher.

## Build the application

```powershell
D:\adunsoftware\python\python3.14.4\Scripts\pip.exe install -r requirements.txt pyinstaller
D:\adunsoftware\python\python3.14.4\python.exe -m PyInstaller --noconsole --onedir --name TwitterDownloader --paths . gui_launcher.py
```

The runnable application is `dist\TwitterDownloader\TwitterDownloader.exe`.

## Build the installer

Install Inno Setup 6, then run this from the repository root:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "/DSourceDir=$PWD\dist\TwitterDownloader" installer\TwitterDownloader.iss
```

The installer is written to `dist\installer`. Pass a different `SourceDir`
definition when the PyInstaller output lives elsewhere.

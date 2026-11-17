# Research: Twitter-like Local Works Timeline

- Query: How should the current PySide6 application present downloaded tweet text, images, and video in an attractive, smooth Twitter/X-like timeline?
- Scope: Mixed: repository inspection and primary Qt/PyInstaller documentation.
- Date: 2026-11-16.
- Assigned task: `.trellis/tasks/11-16-local-library-viewer`. The existing `.current-task` still names the older GUI task; this research follows the explicit new-task assignment and does not alter that pointer.

## Findings

### Repository evidence

| File | Role and relevant patterns |
| --- | --- |
| `requirements.txt:6` | Pins PySide6 6.11.1; no frontend JavaScript stack exists. |
| `twitter_downloader/gui/main.py:71` | Existing Widgets `MainWindow`; form, log and current-session `QListWidget` history. |
| `twitter_downloader/gui/main.py:208` | `_on_finished(path)` is the natural incremental indexing hook. |
| `twitter_downloader/gui/main.py:235` | History currently opens a folder, rather than an internal reader. |
| `twitter_downloader/gui/config.py:8` | Per-user writable application-data location already exists; keep the library index/cache here, not the installation directory. |
| `twitter_downloader/models.py:10` | `MediaItem` already has optional `filename` and `size_bytes` fields, but the writer does not populate/persist them. |
| `twitter_downloader/downloader.py:54` | Media are downloaded before metadata; incomplete downloads may leave media without `tweet.json`. |
| `twitter_downloader/downloader.py:72` | Video names are `<tweet_id>_video_<per-type-index>.mp4`. |
| `twitter_downloader/downloader.py:130` | Image names are `<tweet_id>_<per-type-index>.jpg`. |
| `twitter_downloader/downloader.py:145` | `tweet.json` has text, article blocks, author, counts and remote media URLs, but no local media manifest. |
| `twitter_downloader/extractor.py:106` | Article extraction currently preserves only available plain text, not arbitrary rich article structure. |
| `tests/test_gui.py:13` | Current offscreen GUI tests cover signals/history; no timeline or actual multimedia playback coverage. |
| `PACKAGING.md:18` | The download pipeline depends on an external FFmpeg executable, distinct from Qt Multimedia's runtime libraries. |

### Product direction from the user's steering

Use one readable vertical tweet timeline as the primary view. A gallery/grid would undermine the requested Twitter-like browsing pattern. Each row contains a local/avatar placeholder, display name, handle, publication time, text, attached-media collage and optional read-only saved counters. Preserve original media ordering.

For 1–4 images, use familiar single, split, asymmetric three-image and 2x2 arrangements; reserve a stable media area before thumbnail arrival. Open images in a zoomable overlay/detail viewer. Video appears as a poster with a clear click-to-play affordance and plays inline only when activated. Stop or pause and detach video when its row leaves the viewport or selection changes. The optional right rail holds search and account/date/type filters; hide it in narrow windows. Keep full tweet/article text selectable in the detail reader; provide copy actions for painted timeline text rather than pretending that delegate-painted text supports native selection.

Do not invent missing avatars, verified status, quote/reply relationships, or live engagement. Saved counts are historical snapshots. Unavailable article data remains unavailable; the reader does not create content or add new extraction promises.

### Feasible approaches

| Approach | Fit | Trade-offs |
| --- | --- | --- |
| Extend PySide6 Widgets (recommended) | Reuses the actual main window, worker signals, theme, packaging and download core. Model/delegate painting bounds widget counts. | Variable row heights, media hit areas and the active video editor need careful layout/scroll testing. |
| Qt Quick/QML timeline | Strong alternative if an interaction prototype later proves Widgets inadequate. | Adds a second UI architecture, Python/QML boundary, QML assets and a larger migration. Not justified before measuring the existing stack. |
| Local web frontend | Familiar HTML/CSS content layout and browser-based media UI. | Introduces a frontend build/runtime boundary and local resource serving or an embedded web runtime; significantly expands deployment and ownership. |

These trade-offs are architectural assessments based on repository fit, not performance benchmarks or a claim that one toolkit is universally faster.

### Widgets timeline architecture and feasibility

- `QAbstractListModel` + `QListView` in single-column ListMode + `QStyledItemDelegate`; use batched layout and paginated fetching. Never create one permanent widget tree or video player per work.
- Variable-height tweet rows require `uniformItemSizes=False`. Cache layout/size calculations by stable work ID, width, content revision, media aspect ratio and expansion state. Keep list `data()`, `paint()` and `sizeHint()` free of disk I/O, SQL queries and decoding.
- Persist only source-derived metadata and local UI state. Use stable IDs, not row numbers, when associating results and active media.
- Background workers produce plain records or `QImage` previews; queued signals apply model changes in the GUI thread. Use a bounded worker pool and viewport-priority requests. Prefer `QImage` for worker-side decoding and convert to GUI display resources on the GUI thread.
- A single active delegate-created editor is the preferred supported seam for interactive media. `createEditor()` creates its widget, `updateEditorGeometry()` sizes it; explicitly open at most one editor for the active row and close it on exit. It is a read-only media surface, not a metadata edit.
- Alternatively, a single viewport-child media overlay can track `visualRect(index)` and the cached media subrectangle. This needs explicit clipping and repositioning on scroll, resize and relayout. Prefer the delegate editor if the early prototype passes.
- Do not use `setIndexWidget()` for every tweet. Qt documents it for static index content and directs custom dynamic content to a `QStyledItemDelegate` subclass.
- Preserve a scroll anchor as stable work ID plus pixel offset for expansion, incremental scans and prepended downloads. Queue a “new works” affordance when browsing history rather than forcing a jump to the top.
- Validate the editor/overlay approach with a small Windows interaction spike before committing to the full view. Actual scrolling/native video-window behavior is not proven merely by API availability.

### Text, local-media contract and offline behavior

Treat `tweet.json` plus the actual files as authoritative. Extend new manifests additively with a schema version and media-relative local paths, sizes and optional duration; keep existing fields. Store paths relative to the work directory so moving/importing a library remains practical. Validate resolution remains inside the work directory.

For legacy manifests, walk the original media order while maintaining separate image and video counters, then map each to existing named files. Never map the overall media-array position directly to a filename: mixed image/video posts would resolve incorrectly. Exclude temporary fragments and expose missing/corrupt files as local errors, not silent network fetches.

Render downloaded text using plain text or escaped, application-generated rich text. If Markdown is supported, disable embedded HTML and control resource loading. `QTextBrowser` can resolve local resources; disabling external link opening alone is insufficient to constrain embedded resources or file links. Limit resource loading to approved local work assets and make external HTTP(S) navigation an explicit click action. Avoid automatic remote images, scripts or inline web pages.

### Playback and deployment

Use one `QMediaPlayer`, `QAudioOutput` and `QVideoWidget`, loading a local file URL. Bind play/pause, seeking, time, volume/mute, fullscreen and `errorOccurred`; support playback speed only where the actual backend accepts it. Stop/reset old sources on work changes and shutdown. For unsupported/corrupt media, show a localized error and an explicit “open with system player” action.

Qt 6.11 uses FFmpeg as the principal multimedia backend. Codec support and hardware behavior still vary. MP4 is a container, so a filename extension does not guarantee playback. A working external `ffmpeg.exe` for yt-dlp is not proof that the required Qt multimedia plugin and compatible FFmpeg DLLs are in the installed application. Do not force the deprecated Windows Media Foundation backend as the default fix.

PyInstaller 6.22.0 has a PySide6 QtMultimedia hook and dependency collection, but this is not a guarantee that every target build is complete. Validate the onedir build and Inno installer on a clean Windows environment, including video+audio, seeking, fullscreen, missing codec/error handling and folder access. Include QtMultimedia/QtMultimediaWidgets imports in the actual feature; inspect collected plugins/DLLs when the smoke test fails.

Video thumbnails may use the existing FFmpeg executable in a bounded background subprocess when available. Return a placeholder if unavailable, time-limited, or failed; thumbnail generation must never block opening the library or playback. Do not instantiate players for every thumbnail.

### Proposed performance acceptance targets (not measurements)

- Benchmark with an explicitly documented Windows machine and SSD fixture containing 10,000 works, mixed images/video and several long texts.
- With a warm index, show the first useful timeline rows within 1 second after the main window is shown; measure whole-process cold startup separately.
- Search/filter response target: at most 200 ms after a short input debounce on the indexed fixture; collect p95 timings.
- Target 60 Hz scrolling on the agreed reference display, and investigate main-thread stalls over 50 ms; measure rather than promise universal FPS.
- Only one active video decoder/player; no growth in player/widget counts as users scroll. Memory should stabilize under a documented cache budget during repeated browsing.
- Cold import reports progress and supports interruption; the window remains usable. Missing thumbnails do not delay metadata display.

## External References

All source pages below are primary documentation or upstream source, fetched on 2026-11-16. Qt 6.11 documentation verifies major/minor API contracts; actual pinned-wheel and frozen-build behavior still needs the later implementation tests.

1. Qt for Python 6.11 `QListView`: batched layout, batch sizes and uniform-size contract. https://doc.qt.io/qtforpython-6.11/PySide6/QtWidgets/QListView.html
2. Qt 6.11 `QAbstractItemView`: static-content warning for `setIndexWidget`, persistent editors and `visualRect`. https://doc.qt.io/qt-6.11/qabstractitemview.html
3. Qt 6.11 `QStyledItemDelegate`: `paint`, `sizeHint`, `createEditor`, `updateEditorGeometry`. https://doc.qt.io/qt-6.11/qstyleditemdelegate.html
4. Qt item-model thread safety and incremental fetch contracts. https://doc.qt.io/qt-6/qabstractitemmodel.html
5. Qt threading modules: `QImage` painting is supported; painting on widgets from workers is not. https://doc.qt.io/qt-6/threads-modules.html
6. Qt for Python 6.11 `QMediaPlayer`: audio output, local sources, video widget and asynchronous errors. https://doc.qt.io/qtforpython-6.11/PySide6/QtMultimedia/QMediaPlayer.html
7. Qt 6.11 `QVideoWidget`: one display output per media object, aspect ratio and fullscreen. https://doc.qt.io/qt-6.11/qvideowidget.html
8. Qt 6.11 Multimedia: backend, runtime deployment and format caveats. https://doc.qt.io/qt-6.11/qtmultimedia-index.html
9. Qt 6.11 `QTextBrowser`: link handling and local resource resolution. https://doc.qt.io/qt-6.11/qtextbrowser.html
10. Qt `QTextDocument`: Markdown features including `MarkdownNoHTML` and resource loading hooks. https://doc.qt.io/qt-6/qtextdocument.html
11. PyInstaller v6.22.0 QtMultimedia hook. https://raw.githubusercontent.com/pyinstaller/pyinstaller/v6.22.0/PyInstaller/hooks/hook-PySide6.QtMultimedia.py

## Related Specs

- `.trellis/workflow.md`: persist research and keep plan separate from implementation.
- `.trellis/spec/guides/cross-layer-thinking-guide.md`: make download-to-manifest-to-index-to-view contracts explicit.
- `.trellis/spec/backend/index.md`, `error-handling.md`, `quality-guidelines.md`: mostly unfilled templates; do not mistake placeholders for established implementation conventions.
- `AGENTS.md`: object-oriented Python, preserve account/date/tweet layout, `Downloader.download()` public result and CLI behavior, at most eight download workers.

## Caveats / Not Found

- No real playback, installed-library import, GUI rendering, performance benchmark or frozen-app test was executed for this planning research.
- No QML or web frontend infrastructure was found; introducing either is new scope.
- No complete local media manifest or persisted work library was found.
- No stored avatar/verification/thread relationship contract was found. A Twitter-like UI must not imply those fields exist.
- Offscreen GUI tests do not prove audio/video presentation; a clean Windows interactive acceptance run is required during implementation.
- Built-in web results provided no usable text in this run; primary pages were verified using read-only HTTPS retrieval. No packages were installed.

# Implementation Coordination

The user approved implementation on 2026-11-16. Follow the existing PRD, prioritizing the Twitter-style single-column timeline.

Implementation is complete and ready for human review. Independent checking found
no confirmed product blocker; final quality, real-event-loop media edges and
same-machine frozen playback pass. See `research/integration-verification.md` for
evidence and limits. The 10k-row two-second startup target, clean installation,
Linux runtime and complete accessibility acceptance are not established. No
commit or archive is authorized/performed at this stage.

## Repository Instructions

- Every new/modified method must have a Chinese docstring, full parameter/return type hints, Chinese entity-field comments, configuration explanations, key business inline comments, and Chinese business logs.
- Preserve pre-existing AGENTS.md changes and tracked bytecode modifications. Set PYTHONDONTWRITEBYTECODE=1 when running Python.
- User requires generated unit-test classes to be removed after completion. Use temporary assertion/function-based verification scripts in `.build-verification/` where possible; preserve pre-existing tests.
- Prefer codegraph for lookup if available; fall back to bounded file reads/search if it is not installed/usable.

## Ownership

- Data implementer: `twitter_downloader/library/`, `twitter_downloader/downloader.py`, temporary data checks only.
- Timeline implementer: `gui/timeline_model.py`, `gui/tweet_delegate.py`, `gui/timeline_view.py`, `gui/thumbnail_service.py`, temporary timeline checks only.
- Root: `gui/library_page.py`, `gui/video_player.py`, `gui/image_viewer.py`, `gui/tweet_detail.py`, shell/config/theme, documentation, integration and delivery verification.
- Everyone shares a workspace; preserve other edits and coordinate interface changes before changing them.

## Shared Data Contract

`twitter_downloader.library.models` exports frozen dataclasses:

- `LocalMedia(path: str, media_type: str, width: int | None = None, height: int | None = None, exists: bool = True, size_bytes: int | None = None, signature: str = '')`. `path` is validated absolute local path, never an untrusted remote URL; missing assets may have `exists=False`.
- `LibraryWork(key: str, root: str, directory: str, tweet_id: str, username: str, display_name: str, created_at: str | None = None, text: str = '', article_blocks: tuple[str, ...] = (), article_urls: tuple[str, ...] = (), media: tuple[LocalMedia, ...] = (), url: str = '', like_count: int | None = None, retweet_count: int | None = None, reply_count: int | None = None, warning: str = '', signature: str = '')`.
- `LibraryQuery(search: str = '', author: str = '', kind: str = '', since: str = '', until: str = '', sort: str = 'newest')`. Empty kind means all; other kinds are `image`, `video`, `text`, `article`. Dates are ISO date strings or empty.

`LibraryRepository(db_path: str | Path)` owns a same-thread sqlite connection. Methods: `upsert(work) -> None`, `query(query: LibraryQuery | None = None, offset: int = 0, limit: int = 80) -> list[LibraryWork]`, `count(query: LibraryQuery | None = None) -> int`, `authors() -> list[tuple[str, int]]`, `close() -> None`. UI jobs create/use/close their own repository in their own worker. Schema 2 persists the absolute canonical root plus relative work/media paths; UI entities are restored to validated absolute paths. Schema 1 migrates without changing key/first_seen. First initialization is serialized per process with bounded cross-process retry; normal connections do not repeat schema writes.

`read_work(directory: str | Path, root: str | Path) -> LibraryWork` is tolerant and validates all file paths.

`LibraryScanner(repository).scan(roots: list[str], on_progress: Callable[[int, str], None] | None = None, cancelled: Callable[[], bool] | None = None) -> int` imports/updates/prunes available roots and returns processed count. Missing/offline roots must not purge existing cached rows. Cancelled scans must not prune unfinished roots.

## Timeline/UI Contract

- `ThumbnailService(cache_dir: str | Path, parent: QObject | None = None)`; `ready = Signal(str)` emits media path; `request(media: LocalMedia, size: QSize) -> QPixmap | None` returns memory cache or schedules bounded background work without file I/O on paint; `close() -> None` stops/joins its owned pool safely.
- `TimelineModel` exports `WORK_ROLE`; `set_works(list[LibraryWork])`, `append_works(list[LibraryWork])`, `work_at(row: int) -> LibraryWork | None`, `row_for_key(key: str) -> int`.
- `TimelineView(thumbnails: ThumbnailService, parent: QWidget | None = None)` exposes `.timeline_model`, `.tweet_delegate`; signals `open_detail(object)`, `open_media(object, int)`, `menu_requested(object, QPoint)`, `visible_changed()`, `near_end()`.
- View methods: `set_works(works: list[LibraryWork], preserve_anchor: bool = True)`, `append_works(works)`, `capture_anchor() -> tuple[str, int] | None`, `restore_anchor(anchor)`, `media_rect(key: str, media_index: int) -> QRect` in viewport coordinates, `scroll_to_top()`.
- Cached row heights and media rectangles must match actual painting/hit testing. Feed text is painted; full selection/copy is provided in detail.
- The root mounts one real VideoPlayer widget as a viewport child over an ordinary single-video rectangle, and hides/pauses it when not fully in view; timeline view emits visible_changed on scroll/resize/layout. Small collages, extra media and insufficient viewports use an in-app video dialog with the same player. Fullscreen restore returns to the original parent and single-player layout, or viewport geometry.

## Integration API Owned by Root

- `LibraryPage(roots: list[str], db_path: Path, cache_dir: Path, parent=None)` offers `add_root(path: str)`, `refresh()`, `index_directory(path: str, root: str)`, `open_directory(path: str, root: str)`, `shutdown() -> bool`, `pause_playback()`, and signals `roots_changed(list)`, `status_changed(str)`, `download_requested()`. Directory jobs are single active with latest-open and coalesced-upsert queues.
- LibraryPage is created lazily on first show, so legacy MainWindow tests do not launch background jobs. Main window chooses the library initially in a deferred event and preserves existing public download-control attributes/tests.
- VideoPlayer is one shared local QMediaPlayer/QAudioOutput/QVideoWidget with explicit controls and close/fullscreen lifecycle.

## Verification

Use `D:/adunsoftware/python/python3.14.4/python.exe`. Existing PySide6 is 6.11.1; local yt-dlp is 2025.12.8 despite requirements pin, so mock download tests and avoid changing installed packages without a concrete need. ffmpeg/ffprobe are on PATH. Run existing unittest coverage and meaningful temporary local-fixture checks. Never claim targets were met without measured evidence. Preserve originals and make the SQLite catalog/caches rebuildable.

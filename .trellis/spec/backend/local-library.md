# Local Library and Desktop Timeline Contract

## 1. Scope / Trigger

Use this contract when changing downloaded-work metadata, local catalog storage,
scanning, the Widgets timeline, preview workers, or Qt Multimedia playback.
These layers share persisted paths and asynchronous completion events. A mistake
can expose incomplete downloads, lose a reading position, or stop application
shutdown. Downloaded originals remain authoritative; the catalog and previews
are disposable derived data.

## 2. Signatures

```python
Downloader.download(tweet: TweetInfo) -> str  # Existing constructor/callback contract remains.
read_work(directory: str | Path, root: str | Path) -> LibraryWork
LibraryRepository(db_path: str | Path)
repository.upsert(work: LibraryWork) -> None
repository.query(query: LibraryQuery | None = None,
                 offset: int = 0, limit: int = 80) -> list[LibraryWork]
repository.count(query: LibraryQuery | None = None) -> int
repository.authors() -> list[tuple[str, int]]
repository.prune(root: str, seen_keys: set[str], *,
                 started_at: float | None = None) -> int
repository.close() -> None
LibraryScanner(repository: LibraryRepository)
scanner.scan(roots: list[str], on_progress: Callable[[int, str], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> int
```

UI contracts:

- `LibraryPage(roots, db_path, cache_dir, parent=None)` exposes `add_root`,
  `refresh`, `index_directory`, `open_directory`, `pause_playback`, and
  `shutdown() -> bool`. It emits `roots_changed`, `status_changed`, and
  `download_requested`.
- `TimelineModel` owns immutable `LibraryWork` values and exports `WORK_ROLE`.
  `set_works`, `append_works`, `work_at`, and `row_for_key` never access storage.
- `TimelineView.capture_anchor() -> tuple[str, int] | None` records stable work
  identity plus its viewport pixel offset. `restore_anchor` uses the same pair.
- `ThumbnailService.request(media: LocalMedia, size: QSize) -> QPixmap | None`
  returns a cached GUI-thread pixmap or schedules bounded work; `ready(str)`
  identifies the local media path, and `close()` joins owned workers.

Catalog schema: SQLite `PRAGMA user_version = 2`, table `library_works` with
`key` primary key, `root`, `directory`, `author`, nullable `published_date`,
`sort_at`, `first_seen`, `search_text`, `has_image`, `has_video`, `has_article`,
`is_text`, `signature`, and JSON `payload`. Indexes cover sort, author, root, and
publication date. Schema 1 migrates in place; a future schema fails explicitly.

## 3. Contracts

### Metadata and paths

- New `tweet.json` snapshots add `schema_version: 2`. Media entries retain source
  order and add relative `filename`, zero-based `order`, and actual `size_bytes`.
  Existing fields and file names retain their meanings.
- Images and videos have **independent** one-based filename counters. Source
  `image, video, image` maps to `id_1.jpg`, `id_video_1.mp4`, `id_2.jpg`.
- Finish media and Markdown before atomically replacing final JSON. Only then
  emit completion. A failed replacement preserves prior JSON and removes its
  temporary file. The public download result remains a directory `str`.
- Reader paths are resolved and contained in the selected root/work directory.
  Reject remote schemes, parent traversal, absolute manifest filenames, drive
  prefixes, temporary fragments, and link/junction escapes. Do not fetch remote
  media while reading local metadata.
- UI entities use absolute local paths. SQLite stores canonical absolute `root`,
  work `directory` relative to root, and media paths relative to work, with
  `paths_relative: true` in payload. Deserialize to validated absolute entities
  without filesystem I/O. Never use relative UI paths.
- Identity is qualified by the canonical root and relative work directory:
  repeated imports deduplicate; a separately stored copy has its own identity.
- Preserve absent data as absent: `created_at=None`, unknown author as empty
  strings, missing counts as `None`, missing media as `exists=False`. Do not infer
  publication date from directory labels. Unknown dates sort by stable
  `first_seen`, not the latest rescan time; ties use stable key order.
- `LibraryQuery` supports literal case-insensitive substring search, exact
  author, inclusive ISO dates, kind `image|video|text|article|''`, and
  `newest|oldest`. Bind all user SQL parameters and escape LIKE `%`, `_`, and
  backslash. Pagination clamps offset to nonnegative and limit to 1..10000;
  normal UI pages contain 80 works.

### Thread ownership and lifecycle

- Every SQLite connection is created, used, and closed in its worker thread.
  Same-process initialization is serialized, cross-process BUSY/LOCKED has a
  bounded retry, and an initialized connection does not execute schema writes.
- Complete scans prune only their accessible root and only records first seen
  no later than scan start. Cancellation, traversal errors, and unavailable roots
  preserve cached rows. A concurrent successful download must survive pruning.
- GUI catalog results use request generations. Directory jobs are single active;
  opening history keeps the latest pending selection, while completion upserts
  coalesce by directory. Import during scanning schedules another scan.
- `QImage` decoding, disk cache access, and FFmpeg posters run in workers.
  `QPixmap`, widgets, and model mutation remain on the GUI thread. Painting,
  model reads, row measurement, and preview requests perform no SQL/disk/decode.
- Preview defaults: 3 workers, at most 48 pending requests, estimated pixel-memory
  LRU target 128 MiB, disk target 512 MiB, and layout LRU 256 entries. Disk pruning
  is periodic, so the target is not a strict instantaneous disk high-water mark.
  FFmpeg poster processes time out after 5 seconds. Shutdown clears queued work
  and joins active owned workers; no QThread is destroyed while running.
- Full-image viewing has one decoder plus the latest pending request. Discard
  stale results. Both `done()` and `closeEvent` stop and join that decoder.
- Use **one** `QMediaPlayer/QAudioOutput/QVideoWidget` per library page. Ordinary
  single videos play over their timeline media rectangle. Small collages,
  additional media, and insufficient viewports use an in-app dialog with the
  same player. Entering fullscreen reparents it; exiting restores its original
  parent and single-player dialog layout, or viewport geometry. Page changes/
  out-of-view pause playback.
- Do not autoplay, load remote resources, or automatically launch a system
  player. Unsupported local playback gets an explicit error and user action.
- MainWindow constructs LibraryPage lazily after show. A completed callback is
  not QThread completion: enable another download only after `finished`. Ignore
  window close during an active download and explain that it must finish.

### Layout and configuration

- A single tweet timeline is primary, with painted author/text/media rows,
  thin separators, 1/2/3/4-media arrangements, and explicit extra-media access.
  Feed width caps at 700 logical pixels and receives expansion priority over
  the trailing spacer. Compact windows move the filter rail into a dialog;
  return it at its original layout position.
- Refresh, append, resize, and text expansion preserve stable selection and
  pixel anchor while the work remains. New downloads display a banner while
  reading history rather than forcing the scroll position to zero.
- Config keys: `output_dir`, `cookies_path`, `library_roots`,
  `window_geometry.width/height`. Defaults and Chinese parameter explanations
  live in `gui/config.py`; saves are atomic.
- Windows config/catalog/logs: `%APPDATA%/TwitterDownloader`; previews:
  `%LOCALAPPDATA%/TwitterDownloader/previews`. Missing environment variables use
  `~/.config` and `~/.cache`. No catalog/cache writes beside installed binaries.
  No additional product environment key is required by this feature.

## 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Bad/missing JSON | Recover available Markdown/plain text/media, show warning, keep other works readable |
| Missing local media | Preserve unavailable item and show an honest placeholder/error |
| Escaping/remote media path | Exclude that asset; never read outside validated boundaries |
| Offline/cancelled/partially unreadable scan | Preserve cached rows; no incomplete-root pruning |
| SQLite BUSY/LOCKED or read-only disk | Surface/retry appropriately; **do not** rename as corruption |
| SQLite CORRUPT/NOTADB | Back up catalog and sidecars with timestamp, rebuild derived index |
| Future schema | Fail clearly; never downgrade or delete the catalog |
| Malformed cached payload | Skip/remove only invalid derived row so pagination can recover |
| Poster failure/missing ffmpeg | Neutral cover; allow supported local playback |
| Windows build PATH contains foreign ICU DLLs | Isolate build PATH to Python/Windows, clean analysis cache, then run the frozen executable |
| Unsupported/corrupt video | Local error with explicit system-player fallback; other rows remain usable |
| Fast navigation/close | Bound queues, discard stale results, stop/join workers and release media |

## 5. Good / Base / Bad Cases

- **Base:** an existing legacy text/photo/video directory imports once, reopens
  offline, and opens full text and local media without moving files.
- **Good:** mixed legacy media retains type-specific filename order; a schema-1
  catalog migrates to relative paths without changing keys/first_seen; a new
  download during scanning survives pruning and shows a non-jumping banner.
- **Bad:** a malformed JSON asset points to `../../outside.mp4`, the root goes
  offline, a video cannot decode, or catalog initialization competes with another
  process. Each case must stay local, preserve originals, and report recoverably.

## 6. Tests Required

- Existing CLI/downloader/GUI regression suite and eight-thread limit.
- Reader assertions: old mixed order, Chinese/English/emoji/CRLF, Markdown/text
  recovery, missing files, signatures, explicit zero vs absent counters, and
  path/link/fragment rejection.
- Repository assertions: reopen/dedup, literal search, date/type/author paging,
  stable unknown-date sorting, relative schema-2 payloads, v1 migration,
  damaged/future schema behavior, and thread/process initialization contention.
- Scanner assertions: unavailable/cancelled roots do not prune; deleted works
  prune only after a complete scan; new concurrent completions survive.
- Qt assertions: exact paint/hit rectangles, expansion, keyboard selection,
  prepend/resize/append anchor offsets, cache caps, stale results, decoder/worker
  shutdown, compact rail restore, and single directory-task queue.
- Real Windows Qt + H.264/AAC fixture: valid video frames and advancing position,
  both tracks, seek/mute/rate, same player identity across fullscreen/dialog,
  page-switch pause, download-callback vs thread-finished lifecycle, and shutdown.
- Drive asynchronous multimedia tests with a real QApplication.exec event loop
  and bounded QTimer states. Include actual replay frames and end/replay/immediate
  stop. A continuous manual processEvents loop can omit DeferredDelete cleanup;
  compare against the real event loop before changing product lifecycle code to
  fix a test-only native shutdown hang.
- Frozen onedir: actually decode audio/video tracks, verify Qt Multimedia plugins
  and FFmpeg runtime DLLs, and retain the entire `_internal` tree. Same-machine
  verification does not replace clean-machine installation or Linux testing.
  Inspect collected dependency sources if QtCore import fails: a third-party
  `icuuc.dll` with versioned exports cannot satisfy Qt's unversioned Windows ICU
  imports. Use the isolated-PATH build procedure in `PACKAGING.md`, not DLL copies
  from unrelated tools. Run test probes after standard PyInstaller runtime hooks.
- Record 10k-record latency with environment/cache conditions. Offscreen samples
  do not establish native FPS, end-to-end search latency, or total process memory.

Respect the project requirement to remove newly generated unittest classes at
completion. Temporary function/assertion harnesses belong in ignored
`.build-verification/`; preserve existing maintained tests.

## 7. Wrong vs Correct

**Wrong:** use the overall mixed-media position as the local filename suffix,
share SQLite connections with the GUI, persist arbitrary absolute asset paths,
or assume `on_done` means its QThread can be destroyed.

**Correct:** maintain independent media counters, instantiate repository inside
its owning worker, serialize contained root-relative paths, and let
`QThread.finished` govern thread teardown and the next download.

```python
# GUI scheduling supplies paths/plain values; the worker owns its connection.
repository = LibraryRepository(db_path)
try:
    work = read_work(directory, root)
    repository.upsert(work)
finally:
    repository.close()
```

**Wrong:** restore a fullscreen player only with `setParent(old_parent)` or give
the timeline and empty tail equal stretch. The player leaves its layout, and the
timeline receives only part of available space.

**Correct:** restore the original single-player layout and allocate stretch so the
timeline expands to its 700-pixel cap before the tail consumes the remainder.

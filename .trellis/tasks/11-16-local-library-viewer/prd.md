# Downloaded Works Timeline — Implementation and Acceptance

Date: 2026-11-16

Status: implementation completed on 2026-11-16 and ready for human review. Release verification limits and unproven performance targets are recorded below.

## Goal and Confirmed Requirements

- Read downloaded Twitter/X text, images, and videos directly in the existing desktop application.
- Closely resemble the Twitter/X single-column tweet-list browsing style. A gallery or masonry grid is not the primary view.
- Treat attractive presentation and smooth browsing as core requirements.
- Implement the confirmed modification plan, including local browsing and regression verification.

## Repository Findings

- `twitter_downloader/gui/main.py` is a PySide6 Widgets download window; its history opens local folders and is limited to the current session.
- `gui/resources.py` already supplies a dark theme, blue accents, and Segoe UI/Microsoft YaHei font fallbacks.
- `Downloader.download()` saves media and metadata, emits `on_done(target_dir)`, and returns the directory string.
- Output layout is `<root>/<username>/<YYYYMMDD>/<tweet_id>/`, with `tweet.json`, `tweet.md`, JPEG images, and MP4 videos.
- JSON includes author, date, text, article blocks, remote media URLs/dimensions, and optional engagement counts, but not local filenames.
- Images use `<tweet_id>_<image_index>.jpg` and videos use `<tweet_id>_video_<video_index>.mp4`; each type is independently numbered from one.
- `MediaItem` already has optional `filename` and `size_bytes`, but the writer does not populate/persist them.
- Avatars, verification state, quote trees, reply threads, and full rich article structures are not currently saved.
- PySide6 is pinned to 6.11.1. Download work already runs in QThread; the image downloader has an eight-worker maximum.
- Windows delivery uses PyInstaller onedir and Inno Setup; the shared CI also produces Linux artifacts.
- No populated sample works were found under the workspace `downloads/` directory. Fixture and real-media validation are implementation work.

## Proposed Viewing Experience

### Layout

- Keep a single window with **Library**, **Downloads**, and **Settings** navigation.
- Library layout: narrow left navigation; centered tweet timeline about 600–680 logical pixels wide; optional right rail with search and account/date/type filters.
- Compact windows collapse the right rail into a filter drawer and condense navigation. Avoid horizontal scrolling.
- Keep download progress visible through a compact status entry. Put verbose logs in the Downloads page.
- Import the configured output directory and support adding an existing download root. Browsing remains available offline and after restart.

### Tweet Rows and Reading

- Row hierarchy: avatar/placeholder → display name, handle, publication time → original text → inline media → optional read-only saved counters.
- Preserve text paragraphs, line breaks, Unicode, and emoji. Long text has a visible expand action; the tweet/article detail reader supports selection and copying.
- Use thin separators, quiet backgrounds, consistent alignment, and restrained hover/selection feedback to recreate a timeline rather than a collection of oversized cards.
- Photo layouts: one image; two side by side; three as large-plus-stacked; four as 2×2. Provide an explicit view-all/count for additional media.
- Show photo previews with stable geometry. Full-image viewing preserves aspect ratio and offers zoom, fit, previous/next, and Escape to close.
- A video row initially shows a poster and duration when available. An explicit click starts inline playback; offer pause, seek, volume/mute, supported speed changes, and fullscreen.
- Only one video is active. Pause when the row leaves view or the user switches content/pages, and release resources on close.
- Preserve the current work, expansion state, focus, and visible pixel offset when opening/closing media, expanding text, or refreshing.
- When new works arrive while reading history, show a new-works banner; do not force a jump to the top.
- Secondary actions: copy text/link, open original post, and open containing folder.
- Existing social counts are historical, read-only snapshots; omit missing values and avoid fake interactive social buttons.
- Missing avatars use deterministic local placeholders. Do not fabricate verification, quote/reply context, or absent article text. Locally saved article blocks are readable in full; a source URL is not equivalent to an archived article.

### Visual and Accessibility Direction

- Extend the current dark blue theme and improve typography, spacing, media framing, icon consistency, and focus states.
- Suggested starting tokens: 15–16 logical-pixel body text, 1.45–1.6 line height, 4/8 pixel spacing scale, and restrained media rounding; validate under Windows text scaling.
- Use short interruptible transitions only where helpful and support reduced motion. Avoid per-row entrance effects during scrolling.
- Provide accessible names, visible focus, and Tab/Enter/Escape/arrow/Space controls appropriate to the focused content.
- Design searches did not yield a suitable desktop timeline template. This proposal therefore uses the actual project theme and general desktop usability guidance, not unrelated returned marketing templates.

## Technical Approach and Decisions

### Framework Choice

Recommended: retain **PySide6 Widgets**, add a model/view timeline and **Qt Multimedia**, and keep the CLI and GUI on the existing download core.

| Approach | Benefit | Cost / Fit |
| --- | --- | --- |
| Extend current Widgets | Reuses shell, settings, worker signals, tests, and packaging. Delegates limit widget counts. | Variable heights and an active inline video surface need an early prototype. Recommended. |
| Qt Quick/QML viewer | Declarative interactive delegates and animation-oriented UI. | Adds QML deployment and a second UI architecture. Reserve if the Widgets prototype exposes a material limitation. |
| Local web frontend | Familiar browser feed layout tooling. | Adds frontend tooling, a serving/bridge lifecycle, and another deployment layer. Higher change scope. |

### Library and Metadata Contract

- Add a Qt-independent reader/scanner/repository layer. Original files remain authoritative; a SQLite catalog and previews are rebuildable.
- Put the catalog in writable per-user application data and previews in a cache directory, never beside installed program files.
- Store normalized root plus relative work/media paths, stable identity, author/date/text, media order/type, and invalidation signatures. Deduplicate repeated imports of the same location; keep root-qualified identities for separate copies.
- For legacy JSON, reconstruct media in original JSON order with independent image/video counters and verify actual file existence. Do not use the overall media position as the filename index.
- Tolerate missing fields and bad JSON. Fall back to available Markdown/plain text and recognized files with an incomplete-data indicator; do not invent author, date, or order.
- Prefer the recorded publication date; show unknown dates honestly and use a documented stable sort fallback. Directory dates are not proof of publication time.
- For future downloads, add optional `schema_version` and per-media relative `filename`, `order`, and actual `size_bytes` while retaining all current fields and file naming. Populate existing MediaItem attributes where possible.
- If extending the metadata writer, publish JSON atomically after the complete media/metadata snapshot is ready; preserve completion callback ordering and the public return type.
- Validate resolved files stay within the work/root. Exclude `.part`, temporary fragments, and conversion intermediates.
- Initial scanning and manual incremental refresh run in the background. GUI completion upserts only the finished directory; CLI downloads appear after refresh. A recursive watcher is not required initially.
- Search the catalog instead of reparsing all JSON. Start with parameterized indexed filters and practical Chinese/English substring search; do not assume default FTS tokenization handles Chinese.

### Timeline, Threads, and Resource Bounds

- Use QAbstractListModel + QListView + QStyledItemDelegate, incremental fetching, and batched layout. Variable-height rows must not assume uniform item sizes.
- Cache row measurement by stable work ID, width, content revision, and expansion state. Recompute affected/visible rows and restore the reading anchor after layout changes.
- Keep model data access, delegate painting, and size measurement free of SQL, disk I/O, and media decoding.
- Paint inactive rows and media covers. Create an interactive media surface only for the active post; prototype a delegate editor or one viewport overlay rather than permanent widgets for all posts.
- Run scanning, catalog work, thumbnail decoding/resizing, and poster extraction in bounded workers. Queued signals apply model/widget changes on the GUI thread; each SQLite connection belongs to its owning thread.
- Background image work returns QImage/plain data; create QPixmap and manipulate widgets/surfaces on the GUI thread.
- Use request generations and stable media identity to discard stale results after quick navigation, resizing, filtering, or root changes.
- Prioritize visible previews plus a small look-ahead region. Start with 2–4 preview workers, tune under downloading, and retain the downloader's existing concurrency limit.
- Use size-limited memory LRU and disk caches. Start with a 128 MiB thumbnail-memory budget; this is a tuning target, not a measured total-process limit.
- Cache keys include file identity, mtime/size, target dimensions, and display scale. Load previews in the feed and decode larger originals on demand.
- Reuse one QMediaPlayer + QAudioOutput + active QVideoWidget. Validate scrolling/clipping, fullscreen restoration, error recovery, and cleanup early.
- Generate video posters/duration through bounded background work, optionally the already-required external ffmpeg executable. Time out failed jobs and show a neutral cover; poster generation must not block reading.
- MP4 container/remux success does not guarantee decoder compatibility. Test real codecs and packaged Qt multimedia plugins/runtime libraries; an external ffmpeg.exe alone does not prove deployed playback works.
- Supported media must play in-app. Unsupported/corrupt media gets a clear error and an explicit system-player fallback; avoid automatic transcoding of the whole library.
- Render archived text as plain/escaped application-controlled rich text. Restrict resource loading and require explicit external navigation so viewing local works does not fetch remote media automatically.

## Proposed File Changes

All paths are relative to `twitter_downloader/` unless explicitly noted. These are intended boundaries; combine small modules when simpler.

| Area | Files | Work |
| --- | --- | --- |
| Window/navigation | `gui/main.py`, optional `gui/download_page.py` | Add library/download navigation and completion-to-library integration; retain worker lifecycle and download behavior. |
| Theme/settings | `gui/resources.py`, `gui/config.py` | Shared timeline styling, labels/icons/focus, library roots and view state. |
| Library data | new `library/models.py`, `reader.py`, `repository.py`, `scanner.py` | Tolerant legacy import, catalog records, SQLite, incremental updates. |
| Timeline | new `gui/library_page.py`, `timeline_model.py`, `tweet_delegate.py` | Feed, filters/search, variable-height rendering, hit regions, scroll anchors. |
| Detail/media | new `gui/tweet_detail.py`, `image_viewer.py`, `video_player.py` | Selectable article/text, image viewer, shared inline/fullscreen playback. |
| Preview jobs | new `gui/thumbnail_service.py` | Bounded jobs, priority, caches, invalidation, stale-result handling. |
| Media manifest | existing `models.py`, `downloader.py` as needed | Backward-compatible local media mapping and completion snapshot. |
| Delivery/verification | repository `tests/`, `README.md`, `PACKAGING.md`, build config as needed | Regression/GUI/media tests, viewer documentation, multimedia deployment and frozen-app validation. |

## Implementation Sequence

1. **Feed and playback proof of concept.** Build representative text-only, long-text, collage, portrait/landscape, and video rows. Establish visual tokens and prove variable heights, click handling, scroll anchoring, active inline player/fullscreen, and packaged Windows playback. Resolve these risks before expanding implementation.
2. **Existing-works data layer.** Implement tolerant readers, legacy filename mapping, persistence/import/refresh, filters/search, and successful-download upserts. Add future manifest fields where needed. Deliver offline/restart discovery without redownloading.
3. **Complete Twitter-style reading.** Integrate the timeline hierarchy, inline layouts, text expansion, saved article reader, image viewer, video controls, reading-position restoration, new-work banner, and keyboard/error states. Deliver the entire download-to-browse loop.
4. **Performance, polish, and release.** Tune lazy fetching, layout/preview caches, cancellation, memory limits, and log/progress frequency under concurrent downloading. Validate DPI/compact widths/accessibility/offline behavior, update tests/docs, and test a clean Windows installation. Check existing Linux build viability where shared packaging changes affect it.

## Acceptance Criteria

### Functional and Visual

- [x] Existing roots import without moving originals or downloading again; restart preserves discovery.
- [x] Primary view is a Twitter-like single-column feed showing author, handle, time, text, and inline media.
- [x] Text-only, image-only, multi-image, video, mixed-media, long Chinese/English/emoji text, and saved article blocks render correctly.
- [x] Legacy mixed media maps to the correct actual files and ordering.
- [x] Image zoom/navigation and video play/pause/seek/mute/fullscreen work; only one player is active.
- [x] Opening/closing previews, expansion, refresh, and new downloads preserve the reading anchor where the work still exists.
- [x] Missing metadata/media, invalid files, unavailable posters, and playback errors do not break unrelated posts.
- [x] Local reading works offline without cookies or remote thumbnails; unknown data is represented honestly.
- [x] Keyboard focus remains visible and layout works at compact/wide widths and 100%/150%/200% Windows scaling.
- [x] CLI commands/public contracts, output hierarchy, and eight-worker download limit remain compatible.

These checks use isolated fixtures, native Windows Qt, and existing regressions.
Native H.264/AAC decoding and a same-machine frozen build pass. A complete codec
matrix, physical audio/video-surface inspection, clean-system installation,
Linux runtime and full accessibility audit are not claimed.

### Performance Targets — Partial Evidence, Not Full Acceptance

Record the reference Windows machine, SSD/storage, display scaling, Qt/backend version, media codec/dimensions, and cold/warm cache state before benchmarking. Use 10,000 catalog works and representative mixed-content rendering fixtures.

- [ ] With an existing catalog, initial usable timeline targets 2 seconds or less; full cold import remains background, progress-reporting, and cancellable.
- [ ] Indexed search/filter p95 targets 300 ms or less after a 200 ms input debounce; measure end-to-end latency separately.
- [ ] Cached selection/expand feedback targets 100 ms or less; slower decoding receives immediate loading feedback.
- [ ] Aim for 60 Hz scrolling; investigate application-controlled GUI callbacks blocking for 100 ms or longer, including during downloads. Measure frame/input timing rather than claim universal FPS.
- [ ] A successful GUI download appears or triggers a new-work banner within 1 second of completion.
- [ ] Preview caches remain bounded; repeated long scrolling/open/close does not accumulate original images, widgets, decoders, or unbounded memory.

Final isolated 10k-record query+count: median 27.495 ms, p95 57.473 ms, excluding
input debounce and GUI updates. Native first 80 rows: 2804.754 ms; the stable
two-second target is not met. Paging/layout/queue bounds and single-decoder image
navigation pass; total RSS, disk high-water, end-to-end input latency and sustained
60 Hz scrolling have not been measured. Keep the proposed targets unchecked
rather than substitute narrow measurements for release acceptance.

## Verification and Definition of Done

- Unit tests: parser tolerance, per-type counters, filename/path validation, date/sort fallback, catalog upsert/dedup/rebuild, changed/missing files, cache invalidation.
- Fixture-based Qt tests: filters, expansion/anchor, detail selection, new-work banner, stale results, player switching, and shutdown cleanup.
- Interactive desktop checks: text/collage fit, high DPI/compact layout, keyboard/focus, fullscreen restoration, single audio source, and simultaneous download/browsing.
- Real frozen-app checks: supported downloaded MP4 audio/video, seek/end/error states, deployed Qt plugins/runtime libraries, and missing poster-tool handling. Offscreen startup is insufficient to prove playback.
- During implementation, run the existing unittest suite and Python compilation check and verify CLI help/download flow compatibility; update user and packaging documentation.
- Implementation checks and their limits are recorded in [integrated verification](research/integration-verification.md), with separate data and timeline reports.

## Out of Scope and Reversibility

- First version excludes cloud sync, social posting, live engagement, fabricated reply/quote reconstruction, recursive live watching, bulk deletion, and automatic library-wide transcoding.
- Gallery mode, favorites/tags, and richer collection can be evaluated after the timeline browsing loop is stable.
- Keep the catalog/previews rebuildable and separate from originals. Additive metadata changes must not force migration or change existing field meanings.

## Research References

- [Viewer architecture research](research/viewer-architecture.md) — Primary Qt/PyInstaller sources, approach comparison, repository contracts, and implementation caveats.

## Decision Status

- User-confirmed: view text/images/videos directly; attractive and smooth; prioritize Twitter/X tweet-list browsing.
- Implemented: existing Widgets shell + model/view timeline + rebuildable SQLite catalog + local previews + shared Qt Multimedia player.
- Review-ready: user-authorized implementation is complete; changes remain uncommitted for human testing and review. Clean-install and remaining performance release acceptance stay open.

# Timeline Implementation Verification

Date: 2026-11-16

## Scope and handoff

Implemented files:

- `twitter_downloader/gui/timeline_model.py`
- `twitter_downloader/gui/tweet_delegate.py`
- `twitter_downloader/gui/timeline_view.py`
- `twitter_downloader/gui/thumbnail_service.py`

The public APIs match `info.md`. Additional delegate helpers are `layout_for`, `hit_test`, `toggle_expanded`, `invalidate_layouts`, and `set_viewport_width`. The root's single-player overlay uses `TimelineView.media_rect` in viewport coordinates. Media after the fourth item intentionally has no inline rectangle; root handles those through the shared-player reading window.

No product files outside this ownership were modified by the timeline implementer. Temporary function/assertion scripts are under ignored `.build-verification/timeline-*`; no unit-test class was introduced. Product writes stopped after the final lint/fixture checks, for root integration and independent review.

## Environment

- Windows; Intel Core i7-8700 3.20 GHz, 12 logical processors.
- Workspace E: is on KINGSTON SNV3S1000G, approximately 1 TB SSD.
- Python 3.14.4 at the repository-specified executable.
- PySide6/Qt 6.11.1.
- FFmpeg `N-122527-g4561fc5e48-20260122`.
- Qt platform `offscreen`, logical viewport 680×860 and 420×800, scale factors 1, 1.5, and 2.
- The synthetic video is a 640×360, one-second H.264/yuv420p MP4. Image fixtures are 1200×800 and 500×1000 JPEGs.
- `PYTHONDONTWRITEBYTECODE=1` and `python -B` were used. Existing tracked bytecode changes were preserved.

## Checks passed

1. Ruff check and formatting on all four product files.
2. Python syntax compilation without writing bytecode, plus AST assertions that every method has a docstring, parameter annotations, and return annotation. No repository-configured standalone type checker was found; this is not a claim of complete static type checking.
3. `.build-verification/timeline-check.py` at 100%, 150%, and 200% scale:
   - Pure text, Chinese/English/emoji, long text, saved article indicator, missing media warning, single/two/three/four/more-than-four and mixed media layouts.
   - Paint measurement and size hints agree; media rectangles stay within the row, do not overlap, and preserve original media indices.
   - Mouse media activation, keyboard Enter/Space activation, and text expansion.
   - Prepending a new work during refresh, viewport resize from 680 to 420, and duplicate page append preserve stable work `row-30` at top offset -37 logical pixels.
   - Current keyboard selection survives reset using its stable work identity.
   - Real local JPEG and MP4 thumbnail generation completes; subsequent requests return GUI-thread pixmaps.
   - Pending thumbnail jobs remain at or below 48.
4. `.build-verification/timeline-stress.py`:
   - 10,000 model records indexed and deduplicated correctly.
   - 1,600 records loaded through 80-row pages; sampled distant scrolls complete.
   - Layout objects stay at the 256-entry LRU cap.
   - Sample run: 10k model reset/index map 2.0 ms; first 80 rows plus one event pass 96.3 ms; slowest of nine sampled scroll-plus-event passes 40.1 ms.

Those timings are narrow synthetic, offscreen observations, not a 10,000-work SQLite import/search benchmark, a whole-process startup result, or a measured display frame rate. Images/posters may be warm in the dedicated fixture cache in repeated runs. Actual multimedia playback is root-owned and was not established by poster extraction.

## Visual inspection and environment caveat

The offscreen Qt platform reports an empty `QFontDatabase.families()` in this environment, causing even ASCII to render as missing-glyph boxes. The temporary visual script explicitly loads the installed Segoe UI, Microsoft YaHei, and Segoe UI Emoji font files with `QFontDatabase.addApplicationFont`; product font behavior was not changed to work around the test platform. After that, the actual timeline Chinese/Latin author, body, date and footer text renders correctly. The timeline uses 15 px body text with a fixed 23 logical-pixel line height, thin row separators, a restrained hover/selection background, and deterministic local avatar placeholders.

Rendered fixture captures are at `.build-verification/timeline-fixture/timeline-wide.png` and `timeline-compact.png`. Their media image content is synthetic, not a downloaded user work. The geometry reserves the same area before and after preview completion.

## Implementation bounds and remaining integration work

- Painting, model reads, layout measurement and `ThumbnailService.request` contain no filesystem/SQL/media decoding. Image decoding, cache I/O, cache pruning and bounded FFmpeg poster extraction run in three owned workers; QPixmap creation remains on the GUI thread.
- Preview cache targets are 128 MiB estimated pixel memory and 512 MiB disk, with disk pruning at startup and every 96 completions. Actual process RSS and steady-state disk high-water mark have not been measured. Disk usage can briefly exceed the pruning target between sweeps.
- A 48-request cap and increasing priority favor new visible requests. Already-running poster subprocesses have a five-second timeout; close clears queued work and joins active work. It does not forcibly interrupt an in-progress image decoder.
- Unsupported/corrupt preview assets keep neutral covers. The failure memo is bounded. The root's media UI supplies actual playback errors and explicit system-player fallback.
- Root should validate native-window/frozen-build rendering and actual video+audio, fullscreen restoration, player clipping on scroll, very small viewport fallback, page-switch pause, and complete application shutdown. Offscreen assertions do not prove those behaviors.
- Root should run the existing application suite, full integration checks and any final spec/documentation updates. No universal 60 Hz, 2-second startup, or 300 ms search performance target is claimed here.

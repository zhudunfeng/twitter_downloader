# Integrated Verification — Local Library Viewer

Date: 2026-11-16. All input works/media are isolated local fixtures; no original
download directories or personal settings were modified. Commands use
`PYTHONDONTWRITEBYTECODE=1`. No commit was created.

## Environment and Scope

- Windows 10 build 19045, Intel Core i7-8700 3.20 GHz, SSD KINGSTON SNV3S1000G.
- Python 3.14.4, PySide6/Qt 6.11.1, PyInstaller 6.22.0, Pillow 12.3.0.
- Actual installed yt-dlp is 2025.12.8, unlike the requirements pin; network
  extraction/download was mocked. Dependencies were not upgraded by this task.
- Native integration platform is `windows`, not `offscreen`. Actual fixture
  video is MP4, H.264 Constrained Baseline/yuv420p, 960x540, 25 fps, eight seconds,
  plus AAC LC mono 48 kHz audio. JPEG fixtures are 960x640.
- Verification harnesses are function/assertion scripts under ignored
  `.build-verification/`. No new unittest classes were retained. Existing tests,
  user AGENTS.md changes, tracked bytecode changes, and install ZIP were preserved.

## Product and Existing Regression Checks

- Ruff: GUI, library, and downloader **pass**.
- Existing `unittest discover -s tests -p 'test*.py' -v`: **14/14 pass**.
- `python -m twitter_downloader --help`: **pass**; single/batch entrypoints remain.
- In-memory compilation and Python-3.10 AST parsing: **19 files pass**, without
  generating bytecode. Methods' Chinese docstrings/type annotations were checked.
- Formal static type checker: **not run**; none is configured/installed. AST and
  annotation checks are not substitutes for mypy/pyright or Python-3.10 execution.
- Data assertions: legacy mixed numbering, recovery, path rejection, signature
  changes, stable dates, filters, scan cancellation/pruning races, and atomic JSON
  completion **pass**; see [data report](data-verification.md).
- Independent reviewer assertions **pass**: 120 concurrent thread opens, six
  process opens, existing-catalog read while a writer holds a lock, relative-path
  schema migration, bad-payload rejection, compact/wide layouts, rail restoration,
  single active directory job, fullscreen layout restoration, light media icons.

## Native Main-Window Integration

`.build-verification/integration-check.py` was run again with a **fresh catalog**
and fresh fixture root at `.build-verification/integration-final`.

Passed:

- Lazy library creation, background import of 100 works, search, media filters,
  reading anchor refresh, and non-jumping new-work banner.
- Correct 700-pixel wide timeline, compact layout/filter dialog, and saved article
  paragraphs in the selectable detail reader.
- Thirty quick image switches with at most one active decoder; Escape/reject
  ends the decoder safely.
- Native local playback with **13 valid video frames**, recognized audio/video
  tracks and advancing position; normal single video actually plays inline.
- Seek to approximately four seconds, volume 33%, mute, 1.5x rate, fullscreen
  round trip and unchanged QMediaPlayer identity.
- Page switch pauses; fifth mixed-media video uses the same player in an in-app
  dialog; compact media still opens in-app.
- Mock successful download calls on_done before QThread.finished: duplicate
  launch stays blocked, close is ignored while the worker runs, completed history
  opens the in-app reader, and shutdown joins all owned jobs.

Native `timeline-check.py` also passes at **150% and 200%** scale. At both scales,
103 rows, hit testing, expansion, keyboard actions, local previews, and exact
anchor `('row-30', -37)` survive prepend/resize/append. The earlier offscreen
100%/150%/200% checks and 10k-model stress results are described in the
[timeline report](timeline-verification.md).

Inspected rendered fixture captures: `main-wide.png`, `main-compact.png`, and
`image-viewer.png`. QWidget.grab does not capture the native video surface here.
The Windows desktop screenshot helper failed twice (`FrameArrived timed out`,
then `window capture timed out`), so **interactive native video-surface visual
inspection is not claimed**. Decoding/track/position evidence is recorded
separately; it does not prove physical speaker output or universal codecs.

## Frozen Windows Build

An onedir build of the actual `gui_launcher.py` succeeds. The test-only runtime
probe runs after standard PyInstaller runtime hooks and supplies isolated config,
catalog, cache, and media. It is **not** part of documented production builds.

- Both console and final **noconsole** executables launch and actually decode.
- Latest noconsole result: Qt 6.11.1, `windows`, **7 valid frames**, audio/video
  tracks present, 700-pixel inline timeline, 1.598 seconds from showEvent to the
  playback assertion (not whole-process launch latency).
- Collected: Qt6Multimedia.dll, Qt6MultimediaWidgets.dll,
  ffmpegmediaplugin.dll, windowsmediaplugin.dll, avcodec-61.dll,
  avformat-61.dll, avutil-59.dll, swresample-5.dll, swscale-8.dll.
- Initial build failed at QtCore import because PATH supplied Poppler's ICU 78
  DLL with versioned exports, while Qt imports Windows ICU's unversioned symbols.
  A clean rebuild with Python/Windows-only PATH fixes it; no system DLLs were
  copied. PACKAGING.md now uses/restores an isolated build PATH.
- Existing optional yt-dlp build warnings (curl_cffi/js/pycparser tables) were
  observed; unrelated dependencies were not installed.

This is a **same-machine frozen application test**. No clean Windows installation,
Inno Setup run, Linux build, or fresh internet download is claimed.

## Performance Evidence and Limits

Reference: native Windows, scale 100%, **10,000 synthetic catalog records**,
existing catalog, warm OS catalog cache; offline synthetic root intentionally
cannot be scanned. Media placeholders mix text and image rows. Before the final
reference run, a stale test process was terminated and no other Python media,
frozen application, FFmpeg, or packaging process remained.

`catalog-benchmark.py`, 60 sequential query+count samples:

- Final median **27.495 ms**, p95 **57.473 ms** for repository query+count, including
  deserialization of up to 80 works. This excludes the 200-ms input debounce and
  GUI application of search results; it is not end-to-end input latency.
- Final native first 80 rows: **2804.754 ms**. An earlier sample measured
  **2569.158 ms**, but a stale media-edge test process was later found, so that
  sample is not an isolated reference. An instrumented profiling run measured
  **1029.027 ms** (LibraryPage construction ~230 ms and QWidget.show ~626 ms).
  These runs have different system/cache/profiling conditions; the
  **two-second target is not consistently established**.
- Prior concurrent-build sample was 2897.501 ms and is not the isolated reference.
- Full synthetic seeding initially took 5.235 seconds; repeated unchanged upserts
  are much faster and are not an initial-import figure.
- Timeline stress loads 1600 rows in 80-row pages from a 10k model, layout LRU
  stays <=256, thumbnail pending <=48. See timeline report for narrow timings.
- Preview targets are 128 MiB estimated pixel memory and 512 MiB disk with
  periodic pruning. No total-process RSS/high-water claim or measured 60 Hz claim.
- GUI completion/banner latency and callback/frame p95 were functionally checked,
  but not separately timed against every proposed threshold. Sustained concurrent
  real downloading, cold media/library import and full accessibility audit remain
  release acceptance work.

## Additional Media Edge Check and Test-Driver Correction

The final `.build-verification/media-edges-event-loop.py` uses a real
`QApplication.exec()` loop with QTimer state transitions. Three consecutive
native Windows runs **pass**, each with 14 valid video frames, two actual replay
frames, and a total check duration of 1.642–1.676 seconds. Results are in
`.build-verification/integration-final/media-edges-event-loop-runs.json`.

Covered: image zoom/original decode/close; missing-video and corrupt-video error
messages followed by valid playback; seek to end; restart and receive actual
replay frames; reach end again, restart and immediately stop within the same
timer callback; close without hanging. A separate real-loop immediate-stop
probe also passed five consecutive runs.

The older `media-edges.py` continuously called `processEvents()` instead of
running the application event loop. It hung in native AudioRenderer shutdown
after end/replay. Disconnecting frame callbacks, removing corrupt-source
recovery, and using native play did not change it. Explicit DeferredDelete
dispatch in that manual pump passed, as did the real-loop controls. The evidence
therefore supports a **test-driver cleanup-timing artifact**, not a confirmed
product defect. The formal harness was corrected; product lifecycle code was
not changed to accommodate an incomplete test event loop. The obsolete harness
is not counted as passed. No decoder compatibility beyond the recorded fixture
or physical speaker output is inferred from these results.

## Final Review Status

The independent check found no confirmed product blocker. Final Ruff, 14/14
existing tests, CLI help, and 19-file in-memory syntax/Python-3.10 grammar plus
method-docstring/annotation checks were repeated successfully. No product source
changed after the successful frozen build. Implementation is ready for human
review; the release acceptance limits above remain open. No commit or task
archive was performed.

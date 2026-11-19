# Reviewer Verification — Local Likes and Bookmark Folders

Date: 2026-11-17

The reviewer loaded the current task, PRD, check context and referenced contracts,
then traced storage, controller, timeline, dialogs, navigation and media call paths.
Implementation and documentation remain uncommitted for human review.

## Findings (fixed)

- File: `twitter_downloader/gui/library_page.py`
  - Issue: `_position_player` returned immediately for fullscreen/dialog placement,
    so removing the playing work from a personal list could leave it playing.
  - Fix: Check work presence and page visibility before placement-specific returns.
    Pause without replacing the player, clearing its source, or playing a neighbor.

- Files: `twitter_downloader/gui/library_page.py`,
  `twitter_downloader/gui/personal_state_controller.py`
  - Issue: Ordinary write failures rolled back but did not expose the retry button;
    a read-only query failure exposed a button whose controller retry did nothing.
    Retrying an older failed target while newer writes were pending could also
    supersede current intent.
  - Fix: Expose `has_failed_operation`, distinguish saved-operation retry from
    query recovery, requery when no write/reinitialization needs retry, and disable
    UI/controller retry while accepted writes are pending. An unrelated successful
    operation does not hide a still-unresolved failed target.

- File: `twitter_downloader/gui/library_page.py`
  - Issue: A successful query prepared before personal storage became unavailable
    could arrive later and replace the explicit error view with stale rows.
  - Fix: Invalidate the query generation, pending query and delayed reconciliation
    before showing personal unavailability. Pause any now-hidden active media.

- Files: `twitter_downloader/gui/library_page.py`,
  `twitter_downloader/gui/personal_state_controller.py`
  - Issue: Any failed operation cleared every picker save registration, losing
    the close-on-confirmation association of another accepted successful save.
    Global failure handling also unlocked forms while later writes were pending.
  - Fix: Add `operation_failed(action, key)` and clear only the failed operation's
    registration. Return a boolean acceptance result from controller mutation
    methods, register picker requests after acceptance, and preserve actual busy
    state while presenting an error.

- File: `twitter_downloader/gui/bookmark_folders.py`
  - Issue: Releasing the global busy state immediately erased the inline failure
    message, making a failed picker difficult to correct or retry.
  - Fix: Preserve inline errors across busy release; clear them on a new action
    or refreshed successful folder result. `show_error` accepts a keyword busy
    state so another accepted operation remains protected.

- File: `twitter_downloader/gui/personal_state_controller.py`
  - Issue: After a close timeout, the retained window could not reopen personal
    storage through retry. Clearing closing state before an in-flight initialize
    completed would instead advertise a stopping worker as writable.
  - Fix: Keep the old worker unavailable while it drains, then allow explicit
    retry to leave closing state only after that worker has stopped and create
    a new accepting worker. Accepted writes still finish before destruction.

## Findings (not fixed)

- Repository-wide Ruff has 30 existing diagnostics in seven unmodified files.
  The parent checked Ruff JSON against the changed-file set: none belong to this
  feature's 13 modified/new source files. These baseline issues were not expanded
  into this feature's scope.
- Static TypeCheck is not available: neither mypy nor pyright is configured or
  installed. No static type-check pass is claimed; compilation is not a substitute.
- Clean-machine installation and Linux media verification are not established by
  this same-machine Windows review. The parent owns the frozen-package check.

## Verification

- Lint: **PASS for all 13 modified/new Python source files**, Ruff 0.16.3.
  Full-repository baseline remains non-clean as described above.
- TypeCheck: **NOT RUN / unavailable**, verified with `importlib.util.find_spec`
  for mypy and pyright and inspection for project checker configuration.
- Maintained tests: **PASS**, `python -m unittest discover -s tests -v`, 14 tests.
- Existing controller assertion harness: **PASS**, rerun after final controller
  changes. Covers rapid targets, old hydration, multi-folder deletion barriers,
  queue bounds, initialization retry, rollback, and shutdown success/failure.
- Existing UI assertion harness: **PASS**. Covers model move/remove notifications,
  persistent indexes, no ordinary-mark reset/layout invalidation, exact hit areas,
  keyboard actions, picker validation/default protection, and detail state.
- Full native integration: **PASS**. Legacy import, likes, detail synchronization,
  folders A+B, deduplicated all-bookmarks, cancel no-op, removing A while keeping B,
  deleting B with default rehome, final-unlike empty state, per-scope filters,
  navigation/player reuse, rapid intent and persistence on shutdown.
- Reviewer regression harness: **PASS**, Windows platform asserted, real
  `QApplication.exec` plus bounded QTimer/event stages, no `processEvents` loop:
  - Ordinary failure rollback, visible/effective retry and pending-intent guard.
  - Read failure recovery and stale successful query rejection.
  - Fatal SQLite-code isolation and explicit reopen/retry.
  - Picker A failure does not clear accepted picker B save; busy protection and
    persistent inline error are checked before and after B commits.
  - H.264/AAC local playback produced seven valid native frames. Ordinary like
    preserved the same QMediaPlayer/source and advancing position. Removal paused
    dialog and fullscreen playback; restoring membership did not autoplay.
  - Accepted-write close timeout drains and can reopen explicitly; initialization
    close timeout does not expose the stopped worker as available.
- `git diff --check`: **PASS**, with only Git's existing LF/CRLF conversion notices.
- Existing maintained tests were preserved. New regression logic is a temporary
  function/assertion harness under ignored `.build-verification/`; no new unittest
  classes were retained.

## Final source identity

The final reviewer regression and controller checks cover these SHA-256 values:

| File | SHA-256 |
| --- | --- |
| `gui/personal_state_controller.py` | `D77CC6D3B102B4D794CB88B62E1E1537A3E4218D8647EB067422AA64D685E6D3` |
| `gui/library_page.py` | `3298C17626A09845D8C8F84687B893FAB98CAEEBA742E7AD69D8203DBC0C30F0` |
| `gui/bookmark_folders.py` | `FDDADC9D845471480507950C3E798684B01BA9890E3C9B61FA0E082E3BE0FE19` |

Paths in this table are relative to `twitter_downloader/`.

## Parent final verification

- Re-ran Ruff over all 13 final modified/new source files: PASS. AST syntax
  parsing, `git diff --check`, task context validation, legacy library-data check
  and CLI help also passed. The baseline Ruff JSON confirms all 30 unrelated
  findings are confined to seven untouched legacy files.
- Built an isolated PyInstaller 6.22.0 `onedir` verification app with an ignored
  runtime hook and isolated config/database/download fixtures. The build used
  the documented restricted PATH to avoid unrelated runtime DLL collection.
- Compared code objects for all 13 final source modules with embedded PYZ code:
  PASS, including the final close-timeout initialization guard.
- Frozen native Windows / Qt 6.11.1 event-loop verification: PASS. Two legacy
  image/video works loaded; two custom folders were created; like and A+B
  membership saved; all-bookmarks deduplicated; 11 valid H.264/AAC frames decoded;
  an unlike preserved player/source/advancing playback; final-member removal
  paused playback; after clean shutdown, reopening the personal database
  confirmed persisted marks and folders. The process exited with code zero.
- Captured and inspected the native frozen window for Chinese text, route/folder
  controls, dark theme, actions and page fit. Video validity is established by
  the live video-sink checks, not by the QWidget grab's black native-video area.
- Full instrumented run took 2.765 seconds after window initialization. This is
  smoke-test evidence on this development machine, not clean-machine installation
  or a performance guarantee. No release installer was produced or replaced.
- Storage/performance, compact/scaled UI and regression details are recorded in
  `prd.md`. No changes were committed or archived; human review remains next.

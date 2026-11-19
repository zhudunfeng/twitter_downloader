# Research: Portable Paths and Safe Startup Migration

- Query: Move application-owned persistent data from profile directories into a configurable software-relative data root without losing durable likes or folder memberships.
- Scope: mixed; repository-first, with narrow official SQLite/Python/PyInstaller references.
- Date: 2026-11-19

## Findings

### Files found and concrete consumers

- `twitter_downloader/gui/config.py:11-22`: single current definition of APPDATA-based config/catalog/personal paths and LOCALAPPDATA preview cache.
- `twitter_downloader/gui/config.py:31-64`: preference loading and atomic sibling temporary-file replacement. Keep save temporaries beside the destination.
- `twitter_downloader/gui/config.py:67-74`: default GUI output currently literal `downloads`, so CWD affects it.
- `twitter_downloader/gui/main.py:15-18`: imports storage constants by value. Changing constants at runtime will not safely update all consumers.
- `twitter_downloader/gui/main.py:105`: loads preferences when constructing the window.
- `twitter_downloader/gui/main.py:235-291`: existing settings form, appropriate place for a data-directory control.
- `twitter_downloader/gui/main.py:329-332`: passes catalog/cache/personal paths to the single library page.
- `twitter_downloader/gui/main.py:382`: persists output settings; normalize GUI-relative download paths consistently against software root.
- `twitter_downloader/gui/main.py:415`: cookie browser starts at the home folder; merely browsing home is not a persistent profile write.
- `twitter_downloader/gui/main.py:581-604`: creates APP_DATA_DIR and rotating log before the window. Migration/validation must happen before this log creation.
- `twitter_downloader/gui/main.py:516-524`: download history is session memory only, no history file to relocate.
- `twitter_downloader/library/personal_state.py:389-400`: existing SQLite backup API use; do not initialize this writable repository merely to read legacy files during migration.
- `twitter_downloader/library/repository.py:234-260`: catalog SQLite connection and WAL mode. A raw copy of `library.sqlite3` can miss committed WAL data.
- `twitter_downloader/gui/thumbnail_service.py:69-71`: preview temporary files already live in its passed cache directory.
- `twitter_downloader/downloader.py:193`: metadata temporary files already live in the destination work directory.
- `PACKAGING.md:19`: current claim that install directory never needs write permission becomes stale under the new portable default.
- `tests/test_gui.py:46`: maintained GUI test directly instantiates MainWindow; verify new startup initialization is isolatable and does not migrate real user data during tests.

### Minimal implementation boundary

1. Centralize resolution in a Qt-free storage module; preserve current public config path exports for consumers.
2. Frozen software root: `Path(sys.executable).resolve().parent`; source root: derive from this project's module location, not interpreter location or CWD.
3. Bootstrap lives at software-root `storage.json`, outside the selected data root to avoid circular lookup. Store selected `data_dir` and only the minimum migration source metadata. Resolve relative configured paths against software root.
4. Environment override has highest precedence, then bootstrap, then software-root `data`. Surface the winning source in settings; do not pretend a bootstrap change can override an active environment variable.
5. Resolve once per process. Directory changes persist only the next-start target. Do not mutate live worker paths, move open logs, or copy an active application's constantly changing files from a settings click.
6. Explicit startup preparation runs before log/window/library creation. Path/bootstrap/import failure must remain visible; preference JSON fallback must not swallow a failed personal-data migration and initialize a fresh empty store.
7. GUI downloads default to software-root `downloads`. Preserve explicitly configured output paths and original download roots; do not change CLI `--output` semantics or work identity.

### Safe migration mechanics

- Read source databases through SQLite's backup API, including catalog WAL content; use a read-only source URI and a newly staged target. Never use `immutable=1` as a shortcut for active WAL sources.
- Configure finite connect timeout and a backup progress deadline. `Connection.backup` retries in steps; connection timeout alone does not prove the whole backup finishes within a deadline.
- Stage the complete managed dataset in a unique sibling directory on the same filesystem, close all staged handles, validate completed copies, then publish with a directory rename. Preserve the prior target if it became nonempty; never replace an existing populated dataset.
- Copy known owned files only: preferences, personal/catalog SQLite snapshots and optional rotated desktop logs. Do not follow arbitrary symlink trees or recursively copy downloaded media. Preview cache may regenerate.
- Preserve source files. No personal-store corruption/future-schema reset. A failed backup must fail startup import clearly and clean only the agent-created staging directory.
- For an existing application-data dataset selected intentionally, use it as-is; do not merge current personal data into it. Unknown nonempty directories should be rejected rather than partly adopted.
- A target newly selected during the current process must inherit the final committed source state on next startup, after current personal writes drain. Record migration intent in bootstrap, rather than eagerly copying at settings-save time.
- Consider distinct source/target, ancestor/descendant paths, links/junctions, target races, read-only parent and unavailable drives before deleting any empty directory or publishing a stage.

### Read-only installs and alternatives

- Chosen approach: portable default plus bootstrap and environment override. Meets the request and supports a writable data directory even when the software directory cannot hold a new bootstrap.
- Alternative: configure only with an environment variable. Smaller, but fails the requested practical in-application configurability.
- Alternative: keep one locator in APPDATA. Avoids read-only software roots but contradicts the user's desired default and preserves unexplained C-drive state.
- Do not auto-fallback to APPDATA when a portable write fails. Explain the permission problem and show the environment/bootstrap remedy.

## External References and Runtime

- Local runtime inspected: Python 3.14.4, SQLite 3.50.4. `sqlite3.Connection.backup` exists; project already uses it for personal-data backup.
- Python sqlite3 reference, `Connection.backup`: supports incremental pages/progress/sleep and backup while another client accesses the source. The source and destination are separate connections. Official reference for the installed Python version: https://docs.python.org/3.14/library/sqlite3.html#sqlite3.Connection.backup . The API is already exercised by this project's local backup implementation.
- SQLite online backup background: https://sqlite.org/backup.html . The application needs a consistent database snapshot rather than independently copied live database/journal files.
- PyInstaller runtime documentation reference: https://pyinstaller.org/en/stable/runtime-information.html . Use the launched executable parent for user data, not the bundle/resource directory (`sys._MEIPASS`), which can be `_internal` or temporary extraction. This workspace's prior verified builder was 6.22.0; no upgrade is required for path resolution.

## Related Specs

- `.trellis/spec/backend/local-library.md`: root-qualified work identity; personal data cannot be reconstructed; catalog/cache are disposable; workers own their SQLite connections; shutdown drains accepted writes.
- `.trellis/spec/guides/code-reuse-thinking-guide.md`: keep path defaults and normalization in one source, update every consumer.
- `.trellis/spec/guides/cross-layer-thinking-guide.md`: settings -> bootstrap -> next-start resolution -> preparation -> config/log/database/cache consumers must agree.

## Suggested Verification

- Source/frozen roots with unrelated CWD, Unicode/spaces, bootstrap relative/absolute, empty/invalid JSON, non-string/null fields, environment precedence.
- Legacy config plus real likes and A+B membership import; compare source bytes after migration; verify no original downloads move.
- Keep an active WAL connection open, commit a row present only in WAL, migrate, reopen target and assert that row survives.
- Inject failed copy/backup and target publication races; ensure no half-dataset is usable and no existing files are overwritten.
- Parent permission failure, missing drive, unrelated populated target and nested/link paths; no silent profile fallback.
- Save target while a personal write remains pending, drain/shutdown, then initialize a fresh process and verify final state in the new directory.
- Native settings at 700px window width, long paths, browse/default/reset, active-environment explanation, restart notice and effective path labels.
- Run maintained 14 regressions with an isolated data environment. Assert startup creates owned files only inside the selected root; CLI default behavior remains unchanged.

## Caveats / Not Found

- CodeGraph symbol result for APP_DATA_DIR is stale relative to current config.py. Use indexed symbol locations for discovery and current source for exact edits.
- No retained history store or persistent data writers beyond the config/catalog/personal/log/preview/download paths were found in the scoped search.
- A directory rename is an atomic publication boundary only within its filesystem; staging must be adjacent to the target. This does not claim power-loss guarantees for every device/filesystem.
- OS/Python/Qt/browser/yt-dlp-managed temporary or cookie files are outside this app-persistent-data scope. Document this distinction without changing unrelated tool/runtime caches.
- Existing legacy C-drive files remain recovery copies; deleting them automatically is not authorized by this request.

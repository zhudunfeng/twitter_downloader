# Portable Configurable Application Data Directory

Date: 2026-11-19
Status: implementation authorized by the user's request.

## Goal

Default application-owned persistent data to the software directory instead of
the C-drive profile, and let users configure the location without losing existing
likes, bookmark folders, preferences or the library catalog.

## Requirements and chosen defaults

- Software directory means the executable's parent in a frozen build and the
  project root for source execution; it never follows an unrelated working directory.
- Default data root is `<software>/data`; config, personal.sqlite3, library.sqlite3,
  desktop.log and previews live beneath it. Default GUI download directory is
  `<software>/downloads`. Existing explicitly chosen downloads/roots stay intact.
- A small `<software>/storage.json` bootstrap file selects `data_dir`. Relative
  paths resolve against the software directory. Optional
  `TWITTER_DOWNLOADER_DATA_DIR` overrides the bootstrap for read-only installs;
  make its precedence explicit in UI and docs.
- Settings expose current/effective data path, browse/reset-to-default and a
  clear save/restart indication. Switching never changes live database paths.
- When an empty/new data root is first used, copy existing application data from
  the previously selected root, or legacy APPDATA on the first portable start.
  Preserve old originals. Copy SQLite with the backup API (including committed
  WAL content), not a raw live-main-file copy. Preview cache can be regenerated.
- Migrate a complete staged dataset; publish only after success. An interrupted
  or failed migration must not expose a half-copied personal/config dataset.
- Existing populated application-data roots are used without merging/overwriting.
  Reject unrelated nonempty targets to avoid unsafe implicit directory merging.
- Configuration/path failures are visible and actionable. Never silently fall
  back to C or reset personal data. Keep the active location until restart.
- All new methods/fields/configuration need Chinese explanations and types;
  key migration/save/error branches emit Chinese logs. No dependencies added.

## Plan

1. Inventory path consumers and record portable/SQLite migration decisions.
2. Centralize software/data/download paths and bootstrap parsing, startup import,
   safe directory-change persistence and configuration defaults.
3. Add settings controls and startup/error integration; preserve all other UX.
4. Verify path resolution, migration/rollback/SQLite WAL, UI restart boundaries,
   existing regression tests, documentation and changed-source lint.

## Acceptance Criteria

- [x] Fresh source and frozen starts resolve all owned persistent paths beneath
  software/data regardless of current working directory.
- [x] Bootstrap absolute/relative paths, environment override and GUI setting
  persist with documented precedence; invalid input never silently uses C.
- [x] Legacy personal data/config/catalog import without losing multi-folder
  relationships; originals remain; WAL-backed committed state is included.
- [x] New/empty custom roots inherit current data after restart; existing datasets
  are not overwritten; partial initialization is not accepted as success.
- [x] Settings reflect effective/pending paths, explain restart and environment
  override, validate errors, and remain usable at narrow window sizes.
- [x] Existing likes/bookmarks, media and CLI tests pass. New checks use temporary
  function assertions rather than retained generated unittest classes.
- [x] README/packaging/spec updated; no application-owned persistent writes to
  APPDATA/LOCALAPPDATA by default. OS/library-owned temporary files are outside
  this persistent-data request.

## Scope boundaries

- Do not move/delete original downloads, merge separate datasets, change CLI
  `--output` semantics or rewrite archived metadata.
- Do not delete legacy C-drive data automatically; it remains a recovery copy.
- OS-managed/Python/Qt/third-party temporary files and browser cookies are not
  portable application data and are not relocated by this feature.
- Source and read-only installed-directory behavior both need actionable errors;
  write permissions are not bypassed and no automatic C fallback is permitted.

## Context

- Prior local-likes-bookmarks task was committed by the user in `70e3523`; archived
  it as required before starting this task (archive helper created its own task
  metadata commit). All feature changes for this task remain uncommitted.
- CodeGraph resolves APP_DATA_DIR but its indexed snippet predates the current
  source. Use symbol results for discovery and exact scoped source for edits.
- Current path ownership is centralized in gui/config.py; main.py imports those
  constants and sets up the log. Download history is in-memory only.
- Current personal database is durable; catalog/cache rebuild rules and the
  single-writer/worker lifetime contracts remain mandatory.

## Research References

- `research/portable-paths.md` — repository/path and safe startup migration review.

## Completion

Implemented and verified on 2026-11-19. See `check-results.md` for reviewer and
final native/frozen verification, packaging collection checks and limitations.
No feature commit or archive was created; task is ready for user review/commit.

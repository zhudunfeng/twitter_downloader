# Portable Application Storage

## 1. Scope / Trigger

Applies to GUI configuration, catalog, durable likes/bookmarks, logs, previews
and GUI download defaults. Directory changes cross settings, startup, SQLite
workers and filesystem publication. Preserve the identities and worker lifetime
rules in [Local Library](./local-library.md). CLI `--output`, original archives,
browser cookies and OS/library-owned temporary files are outside this contract.

`gui/storage.py` owns path resolution and migration without importing Qt.
`gui/config.py` re-exports its public API for existing consumers. Module imports
may read selection metadata but **must not create directories, migrate or log to
files**. `load_config()` remains a read-only preference loader.

## 2. Signatures

```python
class StorageError(RuntimeError): ...

resolve_software_directory() -> Path
configured_data_directory() -> Path
configure_data_directory(path: str | Path) -> Path
prepare_storage() -> None
resolve_output_directory(value: str | Path) -> Path
```

Path constants: `SOFTWARE_DIR`, `DEFAULT_DATA_DIR`, `DEFAULT_OUTPUT_DIR`,
`STORAGE_CONFIG_PATH`, `APP_DATA_DIR`, `CACHE_DIR`, `CONFIG_PATH`,
`LIBRARY_DB_PATH`, `PERSONAL_DB_PATH`.

Bootstrap payload (written atomically, UTF-8):

```json
{
  "version": 1,
  "data_dir": "portable-data",
  "migrate_from": "E:/previous/data"
}
```

`data_dir` is required in an existing bootstrap; `version` defaults to 1 for
hand-written files. `migrate_from` is optional internal restart intent, written
when a GUI selection differs from the active process path. Do not put personal
state or download-root identity into this bootstrap.

## 3. Contracts

- Frozen root is `Path(sys.executable).resolve().parent`, never `_MEIPASS`.
  Source root is the repository/package root from `storage.py`, never CWD.
- Selection priority: `TWITTER_DOWNLOADER_DATA_DIR` environment key, then
  `<software>/storage.json` `data_dir`, then `<software>/data`. Relative paths
  resolve against the software root. An explicitly empty environment value is
  invalid, not equivalent to absence.
- An environment-selected existing dataset bypasses bootstrap parsing. An empty
  environment target still resolves a migration source, so a damaged bootstrap
  fails rather than silently creating empty personal data. A known existing
  portable default takes precedence over legacy profile data as an import source
  when no explicit previous source was recorded.
- Config, catalog, personal DB and rotating desktop log share `APP_DATA_DIR`;
  previews are `APP_DATA_DIR/previews`. GUI default downloads are
  `<software>/downloads`; preserve explicit existing output/root settings.
- `APP_DATA_DIR` and consumers are fixed for the process. GUI save validates and
  atomically records the **next startup** path. It never copies an active DB,
  swaps controller connections or changes a running download. Environment
  selection disables the three path controls and explains the override.
- `run()` calls `prepare_storage()` before file logging, window construction,
  catalogs, personal stores or preview workers. Buffer startup logs in memory,
  then flush into the selected root. Startup errors have a visible remedy and
  return nonzero; never fall back to a C-drive profile.
- Empty destinations inherit the selected previous source (legacy
  `%APPDATA%/TwitterDownloader`, or `~/.config/TwitterDownloader`, on the first
  portable run). Copy known preference/log files and SQLite snapshots only;
  previews regenerate. Do not move original archives, rewrite root keys or
  delete old copies.
- Use a unique **sibling stage on the destination filesystem**. SQLite source
  uses read-only `mode=ro` and `Connection.backup`, a total progress deadline and
  explicit connection closure; quick-check each staged DB. Publish the entire
  completed dataset by rename after writing `.storage-initialized.json`.
- Existing recognized datasets are used as-is without merge/overwrite. A marker
  allows an intentionally empty initialized dataset to remain empty on later
  starts, rather than resurrecting old profile data. Unknown nonempty targets
  are rejected. Explicit migration sources that disappeared must fail.
- Settings errors preserve the previous bootstrap and current active paths.
  Directory write probes and JSON temp files stay beside their target and are
  removed after use. Failure cleanup touches only the stage created by that
  attempt. Do not claim cross-process snapshots of several independent DBs are
  one SQLite transaction or promise device-independent power-loss durability.

## 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Missing bootstrap | Use portable default (or environment selection) |
| Malformed JSON, non-object, non-string data path, unsupported version | `StorageError`, visible startup failure; preserve files |
| Empty/control-character/URL/drive-relative path | Reject without changing bootstrap |
| File as directory, unavailable or unwritable parent | Actionable path error; no C fallback |
| Unknown nonempty target | Reject; do not merge with unrelated files |
| Existing dataset selected | Use it as-is, regardless of current dataset contents |
| Required previous source missing or unrecognizable | Fail instead of publishing an empty replacement |
| SQLite busy/backup deadline/corruption/I/O failure | Abort migration, keep originals, remove own unpublished stage |
| Source file permission/stat error | Propagate error; only missing files may be skipped, never interpret inaccessible personal data as absent |
| Target becomes populated before publication | Refuse replacement, preserve new occupant |
| Environment override active | Show source and disable GUI path mutation |
| Settings path saved while personal writes pending | Current worker finishes; next startup imports final committed state |

## 5. Good / Base / Bad Cases

- Base: source launched from another drive/CWD still uses project `data` and
  `downloads`; frozen app uses the EXE parent even with `_internal` resources.
- Good: legacy like plus A+B folder membership survives first portable startup;
  original source files remain. Save a new target, finish a later mark, close,
  restart: the later mark and all memberships appear in the new root.
- Good: an existing alternate dataset stays independent. An initialized empty
  dataset never reimports legacy likes on subsequent starts.
- Bad: a broken bootstrap or failed personal DB backup must not be hidden by a
  fresh default personal DB. Display the failure and preserve recovery copies.

## 6. Tests Required

Use isolated environment/profile paths **before importing config/main**, with
`PYTHONDONTWRITEBYTECODE=1`; never migrate the real user's profile in tests.
Temporary function/assertion harnesses belong in `.build-verification` and no
generated unittest classes remain after completion.

- Assert source/frozen root resolution independent of CWD and resource directory.
- Assert bootstrap absolute/relative path handling, environment priority, invalid
  values, no import-time writes and no profile fallback on permission errors.
- Use actual personal tables and two custom folders; assert marks, timestamps,
  folder IDs and relationships survive. Compare original files before/after.
- Keep a source WAL writer open with a committed row absent from the main file;
  assert backup contains it. Inject stage/copy/publication failures and assert
  no partial dataset is treated as initialized and no occupant is overwritten.
- Save target while active data can still change; restart in a fresh process,
  assert final committed state, config, log and catalog use the new root.
- Native Qt: reset/cancel are nonmutating; invalid selection keeps bootstrap;
  environment disables edits; 700x500 and normal settings remain readable and
  scroll vertically without horizontal overflow or a mismatched background.
- Installer collection is an allowlist: root `TwitterDownloader.exe` and the
  `_internal` tree only. Never recursively package a previously launched app
  root: default/custom user data, downloads, bootstrap and migration stages can
  be present there. Future root runtime resources require explicit entries.
- Maintained CLI/GUI regressions and changed-source Ruff must pass. Report
  unavailable static type checking separately from successful runtime checks.

## 7. Wrong vs Correct

Wrong: write `desktop.log` or create `PersonalStateRepository` before migration,
then copy `personal.sqlite3` by itself with `shutil.copy2`. This can turn an
empty target into an apparent dataset and lose WAL-committed state.

Correct: resolve once, `prepare_storage()`, snapshot source DBs read-only into
one staged dataset, publish, then open logs/window/controllers. A GUI directory
save only writes bootstrap intent; the active connections close normally before
the next startup migrates them.

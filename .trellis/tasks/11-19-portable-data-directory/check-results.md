# Portable Storage Quality Review

Date: 2026-11-19
Reviewer scope: `gui/storage.py`, `gui/config.py`, `gui/main.py`.

## Findings fixed

1. **Portable default data could be bypassed during relocation.** Environment
   selection without a bootstrap, or a hand-written bootstrap for a new empty
   target without `migrate_from`, previously selected legacy profile data even
   when `<software>/data` held the current personal records. Added a common
   fallback-source resolver that prefers the initialized portable default before
   the legacy profile. Explicit bootstrap migration sources retain priority and
   existing destination datasets remain untouched.
2. **Source access failures could masquerade as missing personal files.** Python
   3.14 `Path.exists/is_file/is_dir` may suppress filesystem access errors. The
   migration loop could consequently skip an unreadable personal database and
   publish an incomplete dataset. Dataset detection and source-file/directory
   inspection now use `stat`, ignore only genuine missing-path conditions, and
   expose permission/I/O failures as `StorageError`. Broken known-file symlinks
   are rejected. Staging cleanup and original-file preservation remain intact.
3. **Invalid GUI download paths escaped Qt slots.** Resolution ran outside
   `_persist_config`'s error handling and had no guard in `_start_download`.
   Saving now reports the existing Chinese save failure; starting an invalid
   path reports a Chinese status and creates no worker. Existing config files
   remain unchanged.
4. **Startup log handler cleanup (parent follow-up).** The parent identified
   that a success message could be written directly and then flushed a second
   time from the startup memory buffer. The parent is removing the buffer from
   the business logger immediately after its initial flush and revalidating the
   final startup/frozen build.

## Verification completed by reviewer

- Changed source Ruff check: **PASS** (storage/config/main).
- Changed source Ruff format check: **PASS** (3 files).
- AST parsing: **PASS** (3 files).
- `git diff --check`: **PASS**; Git emits only repository CRLF normalization
  notices.
- Maintained regression suite: **14/14 PASS**, with isolated environment and
  profile paths established before imports.
- Original portable-storage harness: **8 groups PASS**, including source/frozen
  path resolution, import without writes, bootstrap/environment validation,
  settings/restart boundaries, real likes and multiple folders, active committed
  WAL content, original preservation, existing targets, failure cleanup,
  publication race, missing sources, initialized-empty source, and lock deadline.
- Independent `.build-verification/portable-quality-check.py`: **4 groups PASS**:
  - Environment and hand-written bootstrap relocations inherit current portable
    default data rather than a stale legacy database; both sources remain intact.
  - Injected source-directory/personal-file/default-dataset permission errors
    abort instead of silently creating empty personal data; no target or stage
    remains and original data is readable afterward.
  - Invalid GUI output path is rejected on save/start/close without an uncaught
    slot exception or download worker.
  - A fresh-process invalid storage override returns startup error 1, displays
    the environment remedy, and writes neither portable fallback nor profile
    data.
- No new retained unittest classes or dependencies; temporary function/assertion
  scripts stay in the ignored verification directory.

## Limitations / parent follow-up at reviewer handoff

- **Static type checking unavailable:** neither mypy nor pyright is installed
  or configured. Runtime tests, typed signatures and AST checks do not constitute
  a static type-check pass.
- Full-repository Ruff has the previously documented 30 unrelated findings in
  seven unchanged legacy files. No unrelated lint rewrites were made.
- The parent owns final native Qt layout/restart checks and the frozen application
  rebuild/smoke verification after the source handoff.
- Installer integration needs to exclude generated root `data`, `downloads`,
  `storage.json` and migration stages from recursive packaging; otherwise a
  tested portable build can unintentionally bundle user/test data. This was
  sent to the parent because installer/docs were outside reviewer write scope.
- Documentation/spec updates are parent-owned. Required details communicated:
  default portable source precedes stale legacy data; source permission failures
  are fatal; an existing environment target can bypass broken bootstrap, but a
  new empty environment target needs a valid migration-source selection.

Source ownership was returned to the parent after the three reviewer fixes.
No feature commit or task archive was created.

## Final parent verification (2026-11-19)

All implementation follow-ups above are resolved:

- Startup memory logging is detached immediately after its initial flush. The
  final frozen app logs exactly once per launch (success counts 1 in the first
  dataset, 2 after its log is copied for the second launch).
- Installer `[Files]` now allowlists only root `TwitterDownloader.exe` and the
  complete `_internal` tree. This excludes all default/custom root user-data
  folders, downloads, bootstrap and staging without excluding data resources
  inside runtime libraries. Static collection assertions pass. README,
  PACKAGING and the seven-section portable-storage spec are synchronized.

Final checks against the handed-off source plus the log fix:

| Check | Result |
| --- | --- |
| Changed-source Ruff and format | PASS, 3 files |
| Maintained unittest suite | PASS, 14 tests |
| Portable storage function assertions | PASS, 8 groups |
| Independent quality function assertions | PASS, 4 groups |
| Native settings + fresh-process restart | PASS, reset/cancel/error/env/save boundaries and final A+B membership |
| Native settings layout | PASS, 1220x820 and 700x500; vertical scroll, no horizontal overflow, dark theme preserved |
| Existing personal feature native integration | PASS, legacy content, likes, detail, A+B, dedup, cancel, remove/rehome, navigation, no model reset and shutdown |
| Final full PyInstaller build | PASS, isolated runtime probe build using final source |
| Actual frozen EXE launch 1 | PASS, unrelated CWD, no data env override, EXE/data default, legacy import and late personal write |
| Actual frozen EXE launch 2 | PASS, bootstrap next-data, final mark and two-folder membership preserved, original dataset retained |
| Actual frozen EXE launch 3 | PASS, environment override, inherited bootstrap data, all original legacy hashes unchanged |
| Installer source allowlist assertion | PASS, root EXE + internal runtime only |
| Trellis context validation / git diff check | PASS |

Temporary evidence stays in ignored `.build-verification`, including
`portable-storage-check.py`, `portable-quality-check.py`, `portable-ui-check.py`,
`portable-settings-wide.png`, `portable-settings-narrow.png`, and
`portable-frozen-result.json`. The final frozen validation app is under
`.build-verification/portable-final/TwitterDownloader`; it contains a test-only
runtime hook and is not a user release. A spec rebuild initially created new
default build/dist outputs; no existing output directory was removed, and those
new generated files were moved back into this isolated verification directory
before the final EXE checks. No actual user profile/data was migrated by tests.

Remaining verification limits: no installed/configured mypy or pyright, no
installed Inno Setup compiler (installer compilation not run), and no separate
clean Windows machine. The documented 30 pre-existing whole-repository Ruff
findings are outside changed-source scope. No dependencies, generated unittest
classes, feature commit or task archive were added.

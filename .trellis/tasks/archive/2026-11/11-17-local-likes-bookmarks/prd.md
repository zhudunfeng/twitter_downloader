# Local Likes and Custom Bookmark Folders — Implementation Record

Date: 2026-11-17

Status: implemented and verified on 2026-11-17; awaiting human review and commit.

## Goal

Add independent local likes and user-defined bookmark folders to downloaded
tweets. A tweet may belong to several folders. Provide dedicated liked and
bookmarked timelines plus per-folder viewing while preserving the existing
Twitter-style reading, media, legacy compatibility and smooth browsing.

## Confirmed Requirements

- A downloaded tweet can be liked and bookmarked.
- Liked tweets and bookmarked tweets can each be viewed in their own list.
- Users can create their own bookmark folders.
- A single downloaded work may belong to multiple bookmark folders.
- Produce a concrete modification plan before implementation.

## Agreed Product Rules

- Likes/bookmarks are local personal states, independent of X account actions.
- The two states are independent; the same tweet can appear in both lists.
- Folder membership is many-to-many; the all-bookmarks view deduplicates works.
- Likes do not need folders. Bookmark folders are flat in the first version.
- State persists across restarts and ordinary catalog refresh/reimport.
- Existing downloaded works start unmarked; historic source like_count remains
  the saved public count rather than this user's local like state.
- Separate lists reuse the same timeline and reader/player rather than a new UI.
- The system creates a fixed-ID default folder that cannot be renamed/deleted.
  Custom folders have stable UUID IDs independent of their names.
- A work is bookmarked iff at least one folder membership exists. Do not maintain
  an independent bookmark boolean that can contradict membership.
- Custom folder names are trimmed, nonempty, limited to 40 characters and exclude
  control/newline characters. Apply one Unicode-aware normalization/duplicate-name
  rule in both UI and storage. Names are labels, not filesystem paths.
- Removing from folder A preserves membership in B and the like state. Removing
  the last membership cancels collection. An explicit 取消全部收藏 removes every
  membership for that work while preserving its like state.
- Deleting a custom folder never deletes original works/media. In the same
  transaction, move only works with no remaining membership into the default
  folder; works already in another folder retain their other memberships.
- Renaming a folder changes its label only. Likes have no folder organization.

## User Interface and Interaction

### Navigation and dedicated lists

- Expand primary navigation to **作品库 / 喜欢 / 收藏 / 下载 / 设置**.
- Reuse one LibraryPage, TimelineView, image reader and video player. Route all,
  liked, bookmarked and per-folder views as query scopes, not duplicated viewers.
- Inside 收藏, add a selector for 全部收藏, 默认收藏夹 and custom folders, plus
  管理收藏夹. Avoid unbounded folder entries in the fixed navigation rail.
- 全部收藏 returns each local work once even if it has many memberships. A
  selected folder shows only its members. Counts and empty states reflect the
  active scope and filters.
- Search, author/date/type filters and paging operate within the selected scope.
  Default ordering: liked by recent like time; all bookmarks by the start of the
  current collection episode; folder members by time added to that folder.
  Retain publication-newest/oldest alternatives and stable work-key tie breaks.
- Adding another folder does not change global collection-start time. Canceling
  every membership and later collecting again starts a new collection episode.
- Preserve each scope's session search/filter/order and stable-key/pixel reading
  anchor. Deleting the active folder switches to 全部收藏. Pause playback when
  navigation/removal hides the current work; never autoplay a neighbor.

### Per-work actions and folder picker

- Add heart and bookmark footer actions with outline/filled shapes, text/tooltips
  and focus feedback. Keep download-time public counts separately labeled; local
  likes must not increment `like_count`.
- Like is a one-click toggle. Bookmark opens a native multi-select folder picker.
  For a new collection, preselect the default folder; for an existing one,
  preselect every current membership. 保存 commits the complete desired set in
  one transaction; canceling the picker changes no memberships.
- Allow creating a folder from the picker; offer create/rename/delete in the
  manager. Stable IDs keep existing memberships valid after renaming.
- Empty picker selection does not silently erase all memberships. Disable normal
  save for an empty selection and offer a clearly named 取消全部收藏 action.
- A folder view offers 从【folder name】移除. Other views offer 整理收藏夹 and
  取消全部收藏 as applicable. Detail dialogs and context actions show the same state.
- Provide native buttons/checkboxes, accessible names and keyboard operation.
  Painted row actions have keyboard/context-menu alternatives; do not create
  permanent child widgets for every row or rely only on color/hover.
- Empty states distinguish no likes, no bookmarks, empty folder and no filter
  matches. A personal-database error is not an empty list.

## Repository Context Before Implementation

- Prior viewer work is committed as a1d0659 (feat: 增加内容查看功能).
- PySide6 Widgets shell; SQLite catalog; painted model/view timeline.
- CodeGraph is installed but queries for current viewer symbols return no
  results; scoped source reads are the fallback for this task.
- LibraryWork is immutable; its `like_count` is a source snapshot. LibraryQuery
  currently has no personal scope. Catalog schema 2 is explicitly rebuildable,
  with upsert/prune/corruption recovery that must not own personal-state lifetime.
- TimelineModel currently resets/appends records; add targeted state/removal
  notifications. Footer painting/hit-testing currently has no personal actions.
- LibraryPage already has query generations, pending-query coalescing, filters,
  anchor restoration and one player. MainWindow currently maps three numeric nav
  entries to three stacked pages: the new five routes need an explicit mapping.
- UI skill search yielded general selected-control/navigation semantics, not a
  PySide-specific template. Preserve the existing dark Twitter-style design.

## Storage Decision and Alternatives

1. Catalog columns/tables: small initial patch, but personal data would share a
   database explicitly designed to be disposable and automatically rebuilt.
2. Per-tweet JSON flags: changes archive originals and complicates permissions,
   atomic downloader publication and folder-wide updates.
3. **Recommended: separate personal.sqlite3** under the existing per-user app
   data directory, with independent versions, backup and recovery rules.

Catalog/previews remain rebuildable. Refreshing or deleting library.sqlite3 must
not delete personal.sqlite3. Original tweet.json, tweet.md and media stay intact.
README must distinguish personal data from caches and explain that deleting the
whole application data directory also removes personal data unless backed up.

### Personal schema, version 1

- `work_state(work_key PRIMARY KEY, liked_at NULL, collected_at NULL, revision)`:
  UTC integer timestamps plus stored revision. `collected_at` is ordering metadata,
  **not** a bookmark-status flag.
- `folders(id PRIMARY KEY, name, name_key UNIQUE, is_system, created_at,
  updated_at)`; default folder uses a fixed reserved ID.
- `folder_items(folder_id, work_key, added_at, PRIMARY KEY(folder_id,work_key))`:
  many-to-many memberships; index both folder-to-work and work-to-folder access.
- Folder references may cascade only inside the personal database. No foreign
  key/cascade to the disposable catalog. Scanner pruning never deletes personal
  rows or memberships.
- Like changes, replacing a folder set, deleting a folder with default fallback,
  and associated time/revision changes use short atomic transactions. Repeating
  the same desired state is idempotent and preserves timestamps.
- Membership transitions define collected_at: 0-to-1 sets it; 1-to-1 preserves it;
  1-to-0 clears it. Rehoming an orphan into default during folder deletion is
  1-to-1, preserving the collection episode. Like updates touch no memberships.
- Use one writer, finite lock waits/retries and a durable rollback-journal mode
  for personal data rather than blindly copying catalog WAL/NORMAL settings.
  Validate the bundled SQLite runtime/configuration during implementation; no
  dependency upgrade is needed. See research for details.

### Identity, reimport and missing originals

- First version uses existing root-qualified `work.key`: same-root/same-work-path
  refresh and reimport retain state. Directory fallback tweet IDs must not be
  treated as verified global platform IDs.
- Copies under different roots remain independent local works, including marks.
  Moving/renaming a root or work does not auto-migrate identity. An explicit
  relocation/merge workflow is deferred rather than guessing by text/hash.
- Removed works are hidden from readable-work lists while personal rows remain
  available for same-key reimport. Offline roots retain catalog rows under the
  existing scanner contract. Orphan marks are not counted as readable works.

### Query and recovery boundaries

- Apply personal scope/folder predicates in parameterized SQL **before paging**.
  Main query/count use shared predicates; EXISTS avoids duplicate all-bookmark
  rows from many folder memberships. Never fetch 80 then filter in Python.
- Catalog worker connections can attach personal data read-only after separate
  personal-store initialization. Bind file paths; schema names are constants.
  Batch-hydrate state for the page instead of per-row N+1 queries.
- Keep page/count/state reads short and consistently scoped; handle existing
  invalid-payload cleanup without breaking any explicit read-transaction boundary.
  Do not require atomic writes across attached catalog and personal databases.
- Isolate personal initialization/ATTACH failures from the existing catalog
  CORRUPT/NOTADB auto-rebuild catch. A damaged personal store must be preserved
  and reported, not silently replaced or mistaken for catalog damage.
- Personal schema upgrades back up/validate user data and migrate transactionally;
  a newer unsupported version fails clearly. BUSY/read-only/full-disk errors do
  not trigger destructive recovery. Main library reading should remain available
  with an explicit personal-state-unavailable warning.

## Contracts and Async Data Flow

- Add `PersonalWorkState` and `BookmarkFolder` immutable entities, separate from
  archived LibraryWork payload/signature. Personal roles must not invalidate
  media previews or cause personal values to be serialized into catalog archives.
- Extend LibraryQuery with scope `all|liked|bookmarked`, optional folder_id and
  context-valid sort options. Reject stale/deleted folder IDs explicitly.
- Personal repository operations: `set_liked(work_key, enabled)`,
  `set_folders(work_key, folder_ids)`, `create_folder(name)`,
  `rename_folder(folder_id, name)`, `delete_folder(folder_id)`. Return committed
  state/revision. Preserve unrelated fields and revalidate folder existence.
- One shared GUI controller serializes bounded writes and coalesces only pending
  latest intent by work/action. Folder edits submit the desired set, not a stream
  of independent checkbox writes. Folder CRUD uses the same ordered write queue.
- Like UI gives immediate pending feedback; commit confirms, failure restores
  last confirmed state with a Chinese error/retry path. Late results cannot
  overwrite newer intent. Query generations and personal-state revisions are
  separate so old queries cannot resurrect canceled state or stale memberships.
- Controller synchronizes rows, open detail dialogs, folder labels/counts and
  scope totals. Ordinary marks use targeted `dataChanged`; membership removal
  uses paired row notifications and key-based merging/refill to avoid offset
  skips/duplicates. Do not reset the whole model or rescan originals per click.
- Keep existing selection, expansion, anchors and media position where the work
  remains visible. When the anchor disappears, choose surviving successor then
  predecessor. A confirmed last-item removal keeps the current scope's empty state.
- Every connection stays in its owning worker; painting/model access does no
  SQL/disk/decode. On exit, finish accepted personal writes or explain unresolved
  writes; do not silently discard the last click or destroy a running QThread.

## File-Level Modification Map

| Files, relative to repository | Planned change |
| --- | --- |
| new `library/personal_state.py`, `library/models.py` | Personal schema/migrations, folder transactions/validation, state entities |
| `library/repository.py` | Personal read integration, scoped SQL/count, deduplicated paging/sorting |
| new `gui/personal_state_controller.py` | Single write queue, pending/confirmed revisions, shared state and shutdown |
| new `gui/bookmark_folders.py` | Multi-select picker and custom-folder CRUD manager |
| `gui/timeline_model.py`, `tweet_delegate.py`, `timeline_view.py` | State roles, footer hit areas, keyboard routes and local model updates |
| `gui/library_page.py`, `tweet_detail.py` | List/folder scopes, filters/empty states, synchronized actions and anchors |
| `gui/main.py`, `resources.py`, `config.py` | Explicit navigation mapping, icons/focus and personal database path |
| `README.md`, `.trellis/spec/backend/local-library.md` | Folder UX, personal-data/cache distinction, identity and recovery contracts |

Downloader/CLI APIs, metadata schema and output hierarchy stay compatible. Reuse
the existing media components. No new third-party dependency is planned.

## Implementation Sequence

1. Durable personal schema/default folder/transaction/migration contracts, then
   scoped query/count/unique pagination and old-work compatibility checks.
2. Shared controller, like actions, multi-folder picker and create/rename/delete
   manager, with footer/detail/context-menu synchronization.
3. Dedicated liked/bookmarked routes and per-folder selection; scope-aware search,
   sorting/counts, empty states, reading positions and player handling.
4. Regression, lock/corruption/race/shutdown checks, compact/high-DPI/keyboard and
   native-media checks, 10k-catalog/multi-membership measurements, documentation
   and packaging smoke check. Do not claim unmeasured latency/FPS guarantees.

## Out of Scope

- Sending likes/bookmarks to X, account login, cloud synchronization.
- Nested folders/tags, bulk actions, automatic media redownload.
- Online account operations and original-file relocation.
- Cross-root duplicate merging and automatic root/work relocation.
- Using logical bookmark folders to move original files on disk.

## Acceptance Criteria

- [x] Likes and bookmarks toggle independently in every relevant view.
- [x] Dedicated lists show only their corresponding marked tweets.
- [x] Marks survive restart, refresh and valid legacy reimport.
- [x] Existing public counts, original download files and CLI remain unchanged.
- [x] UI stays consistent and responsive on errors and rapid navigation.
- [x] A work can belong to A and B simultaneously; all bookmarks contains it once,
  while A and B each contain it once.
- [x] Removing from A leaves B; removing the final membership cancels collection.
- [x] Create/rename validation is consistent; renaming never breaks stable IDs.
- [x] Deleting a custom folder preserves files/likes, rehomes only orphans into
  default and preserves the global collection episode.
- [x] Picker cancel is a no-op; save is all-or-nothing; cancel-all is explicit.
- [x] Catalog/preview rebuild and same-key reimport retain personal data; removing
  works from the catalog does not prune personal history.
- [x] Search/date/author/type filters, count, folder membership and paging agree.
- [x] Like time, global collection time and per-folder added time follow the
  stated idempotent ordering rules.
- [x] Rapid clicks, stale queries, simultaneous list/detail actions and deleting
  a folder while its picker is open do not overwrite newer state or save half a set.
- [x] Personal BUSY/read-only/corrupt/future schema errors never silently reset
  user data or trigger unrelated catalog rebuilding.
- [x] Current-list last-item removal preserves scope, sensible focus/anchor and
  other folder memberships; ordinary marks do not restart media playback.
- [x] CLI/download/media regressions, keyboard, 100%/150%/200% scaling, compact
  layouts and safe draining of accepted personal writes are verified.

## Definition of Done and Verification Plan

- Use temporary function/assertion checks for schema/migrations, folder changes,
  rollback, duplicate memberships, paging/count, reimport and rebuild isolation.
  Follow the user's rule not to retain newly generated unittest classes.
- Run maintained regression tests, Ruff and any configured type checker;
  compilation/annotation checks do not substitute for a static type checker.
- Qt verification uses a real QApplication event loop: like/save/cancel/CRUD,
  row hit regions, list/detail synchronization, revision races, last-member
  removal, queue bounds, media continuity and safe close.
- Record 10k-work and many-membership performance separately from prior viewer
  measurements. No per-row SQL/N+1 or whole-root scanning on mark clicks.
- Update README/spec contracts and require normal human review before commit.
- Record actual implementation verification results before marking this task complete.

## Research References

- [Persistence and interactions](research/persistence-and-interactions.md):
  primary SQLite/Qt sources, independent-store alternatives, many-to-many
  membership, identity, query/transaction and worker/recovery boundaries.

The user approved this plan and implementation on 2026-11-17. The product rules
above are implementation requirements. Actual verification results and explicit
limits are recorded below and in `check-results.md` before completing the task.

## Implementation and Verification Results (2026-11-17)

- Implemented the independent personal SQLite store, many-to-many memberships,
  scoped SQL and hydration, serialized asynchronous controller, five-route
  navigation, likes, multi-folder picker/manager and synchronized detail actions.
- Preserved maintained downloader/CLI APIs and original-file layout. No new
  dependency was added. Original public counters remain archived snapshots.
- Storage assertion checks passed for schema/default folder, shared name
  validation, idempotent timestamps/revisions, transaction rollback, A+B
  membership, unique scope paging/count, deletion fallback, backup, same-key
  reimport, catalog rebuild independence and corrupt/future/missing-store
  isolation. Eight independent processes initialized concurrently and retained all 24 writes;
  a held external lock failed within the configured finite wait and rolled back.
- Maintained regression suite: 14 tests passed. Existing library-data verification
  and CLI `--help` passed. New verification uses ignored temporary function/assert
  harnesses; no new unittest classes or dependencies were retained.
- Native Windows `QApplication.exec()` integration with 105 legacy tweets passed
  create/save/cancel/delete, independent marks, A+B dedup, A-only removal,
  default fallback, final-row empty state, scopes/filters/navigation, rapid
  targets, no ordinary model reset and accepted-write shutdown persistence.
- Model/picker/keyboard/anchor checks passed, including persistent indexes,
  successor pixel restoration, 240/320/400/700-pixel lists and 150%/200% scaling.
  Native screenshots confirmed Chinese labels and the dark below-list area.
- Native media checks passed for JPEG zoom, missing/corrupt media, real H.264/AAC
  frames, replay and stop. Reviewer regression verified ordinary likes preserve
  player/source/advancing playback; removing the current work pauses dialog and
  fullscreen playback without autoplay when membership is restored.
- Reviewer fixed and rechecked retries, stale-query rejection, simultaneous
  picker failures, inline error retention and close-timeout recovery. See
  `check-results.md` for fault coverage and final source hashes.
- All 13 changed/new Python source files passed Ruff and AST parsing;
  `git diff --check` and task-context validation passed. Full-repository Ruff has
  30 pre-existing findings in seven untouched legacy files, confirmed against
  the changed-file set. Neither mypy nor pyright is configured/installed;
  static type checking was not run and is not implied by syntax verification.
- Isolated PyInstaller 6.22.0 `onedir` verification: PASS on this Windows machine.
  Compared all 13 changed/new source modules against their embedded PYZ code;
  all matched final source. Native Qt 6.11.1 run imported legacy image/video
  works, created A+B, liked and multi-collected, deduplicated all-bookmarks,
  produced 11 valid H.264/AAC video frames, preserved playback across unlike,
  paused after final membership removal, and reopened the personal store after
  shutdown to confirm persisted state. The instrumented hook/build and fixtures
  remain ignored under `.build-verification/`; no formal release installer or
  existing user package was replaced.

### Performance measurement

Python 3.14.4 / SQLite 3.50.4 on this Windows machine, 10,000 catalog works,
30,000 memberships, six warm runs. Median query of 80 works plus count and state
hydration: liked 62.58 ms, all bookmarks 62.99 ms, one folder 74.08 ms. State
hydration used two SELECTs for 80 works; deleting a 10,000-member folder took
626 ms. These are storage measurements, not rendered-FPS or latency guarantees
for other devices.

### Verification limits and handoff

- Clean-machine installation, Linux/macOS media, actual disk exhaustion and
  forced power-loss durability were not tested. Error paths used controlled
  faults, real external locking and deliberately malformed/future databases;
  they do not prove every filesystem/codec/device condition.
- Same-key reimport restores personal data; automatic root/work relocation and
  cross-root merging remain explicitly out of scope. Personal data is local;
  no likes/bookmarks are sent to X.
- README and local-library spec document product behavior, data lifetime,
  backup/recovery, identity and asynchronous API contracts.
- Code remains uncommitted for human review. Do not archive or record a committed
  session until the user has reviewed and committed the work.

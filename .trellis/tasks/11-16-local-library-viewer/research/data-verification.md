# Library Data Verification

Date: 2026-11-16

Scope: the Qt-independent library reader, SQLite repository, scanner, and the downloader's additive local-media manifest. This report does not establish GUI, decoder, deployment, or end-to-end performance acceptance.

## Files Implemented

- `twitter_downloader/library/__init__.py`
- `twitter_downloader/library/models.py`
- `twitter_downloader/library/reader.py`
- `twitter_downloader/library/repository.py`
- `twitter_downloader/library/scanner.py`
- `twitter_downloader/downloader.py`

The downloader keeps existing names and the `str` directory return contract. New JSON snapshots add `schema_version: 2`, per-media `filename`, zero-based `order`, and actual `size_bytes`. The media array retains source order while image/video filename counters remain independent. Markdown is published before the atomic final JSON replacement; completion is emitted afterwards.

## Checks Executed

All Python commands used `D:/adunsoftware/python/python3.14.4/python.exe`, `-B`, and `PYTHONDONTWRITEBYTECODE=1`. Existing tracked bytecode changes were preserved.

1. `-m unittest tests.test_download_flow -v`: **12 tests passed** after the downloader modifications.
2. `-m ruff check twitter_downloader/library twitter_downloader/downloader.py`: **passed**, after scoped fixes for import placement and sorted exports.
3. In-memory `compile(...)` for the five library modules plus `downloader.py`: **6 files compiled**, without writing bytecode.
4. `.build-verification/verify_library_data.py`: **all function assertions passed**. This ignored, temporary verification helper creates no test classes, uses temporary fixture directories, and cleans their contents automatically.

## Fixture Assertions

- Legacy `image, video, image` order maps to `<id>_1.jpg`, `<id>_video_1.mp4`, `<id>_2.jpg`.
- Chinese, English, emoji, paragraphs, article block order, and a zero saved counter survive reading.
- Invalid boolean/negative counters remain absent.
- Changed media bytes invalidate work and media signatures; deleted media remains represented as unavailable.
- Missing JSON fields and malformed JSON recover available Markdown author/date/text/article data.
- Windows-generated CRLF Markdown is normalized before parsing; this check exposed and fixed a trailing carriage-return defect.
- `tweet.txt` fallback works; directory account/date labels are not invented as source metadata.
- Fallback media use natural filename order, and incomplete ordering is reported.
- Parent traversal, drive-qualified paths, remote URLs, declared fragments, malformed media entries, and directory escape are rejected without breaking unrelated content.
- Repeated upserts and repeated imports of one root deduplicate; a separate copy under another root has a distinct key.
- Chinese/English substring search, literal `%`/`_`, author/type/date filtering, and pagination return expected works.
- Unknown publication dates keep their first-import sort fallback across content updates and repository reopen.
- A row inserted by a concurrent completion after scan start survives pruning.
- A malformed cached payload is removed from the rebuildable index so count/pagination can recover.
- A corrupt SQLite file is retained under a timestamped backup name before recreation; a future schema version is not silently downgraded.
- Cancelled scans and unavailable roots retain cached rows; a complete later scan prunes deleted works.
- Nested media assets of a manifest-backed work are not imported as additional works.
- Simulated local downloads produce a complete relative media manifest before `on_done`; a failed atomic replacement preserves the prior JSON and leaves no temporary metadata file.

## Limits and Handoff

- No network download, real MP4 decode, audio output, packaged executable, clean installation, or Linux runtime was exercised by these data checks.
- No 10,000-work latency or scrolling target was measured here.
- `mypy` was not installed and no repository type-check configuration was found; this report does not claim a static type-check pass. New methods have annotations and Chinese docstrings, and entity/configuration fields have Chinese explanations.
- The public UI data contract uses absolute local paths. At handoff, repository payloads also persisted absolute paths; the PRD's stronger root-plus-relative-catalog-path requirement was flagged to the root/check agent for serialization-boundary review. The original download manifest already stores relative `filename` values.
- Product-file ownership was handed back to the root/check agent after these checks. Later checker edits should rerun the affected checks.

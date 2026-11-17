# Bug Analysis: Foreign Windows ICU in Frozen Qt Build

## 1. Root Cause Category

- **D — Test Coverage Gap; E — Implicit Assumption.** Source Qt import, unit tests,
  and successful PyInstaller collection did not prove a frozen Qt import works.
- PyInstaller resolved `icuuc.dll` through an unrelated Poppler directory on the
  host PATH. Its ICU-78 symbols are versioned, but Qt6Core.dll requests unversioned
  Windows ICU functions such as `ucnv_open` and callback exports. PE inspection
  identified the missing exports and exact Analysis TOC source.

## 2. Why the Initial Fix Failed

1. The noconsole executable showed no probe result; bounded wait exposed only a
   timeout. A console bootloader revealed the QtCore DLL import exception.
2. Moving the verification probe after standard Qt runtime setup did not fix the
   import: runtime-hook order was not the cause of the incompatible binary.
3. Comparing original/bundled QtCore hashes and dependency exports revealed the
   foreign ICU. Clean analysis under an isolated PATH removed it and native
   console/noconsole playback passed.

## 3. Prevention Mechanisms

| Priority | Mechanism | Specific Action | Status |
| --- | --- | --- | --- |
| P0 | Build isolation | Limit local build PATH to selected Python and Windows, restore afterwards | Documented and verified |
| P0 | Runtime test | Start the actual frozen executable and decode a known local MP4 | Passed on this machine |
| P1 | Diagnosis | Inspect dependency sources/exports rather than copying arbitrary DLLs | Added to packaging and code-spec |
| P1 | Release acceptance | Repeat playback on a clean supported Windows machine | Remaining release check |

## 4. Systematic Expansion

- Similar-name native libraries from Conda, Poppler, media tools and other SDKs
  can be incompatible despite matching filenames. Preserve onedir trees and
  check the actual imported exports/source paths.
- Do not infer packaged multimedia support from external ffmpeg.exe or offscreen
  liveness. Source, frozen, and clean-machine verification are separate evidence.
- Test-only instrumentation must run after standard runtime initialization and
  must not be included by normal production build commands.

## 5. Knowledge Capture

- Updated `PACKAGING.md` and `.trellis/spec/backend/local-library.md`.
- This repository has no `src/templates/markdown/spec/` tree to synchronize.
- Spec changes remain uncommitted because project workflow assigns commits to the
  human user; no automatic commit or archive was performed.
